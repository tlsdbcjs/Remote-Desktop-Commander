//! Self-extracting native launcher. Payload hashes are checked on every launch.
use racp_contract::RacpError;
use racp_core::{InstanceLock, Workspaces};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    path::PathBuf,
};
const MAGIC: &[u8; 16] = b"RACPPORTABLE0001";
fn failure() -> RacpError {
    RacpError::new("RUNTIME_UNAVAILABLE")
}
fn hash(file: &mut File, size: u64) -> Result<String, RacpError> {
    let mut remaining = size;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 65536];
    while remaining > 0 {
        let n = file.read(&mut buffer[..remaining.min(65536) as usize])?;
        if n == 0 {
            return Err(failure());
        }
        digest.update(&buffer[..n]);
        remaining -= n as u64;
    }
    Ok(format!("{:x}", digest.finalize()))
}
pub fn launch() -> Result<(), RacpError> {
    if !cfg!(windows) || !cfg!(target_arch = "x86_64") {
        return Err(failure());
    }
    let exe = std::env::current_exe()?;
    racp_core::validate_local_path(&exe)?;
    let mut input = racp_core::secure_read_file(&exe)?;
    let length = input.metadata()?.len();
    if length < 88 {
        return Err(failure());
    }
    input.seek(SeekFrom::End(-88))?;
    let mut tail = [0u8; 88];
    input.read_exact(&mut tail)?;
    if &tail[8..24] != MAGIC {
        return Err(failure());
    }
    let offset = u64::from_le_bytes(tail[..8].try_into().map_err(|_| failure())?);
    if offset < 1024 || offset >= length - 88 || length - offset > 2 * 1024 * 1024 * 1024 {
        return Err(failure());
    }
    let expected = std::str::from_utf8(&tail[24..]).map_err(|_| failure())?;
    if !expected
        .bytes()
        .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err(failure());
    }
    input.seek(SeekFrom::Start(offset))?;
    if hash(&mut input, length - offset - 88)? != expected {
        return Err(failure());
    }
    input.seek(SeekFrom::Start(offset))?;
    let mut prefix = [0u8; 4];
    input.read_exact(&mut prefix)?;
    let size = u32::from_le_bytes(prefix) as usize;
    if size == 0 || size > 4 * 1024 * 1024 {
        return Err(failure());
    }
    let mut bytes = vec![0u8; size];
    input.read_exact(&mut bytes)?;
    let manifest: Value = serde_json::from_slice(&bytes)?;
    if manifest["version"] != 1
        || manifest["platform"] != "win"
        || manifest["architecture"] != "x64"
    {
        return Err(failure());
    }
    let files = manifest["files"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 20000)
        .ok_or_else(failure)?;
    let local = PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or_else(failure)?).join("RACP");
    racp_core::private_dir(&local)?;
    let cache = local.join("portable").join(expected);
    racp_core::private_dir(&cache)?;
    let _lock = InstanceLock::acquire(&cache.join("extract.lock"))?;
    let root = cache.join("payload");
    racp_core::private_dir(&root)?;
    let roots = Workspaces::new(&root, &[])?;
    let mut names = std::collections::BTreeSet::new();
    let data = offset + 4 + size as u64;
    let mut consumed = data;
    for row in files {
        let name = row["path"].as_str().ok_or_else(failure)?;
        if name.is_empty()
            || name.contains(['\\', ':', '\0'])
            || name
                .split('/')
                .any(|p| p.is_empty() || p == "." || p == ".." || p.ends_with(['.', ' ']))
            || !names.insert(name.to_lowercase())
        {
            return Err(failure());
        }
        let size = row["size"]
            .as_u64()
            .filter(|v| *v <= 512 * 1024 * 1024)
            .ok_or_else(failure)?;
        let wanted = row["sha256"]
            .as_str()
            .filter(|v| v.len() == 64)
            .ok_or_else(failure)?;
        consumed = consumed
            .checked_add(size)
            .filter(|v| *v <= length - 88)
            .ok_or_else(failure)?;
        let path = root.join(name);
        racp_core::validate_local_path(&path)?;
        let parent = path.parent().ok_or_else(failure)?;
        racp_core::private_dir(parent)?;
        let dir = roots.directory("default", parent)?;
        let leaf = path
            .file_name()
            .and_then(|v| v.to_str())
            .ok_or_else(failure)?;
        if let Some(info) = dir.info(leaf)? {
            if info.link || info.directory || info.size != size {
                return Err(failure());
            }
            let mut existing = dir.open_read(leaf)?;
            if hash(&mut existing, size)? != wanted {
                return Err(failure());
            }
            input.seek(SeekFrom::Current(size as i64))?;
        } else {
            let mut output = dir.create(leaf)?;
            let mut remaining = size;
            let mut buffer = [0u8; 65536];
            let mut digest = Sha256::new();
            while remaining > 0 {
                let n = input.read(&mut buffer[..remaining.min(65536) as usize])?;
                if n == 0 {
                    return Err(failure());
                }
                output.write_all(&buffer[..n])?;
                digest.update(&buffer[..n]);
                remaining -= n as u64;
            }
            output.sync_all()?;
            drop(output);
            if format!("{:x}", digest.finalize()) != wanted {
                return Err(failure());
            }
        }
    }
    if consumed != length - 88
        || !names.contains("racp-client.exe")
        || !names.contains("agent/racp-agent.exe")
        || !names.contains("webview2/msedgewebview2.exe")
    {
        return Err(failure());
    }
    let mut pending = vec![(root.clone(), 0u8)];
    let mut count = 0usize;
    while let Some((path, depth)) = pending.pop() {
        if depth > 32 {
            return Err(failure());
        }
        let dir = roots.directory("default", &path)?;
        for name in dir.names()? {
            count += 1;
            if count > 24000 {
                return Err(failure());
            }
            let info = dir.info(&name)?.ok_or_else(failure)?;
            if info.link {
                return Err(failure());
            }
            let child = path.join(&name);
            if info.directory {
                pending.push((child, depth + 1));
            } else {
                let relative = child
                    .strip_prefix(&root)
                    .map_err(|_| failure())?
                    .to_string_lossy()
                    .replace('\\', "/")
                    .to_lowercase();
                if !names.contains(&relative) {
                    return Err(failure());
                }
            }
        }
    }
    let profile = local
        .join("portable-state")
        .join(racp_contract::digest(exe.to_string_lossy().to_lowercase()));
    racp_core::private_dir(&profile)?;
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let system = PathBuf::from(std::env::var_os("SystemRoot").ok_or_else(failure)?)
            .join("System32/icacls.exe");
        racp_core::validate_local_path(&system)?;
        let status = std::process::Command::new(system)
            .arg(root.join("webview2"))
            .args([
                "/grant",
                "*S-1-15-2-2:(OI)(CI)(RX)",
                "*S-1-15-2-1:(OI)(CI)(RX)",
            ])
            .creation_flags(0x08000000)
            .status()?;
        if !status.success() {
            return Err(failure());
        }
        std::process::Command::new(root.join("racp-client.exe"))
            .args(["--portable-state"])
            .arg(profile)
            .current_dir(&root)
            .creation_flags(0x08000000)
            .spawn()?;
    }
    Ok(())
}
