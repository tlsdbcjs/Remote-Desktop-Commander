//! Read-only observations from one pinned Windows process handle. No debug privilege or write access.
use racp_contract::RacpError;
use serde_json::Value;
use std::path::Path;
use tokio_util::sync::CancellationToken;
pub(super) fn collect(
    request: &Value,
    spool: &Path,
    cancel: &CancellationToken,
) -> Result<Value, RacpError> {
    #[cfg(all(windows, target_arch = "x86_64"))]
    {
        windows::collect(request, spool, cancel)
    }
    #[cfg(not(all(windows, target_arch = "x86_64")))]
    {
        let _ = (request, spool, cancel);
        Err(RacpError::new("CAPABILITY_UNAVAILABLE"))
    }
}
#[cfg(all(windows, target_arch = "x86_64"))]
mod windows {
    use super::*;
    use racp_contract::timestamp;
    use serde_json::json;
    use sha2::{Digest, Sha256};
    use std::{
        io::Write,
        os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
        time::{Duration, Instant},
    };
    use windows_sys::Win32::{
        Foundation::*,
        System::{Diagnostics::Debug::ReadProcessMemory, Memory::*, Threading::*},
    };
    struct Target(OwnedHandle);
    impl Target {
        fn open(pid: u32, expected: f64) -> Result<Self, RacpError> {
            let raw = unsafe {
                OpenProcess(
                    PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_SYNCHRONIZE,
                    0,
                    pid,
                )
            };
            if raw.is_null() {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            let target = Self(unsafe { OwnedHandle::from_raw_handle(raw) });
            if crate::identity::token_sid(raw)?
                != crate::identity::token_sid(unsafe { GetCurrentProcess() })?
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            target.alive()?;
            let mut created = FILETIME::default();
            let mut exited = created;
            let mut kernel = created;
            let mut user = created;
            if unsafe { GetProcessTimes(raw, &mut created, &mut exited, &mut kernel, &mut user) }
                == 0
            {
                return Err(RacpError::new("PROCESS_NOT_FOUND"));
            }
            let ticks = ((created.dwHighDateTime as u64) << 32) | created.dwLowDateTime as u64;
            let birth = (ticks / 10_000_000) as f64 + (ticks % 10_000_000) as f64 / 10_000_000.0
                - 11_644_473_600.0;
            if (birth - expected).abs() > 0.000001 {
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            target.alive()?;
            Ok(target)
        }
        fn alive(&self) -> Result<(), RacpError> {
            if unsafe { WaitForSingleObject(self.0.as_raw_handle(), 0) } != WAIT_TIMEOUT {
                return Err(RacpError::new("PROCESS_NOT_FOUND"));
            }
            Ok(())
        }
        fn region(&self, address: u64) -> Result<Option<Value>, RacpError> {
            self.alive()?;
            let mut info = MEMORY_BASIC_INFORMATION::default();
            let length = unsafe {
                VirtualQueryEx(
                    self.0.as_raw_handle(),
                    address as usize as *const _,
                    &mut info,
                    std::mem::size_of_val(&info),
                )
            };
            if length == 0 && unsafe { GetLastError() } == ERROR_INVALID_PARAMETER {
                return Ok(None);
            }
            if length != std::mem::size_of_val(&info) || info.RegionSize == 0 {
                return Err(RacpError::new("MEMORY_UNAVAILABLE"));
            }
            self.alive()?;
            let readable = info.State == MEM_COMMIT
                && info.Protect & PAGE_GUARD == 0
                && matches!(
                    info.Protect & 0xff,
                    PAGE_READONLY
                        | PAGE_READWRITE
                        | PAGE_WRITECOPY
                        | PAGE_EXECUTE_READ
                        | PAGE_EXECUTE_READWRITE
                        | PAGE_EXECUTE_WRITECOPY
                );
            Ok(Some(
                json!({"base_address":format!("0x{:x}",info.BaseAddress as usize),"size_bytes":info.RegionSize,"allocation_base":format!("0x{:x}",info.AllocationBase as usize),"state":info.State,"protection":info.Protect,"type":info.Type,"readable":readable}),
            ))
        }
        fn read(&self, address: u64, size: usize) -> Result<Vec<u8>, RacpError> {
            self.alive()?;
            let mut bytes = vec![0; size];
            let mut count = 0;
            if unsafe {
                ReadProcessMemory(
                    self.0.as_raw_handle(),
                    address as usize as *const _,
                    bytes.as_mut_ptr().cast(),
                    size,
                    &mut count,
                )
            } == 0
                || count != size
            {
                return Err(RacpError::new("MEMORY_UNAVAILABLE"));
            }
            self.alive()?;
            Ok(bytes)
        }
    }
    fn address(value: &Value) -> Result<u64, RacpError> {
        value
            .as_str()
            .and_then(|value| value.strip_prefix("0x"))
            .and_then(|raw| u64::from_str_radix(raw, 16).ok())
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))
    }
    pub(super) fn collect(
        request: &Value,
        spool: &Path,
        cancel: &CancellationToken,
    ) -> Result<Value, RacpError> {
        let deadline = Instant::now()
            + Duration::from_millis(request["remaining_timeout_ms"].as_u64().unwrap_or(1));
        let check = || {
            if cancel.is_cancelled() {
                Err(RacpError::new("CANCELLED"))
            } else if Instant::now() >= deadline {
                Err(RacpError::new("TIMEOUT"))
            } else {
                Ok(())
            }
        };
        check()?;
        let p = &request["payload"];
        let target = Target::open(
            p["pid"].as_u64().unwrap_or(0) as u32,
            p["create_time"].as_f64().unwrap_or(0.0),
        )?;
        let mut result = json!({"pid":p["pid"],"create_time":p["create_time"],"agent_boot_id":p["agent_boot_id"],"observed_at":timestamp(),"consistency":"live_process_observation","atomic_snapshot":false});
        if request["operation"] == "process.memory_regions" {
            let mut current = address(&p["start_address"])?;
            let mut items = vec![];
            let mut finished = false;
            let limit = p["limit"]
                .as_u64()
                .filter(|limit| (1..=1024).contains(limit))
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            for _ in 0..limit {
                check()?;
                let Some(region) = target.region(current)? else {
                    finished = true;
                    break;
                };
                let Some(next) = address(&region["base_address"])?
                    .checked_add(region["size_bytes"].as_u64().unwrap_or(0))
                    .filter(|next| *next > current)
                else {
                    finished = true;
                    break;
                };
                items.push(region);
                current = next;
            }
            check()?;
            target.alive()?;
            result["items"] = json!(items);
            result["next_address"] = if finished {
                Value::Null
            } else {
                json!(format!("0x{current:x}"))
            };
            return Ok(result);
        }
        let start = address(&p["address"])?;
        let size = p["size_bytes"]
            .as_u64()
            .filter(|size| (1..=16 * 1024 * 1024).contains(size))
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        start
            .checked_add(size)
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        result["address"] = json!(format!("0x{start:x}"));
        result["size_bytes"] = json!(size);
        if size <= 4096 {
            let bytes = target.read(start, size as usize)?;
            check()?;
            result["bytes_hex"] = json!(bytes
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>());
            result["sha256"] = json!(format!("{:x}", Sha256::digest(&bytes)));
            return Ok(result);
        }
        let op = request["operation_id"]
            .as_str()
            .filter(|id| {
                id.starts_with("op_")
                    && id.len() <= 96
                    && id
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
            })
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let path = spool.join(format!("{op}.process-memory"));
        let mut output = racp_core::secure_create_file(&path)?;
        let mut digest = Sha256::new();
        let copied = (|| {
            let mut offset = 0;
            while offset < size {
                check()?;
                let bytes =
                    target.read(start + offset, (size - offset).min(256 * 1024) as usize)?;
                output.write_all(&bytes)?;
                digest.update(&bytes);
                offset += bytes.len() as u64;
            }
            output.sync_all()?;
            check()?;
            target.alive()?;
            Ok::<(), RacpError>(())
        })();
        drop(output);
        if let Err(error) = copied {
            let _ = std::fs::remove_file(&path);
            return Err(error);
        }
        result["sha256"] = json!(format!("{:x}", digest.finalize()));
        result["artifact_id"] = Value::Null;
        result["spool_path"] = json!(path);
        result["artifact_media_type"] = json!("application/octet-stream");
        Ok(result)
    }
}
