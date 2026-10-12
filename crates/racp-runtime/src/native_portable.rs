//! Native-only self extraction. No legacy footer, WebView, runtime discovery or state import.
use racp_contract::{distribution::*, local_ipc::reject_duplicate_keys, RacpError};
use racp_core::{distribution::pin_payload, InstanceLock, Workspaces};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    path::PathBuf,
};
fn invalid() -> RacpError {
    RacpError::new("PACKAGE_INVALID")
}
pub fn report_failure() {
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
        let text:Vec<u16>="RACP native 포터블을 시작하지 못했습니다. 파일 무결성과 상태 경로를 확인하세요. 사용자 데이터는 보존됩니다.".encode_utf16().chain(Some(0)).collect();
        let title: Vec<u16> = "RACP Native Client".encode_utf16().chain(Some(0)).collect();
        MessageBoxW(
            std::ptr::null_mut(),
            text.as_ptr(),
            title.as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}
fn hash_region(file: &mut File, size: u64) -> Result<String, RacpError> {
    let mut hash = Sha256::new();
    let mut left = size;
    let mut buffer = [0u8; 65536];
    while left > 0 {
        let limit = left.min(buffer.len() as u64) as usize;
        let n = file.read(&mut buffer[..limit])?;
        if n == 0 {
            return Err(invalid());
        }
        hash.update(&buffer[..n]);
        left -= n as u64;
    }
    Ok(format!("{:x}", hash.finalize()))
}
pub fn launch() -> Result<(), RacpError> {
    if !cfg!(all(windows, target_arch = "x86_64")) {
        return Err(invalid());
    }
    if std::env::args_os().len() != 1 {
        return Err(invalid());
    }
    let exe = std::env::current_exe()?;
    let source_guard = Workspaces::new(exe.parent().ok_or_else(invalid)?, &[])?;
    let source_dir = source_guard.directory("default", exe.parent().ok_or_else(invalid)?)?;
    let mut input = source_dir.open_pinned_read(
        exe.file_name()
            .and_then(|v| v.to_str())
            .ok_or_else(invalid)?,
    )?;
    let length = input.metadata()?.len();
    if length < 88 {
        return Err(invalid());
    }
    input.seek(SeekFrom::End(-88))?;
    let mut tail = [0u8; 88];
    input.read_exact(&mut tail)?;
    if &tail[8..24] != PORTABLE_MAGIC {
        return Err(invalid());
    }
    let offset = u64::from_le_bytes(tail[..8].try_into().map_err(|_| invalid())?);
    if offset < 1024
        || offset > 128 * 1024 * 1024
        || offset >= length - 88
        || length - offset > MAX_PAYLOAD_BYTES + 1024 * 1024
    {
        return Err(invalid());
    }
    let digest = std::str::from_utf8(&tail[24..]).map_err(|_| invalid())?;
    if !digest
        .bytes()
        .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    {
        return Err(invalid());
    }
    input.seek(SeekFrom::Start(offset))?;
    if hash_region(&mut input, length - offset - 88)? != digest {
        return Err(RacpError::new("PACKAGE_HASH_MISMATCH"));
    }
    input.seek(SeekFrom::Start(offset))?;
    let mut prefix = [0u8; 4];
    input.read_exact(&mut prefix)?;
    let count = u32::from_le_bytes(prefix) as usize;
    if count == 0 || count > 1024 * 1024 || offset + 4 + count as u64 > length - 88 {
        return Err(invalid());
    }
    let mut bytes = vec![0u8; count];
    input.read_exact(&mut bytes)?;
    reject_duplicate_keys(&bytes)?;
    let manifest: NativePayload = serde_json::from_slice(&bytes)?;
    manifest.validate()?;
    let payload_size = manifest.files.iter().try_fold(0u64, |size, file| {
        size.checked_add(file.size_bytes).ok_or_else(invalid)
    })?;
    if offset + 4 + count as u64 + payload_size != length - 88 {
        return Err(invalid());
    }
    let local = PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or_else(invalid)?).join("RACP");
    let cache_parent = local.join("native-portable-cache");
    racp_core::create_private_native_directory(&cache_parent)?;
    let cache_admission = InstanceLock::acquire(&cache_parent.join("admission.lock"))?;
    let cache = cache_parent.join(digest);
    racp_core::create_private_native_directory(&cache)?;
    let _lock = InstanceLock::acquire(&cache.join("extract.lock"))?;
    let root = cache.join("payload");
    if !root.try_exists()? {
        let guards = Workspaces::new(&cache_parent, &[])?;
        let mut pending = vec![(cache_parent.clone(), 0u8)];
        let mut total = 0u64;
        let mut entries = 0;
        while let Some((path, depth)) = pending.pop() {
            if depth > 8 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let dir = guards.directory("default", &path)?;
            for name in dir.names()? {
                entries += 1;
                if entries > 4096 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                }
                let info = dir.info(&name)?.ok_or_else(invalid)?;
                if info.link {
                    return Err(invalid());
                }
                if info.directory {
                    pending.push((path.join(&name), depth + 1));
                } else {
                    total = total.checked_add(info.size).ok_or_else(invalid)?;
                }
            }
        }
        if total
            .checked_add(payload_size)
            .and_then(|size| size.checked_add(count as u64))
            .is_none_or(|size| size > 2 * MAX_PAYLOAD_BYTES)
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        // Failed candidates are kept inactive, bounded, and never retried in-place.
        let candidates = std::fs::read_dir(&cache)?
            .filter_map(Result::ok)
            .filter(|e| e.file_name().to_string_lossy().starts_with("candidate_"))
            .count();
        if candidates >= 4 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let candidate = cache.join(racp_contract::new_id("candidate"));
        racp_core::create_private_native_directory(&candidate)?;
        let guard = Workspaces::new(&candidate, &[])?;
        let dir = guard.directory("default", &candidate)?;
        for entry in &manifest.files {
            let mut output = dir.create(&entry.path)?;
            let mut left = entry.size_bytes;
            let mut buffer = [0u8; 65536];
            let mut hash = Sha256::new();
            while left > 0 {
                let limit = left.min(buffer.len() as u64) as usize;
                let n = input.read(&mut buffer[..limit])?;
                if n == 0 {
                    return Err(invalid());
                }
                output.write_all(&buffer[..n])?;
                hash.update(&buffer[..n]);
                left -= n as u64;
            }
            output.sync_all()?;
            if format!("{:x}", hash.finalize()) != entry.sha256 {
                return Err(RacpError::new("PACKAGE_HASH_MISMATCH"));
            }
        }
        let mut metadata = dir.create(MANIFEST_FILE)?;
        metadata.write_all(&bytes)?;
        metadata.sync_all()?;
        drop(metadata);
        let pins = pin_payload(&candidate)?;
        drop(pins);
        drop(dir);
        drop(guard);
        racp_core::validate_local_path(&candidate)?;
        racp_core::validate_local_path(&root)?;
        if candidate.parent() != Some(cache.as_path()) || root.parent() != Some(cache.as_path()) {
            return Err(invalid());
        }
        std::fs::rename(&candidate, &root)?;
    }
    let pins = pin_payload(&root)?;
    if serde_json::to_value(&pins.manifest)? != serde_json::to_value(&manifest)? {
        return Err(invalid());
    }
    let state_parent = local.join("native-portable-state");
    racp_core::create_private_native_directory(&state_parent)?;
    // State remains stable for this explicit launcher location across native upgrades.
    let profile = state_parent.join(racp_contract::digest(exe.to_string_lossy().to_lowercase()));
    drop(_lock);
    drop(cache_admission);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let mut child = std::process::Command::new(root.join("racp-client.exe"))
            .arg("--portable-state")
            .arg(profile)
            .current_dir(&root)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(0x08000000)
            .spawn()?;
        // Keep immutable native payload pins until the GUI has drained its Agent.
        let status = child.wait()?;
        if !status.success() {
            return Err(RacpError::new("GUI_START_FAILED"));
        }
    }
    drop(pins);
    Ok(())
}
