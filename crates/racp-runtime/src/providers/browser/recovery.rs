//! Replay only the protected context/target authority persisted before external effects.
use super::{
    cdp::{local_endpoint, Cdp},
    session::Browser,
};
use racp_contract::RacpError;
use serde::Deserialize;
use serde_json::json;
use std::{
    path::{Path, PathBuf},
    time::Duration,
};
use tokio_util::sync::CancellationToken;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    version: u8,
    agent_boot_id: String,
    device_id: String,
    endpoint: String,
    context_id: String,
    target_ids: Vec<String>,
}
fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
}
pub(super) fn markers(root: &Path) -> Result<Vec<PathBuf>, RacpError> {
    let mut markers = vec![];
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if !name.starts_with("browser_") || !identifier(&name) {
            continue;
        }
        racp_core::validate_local_path(&entry.path())?;
        let remote = entry.path().join("ownership.json");
        let marker = if remote.exists() {
            remote
        } else {
            entry.path().join("process.json")
        };
        if marker.exists() {
            racp_core::validate_local_path(&marker)?;
            markers.push(marker);
        }
        if markers.len() > 12 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
    }
    Ok(markers)
}
impl Browser {
    pub(super) fn recover(&self, markers: Vec<PathBuf>) {
        if markers.is_empty() {
            return;
        }
        let browser = self.clone();
        tokio::spawn(async move {
            for marker in markers {
                if browser.recover_marker(&marker).await.is_err() {
                    return;
                }
            }
            browser
                .ready
                .store(true, std::sync::atomic::Ordering::SeqCst);
            if let Ok(mut outbox) = browser.outbox.lock() {
                outbox.push(json!({"kind":"health","state":"available","handles":[],"capability":browser.capabilities_value()}),vec![]);
            }
        });
    }
    async fn recover_marker(&self, marker: &Path) -> Result<(), RacpError> {
        if marker
            .file_name()
            .is_some_and(|name| name == "process.json")
        {
            self.recover_process(marker).await?;
            return remove_profile(marker).await;
        }
        let record: Record =
            serde_json::from_slice(&racp_core::read_bounded(marker, 16 * 1024, true)?)?;
        if record.version != 1
            || record.device_id != self.device
            || !identifier(&record.agent_boot_id)
            || !identifier(&record.context_id)
            || record.target_ids.len() > 8
            || record.target_ids.iter().any(|t| !identifier(t))
        {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        let endpoint = local_endpoint(&record.endpoint)?;
        if !matches!(endpoint.scheme(), "ws" | "wss") {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        let work = async {
            let peer = Cdp::connect(endpoint.as_str()).await?;
            let result = cleanup_scope(&peer, &record.context_id, &record.target_ids).await;
            peer.close().await;
            result
        };
        tokio::time::timeout(Duration::from_secs(6), work)
            .await
            .map_err(|_| RacpError::new("CLEANUP_FAILED"))??;
        remove_profile(marker).await
    }
    async fn recover_process(&self, marker: &Path) -> Result<(), RacpError> {
        let record: ProcessRecord =
            serde_json::from_slice(&racp_core::read_bounded(marker, 4096, true)?)?;
        if record.version != 1
            || record.device_id != self.device
            || !identifier(&record.agent_boot_id)
            || record.pid == 0
            || record.pid > i32::MAX as u32
            || !record.create_time.is_finite()
            || record.create_time <= 0.0
            || record
                .job_name
                .as_ref()
                .is_some_and(|name| !name.strip_prefix("Local\\RACP_").is_some_and(identifier))
        {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        #[cfg(windows)]
        if record.job_name.is_none() {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        let pid = sysinfo::Pid::from_u32(record.pid);
        let mut system = sysinfo::System::new();
        loop {
            system.refresh_processes_specifics(
                sysinfo::ProcessesToUpdate::Some(&[pid]),
                true,
                sysinfo::ProcessRefreshKind::nothing(),
            );
            let running = system.process(pid).is_some_and(|process| {
                process.status() != sysinfo::ProcessStatus::Zombie
                    && process.status() != sysinfo::ProcessStatus::Dead
            });
            // A reused PID or a process whose identity cannot be read is ambiguous: retain its profile.
            if running {
                let birth = crate::identity::process_created(record.pid)?;
                if (birth - record.create_time).abs() > 0.000001 {
                    return Err(RacpError::new("CLEANUP_FAILED"));
                }
            }
            if !running && job_empty(record.job_name.as_deref())? {
                return Ok(());
            }
            if tokio::time::Instant::now() >= deadline {
                return Err(RacpError::new("CLEANUP_FAILED"));
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }
}
#[derive(serde::Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProcessRecord {
    version: u8,
    agent_boot_id: String,
    device_id: String,
    pid: u32,
    create_time: f64,
    job_name: Option<String>,
}
pub(super) fn persist_process(
    profile: &Path,
    device: &str,
    boot: &str,
    process: &super::super::containment::OwnedProcess,
) -> Result<(), RacpError> {
    let record = ProcessRecord {
        version: 1,
        agent_boot_id: boot.into(),
        device_id: device.into(),
        pid: process.pid(),
        create_time: crate::identity::process_created(process.pid())?,
        job_name: process.job_name().map(str::to_owned),
    };
    racp_core::atomic_write(
        &profile.join("process.json"),
        &serde_json::to_vec(&record)?,
        false,
    )
}
#[cfg(not(windows))]
fn job_empty(_name: Option<&str>) -> Result<bool, RacpError> {
    Ok(true)
}
#[cfg(windows)]
fn job_empty(name: Option<&str>) -> Result<bool, RacpError> {
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use windows_sys::Win32::{
        Foundation::*,
        System::{JobObjects::*, SystemServices::JOB_OBJECT_QUERY},
    };
    let name: Vec<u16> = name
        .ok_or_else(|| RacpError::new("CLEANUP_FAILED"))?
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let raw = unsafe { OpenJobObjectW(JOB_OBJECT_QUERY, 0, name.as_ptr()) };
    if raw.is_null() {
        return if unsafe { GetLastError() } == ERROR_FILE_NOT_FOUND {
            Ok(true)
        } else {
            Err(RacpError::new("CLEANUP_FAILED"))
        };
    }
    let job = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
    if unsafe {
        QueryInformationJobObject(
            job.as_raw_handle(),
            JobObjectBasicAccountingInformation,
            (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
            std::mem::size_of_val(&info) as u32,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(RacpError::new("CLEANUP_FAILED"));
    }
    Ok(info.ActiveProcesses == 0)
}
async fn remove_profile(marker: &Path) -> Result<(), RacpError> {
    let profile = marker
        .parent()
        .ok_or_else(|| RacpError::new("CLEANUP_FAILED"))?;
    racp_core::validate_local_path(profile)?;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    loop {
        match std::fs::remove_dir_all(profile) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(_) if tokio::time::Instant::now() >= deadline => {
                return Err(RacpError::new("CLEANUP_FAILED"))
            }
            Err(_) => tokio::time::sleep(Duration::from_millis(20)).await,
        }
    }
}
pub(super) async fn cleanup_scope(
    peer: &Cdp,
    context: &str,
    targets: &[String],
) -> Result<(), RacpError> {
    let cancel = CancellationToken::new();
    let contexts = peer
        .call("Target.getBrowserContexts", json!({}), None, &cancel)
        .await?;
    if contexts["browserContextIds"]
        .as_array()
        .is_some_and(|c| c.iter().any(|v| v == context))
    {
        peer.call(
            "Target.disposeBrowserContext",
            json!({"browserContextId":context}),
            None,
            &cancel,
        )
        .await?;
    }
    let current = peer
        .call("Target.getTargets", json!({}), None, &cancel)
        .await?;
    for target in targets {
        if current["targetInfos"]
            .as_array()
            .is_some_and(|items| items.iter().any(|p| p["targetId"] == *target))
        {
            peer.call(
                "Target.closeTarget",
                json!({"targetId":target}),
                None,
                &cancel,
            )
            .await?;
        }
    }
    loop {
        let current = peer
            .call("Target.getTargets", json!({}), None, &cancel)
            .await?;
        let active = current["targetInfos"]
            .as_array()
            .ok_or_else(|| RacpError::new("CLEANUP_FAILED"))?;
        if !active.iter().any(|t| {
            t["browserContextId"] == context || targets.iter().any(|id| t["targetId"] == *id)
        }) {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}
