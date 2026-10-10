use super::*;
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    os::windows::io::{FromRawHandle, OwnedHandle},
};
use windows_sys::Win32::{Foundation::*, NetworkManagement::IpHelper::*, Storage::FileSystem::*};
pub(super) struct Runtime {
    pub executable: PathBuf,
    extension: Option<PathBuf>,
    _files: Vec<File>,
    pub version: String,
}
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}
fn pin(path: &std::path::Path) -> Result<File, RacpError> {
    racp_core::validate_local_path(path)?;
    let raw = unsafe {
        CreateFileW(
            wide(
                path.to_str()
                    .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?,
            )
            .as_ptr(),
            GENERIC_READ,
            FILE_SHARE_READ,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if raw == INVALID_HANDLE_VALUE {
        return Err(RacpError::new("PATH_ACCESS_DENIED"));
    }
    let handle = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileInformationByHandle(raw, &mut info) } == 0
        || info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY) != 0
    {
        return Err(RacpError::new("PATH_ACCESS_DENIED"));
    }
    Ok(File::from(handle))
}
impl Runtime {
    pub fn load(state: &std::path::Path) -> Result<Self, RacpError> {
        let raw = racp_core::read_bounded(&state.join("native-runtime.json"), 4096, true)?;
        let config: Value = serde_json::from_slice(&raw)?;
        if config.as_object().is_none_or(|o| o.len() != 2) {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        let root = PathBuf::from(
            config["root"]
                .as_str()
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?,
        );
        racp_core::validate_local_path(&root)?;
        let expected = config["manifest_sha256"]
            .as_str()
            .filter(|s| {
                s.len() == 64
                    && s.bytes()
                        .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
            })
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
        let mut manifest = pin(&root.join("manifest.json"))?;
        let mut bytes = vec![];
        Read::by_ref(&mut manifest)
            .take(32769)
            .read_to_end(&mut bytes)?;
        if bytes.len() > 32768 || digest(&bytes) != expected {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        let value: Value = serde_json::from_slice(&bytes)?;
        if value["version"] != 1
            || value["engine"] != "cdb-win-x64"
            || value.as_object().is_none_or(|o| {
                o.keys().any(|k| {
                    !["version", "engine", "engine_version", "files", "extension"]
                        .contains(&k.as_str())
                })
            })
        {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        let version = value["engine_version"]
            .as_str()
            .filter(|s| {
                s.len() <= 64
                    && s.split('.').count() == 4
                    && s.split('.')
                        .all(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()))
            })
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?
            .to_owned();
        let files = value["files"]
            .as_object()
            .filter(|o| (3..=16).contains(&o.len()))
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
        let mut names = std::collections::BTreeSet::new();
        let mut pinned = vec![manifest];
        let mut total = 0u64;
        let mut executable = None;
        for (name, expected) in files {
            let lower = name.to_ascii_lowercase();
            if name.len() > 128
                || name
                    .bytes()
                    .any(|c| !c.is_ascii_alphanumeric() && !b"_.-".contains(&c))
                || !(lower.ends_with(".dll") || lower == "cdb.exe")
                || !names.insert(lower.clone())
            {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
            let path = root.join(name);
            let mut file = pin(&path)?;
            let size = file.metadata()?.len();
            total += size;
            if !(64..=64 * 1024 * 1024).contains(&size) || total > 128 * 1024 * 1024 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let mut header = [0u8; 64];
            file.read_exact(&mut header)?;
            let offset = u32::from_le_bytes(header[60..64].try_into().unwrap()) as u64;
            if &header[..2] != b"MZ" || offset < 64 || offset + 6 > size {
                return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
            }
            file.seek(SeekFrom::Start(offset))?;
            let mut pe = [0u8; 6];
            file.read_exact(&mut pe)?;
            if &pe != b"PE\0\0\x64\x86" {
                return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
            }
            file.seek(SeekFrom::Start(0))?;
            let mut hash = Sha256::new();
            let mut block = [0u8; 65536];
            loop {
                let n = file.read(&mut block)?;
                if n == 0 {
                    break;
                }
                hash.update(&block[..n]);
            }
            if expected != &format!("{:x}", hash.finalize()) {
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            if lower == "cdb.exe" {
                executable = Some(path);
            }
            pinned.push(file);
        }
        if !["cdb.exe", "dbgeng.dll", "dbghelp.dll"]
            .iter()
            .all(|n| names.contains(*n))
        {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        if file_version(executable.as_ref().unwrap())? != version {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let actual: std::collections::BTreeSet<_> = std::fs::read_dir(&root)?
            .map(|e| e.map(|e| e.file_name().to_string_lossy().to_ascii_lowercase()))
            .collect::<Result<_, _>>()?;
        names.insert("manifest.json".into());
        if actual != names {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        let extension = if let Some(name) = value["extension"].as_str() {
            if name != "exts.dll" || !names.contains(name) {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
            Some(root.join(name))
        } else {
            None
        };
        Ok(Self {
            executable: executable.unwrap(),
            extension,
            _files: pinned,
            version,
        })
    }
    pub fn command(
        &self,
        pid: u32,
        port: u16,
        root: &std::path::Path,
    ) -> Result<Vec<String>, RacpError> {
        racp_core::validate_local_path(root)?;
        let symbols = root.join("symbols");
        racp_core::private_dir(&symbols)?;
        let mut argv = vec![
            self.executable.to_string_lossy().into_owned(),
            "-server".into(),
            format!("tcp:port={port},clicon=127.0.0.1"),
        ];
        if let Some(extension) = &self.extension {
            argv.push(format!("-a{}", extension.with_extension("").display()));
        }
        argv.extend([
            "-p".into(),
            pid.to_string(),
            "-pd".into(),
            "-noshell".into(),
            "-noinh".into(),
            "-y".into(),
            symbols.to_string_lossy().into_owned(),
        ]);
        Ok(argv)
    }
}
pub(super) fn verify_peer(socket: &TcpStream, pid: u32, birth: f64) -> Result<(), RacpError> {
    let local = socket
        .local_addr()
        .map_err(|_| RacpError::new("PERMISSION_DENIED"))?;
    let peer = socket
        .peer_addr()
        .map_err(|_| RacpError::new("PERMISSION_DENIED"))?;
    if local.ip() != std::net::IpAddr::V4(std::net::Ipv4Addr::LOCALHOST)
        || peer.ip() != local.ip()
        || (crate::identity::process_created(pid)? - birth).abs() > 0.000001
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut size = 0;
    unsafe {
        GetExtendedTcpTable(
            std::ptr::null_mut(),
            &mut size,
            1,
            2,
            TCP_TABLE_OWNER_PID_ALL,
            0,
        );
    }
    if size < 4 || size > 16 * 1024 * 1024 {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let mut buffer = vec![0u32; (size as usize + 3) / 4];
    if unsafe {
        GetExtendedTcpTable(
            buffer.as_mut_ptr().cast(),
            &mut size,
            1,
            2,
            TCP_TABLE_OWNER_PID_ALL,
            0,
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let bytes = unsafe { std::slice::from_raw_parts(buffer.as_ptr().cast::<u8>(), size as usize) };
    let count = u32::from_ne_bytes(bytes[..4].try_into().unwrap()) as usize;
    if count > (bytes.len() - 4) / 24 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut owners = std::collections::BTreeSet::new();
    for row in bytes[4..].chunks_exact(24).take(count) {
        let dw = |at| u32::from_ne_bytes(row[at..at + 4].try_into().unwrap());
        if dw(0) == 5
            && row[4..8] == [127, 0, 0, 1]
            && row[12..16] == [127, 0, 0, 1]
            && u16::from_be_bytes(row[8..10].try_into().unwrap()) == peer.port()
            && u16::from_be_bytes(row[16..18].try_into().unwrap()) == local.port()
        {
            owners.insert(dw(20));
        }
    }
    if owners != std::collections::BTreeSet::from([pid])
        || (crate::identity::process_created(pid)? - birth).abs() > 0.000001
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(())
}

fn file_version(path: &std::path::Path) -> Result<String, RacpError> {
    let name = wide(
        path.to_str()
            .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?,
    );
    let mut unused = 0;
    let size = unsafe { GetFileVersionInfoSizeW(name.as_ptr(), &mut unused) };
    if size == 0 || size > 1024 * 1024 {
        return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
    }
    let mut data = vec![0u8; size as usize];
    if unsafe { GetFileVersionInfoW(name.as_ptr(), 0, size, data.as_mut_ptr().cast()) } == 0 {
        return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
    }
    let mut value = std::ptr::null_mut();
    let mut length = 0;
    if unsafe {
        VerQueryValueW(
            data.as_ptr().cast(),
            wide("\\").as_ptr(),
            &mut value,
            &mut length,
        )
    } == 0
        || length < std::mem::size_of::<VS_FIXEDFILEINFO>() as u32
    {
        return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
    }
    let info = unsafe { std::ptr::read_unaligned(value.cast::<VS_FIXEDFILEINFO>()) };
    if info.dwSignature != 0xFEEF04BD {
        return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
    }
    Ok(format!(
        "{}.{}.{}.{}",
        info.dwFileVersionMS >> 16,
        info.dwFileVersionMS & 0xffff,
        info.dwFileVersionLS >> 16,
        info.dwFileVersionLS & 0xffff
    ))
}
pub(super) fn provision(args: &[String], state: &std::path::Path) -> Result<Value, RacpError> {
    let option = |name: &str| -> Result<PathBuf, RacpError> {
        let path = args
            .windows(2)
            .find(|v| v[0] == name)
            .map(|v| PathBuf::from(&v[1]))
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
        racp_core::validate_local_path(&path)
    };
    let source = option("--cdb-directory")?;
    let root = option("--directory")?;
    let _lock = racp_core::InstanceLock::acquire(&state.join("native-runtime.lock"))?;
    if root.try_exists()? || state.join("native-runtime.json").try_exists()? {
        return Err(RacpError::new("CONFLICT"));
    }
    let guards = Workspaces::new(&source, &[])?;
    let directory = guards.directory("default", &source)?;
    racp_core::private_dir(&root)?;
    let destination = Workspaces::new(&root, &[])?;
    let copied = destination.directory("default", &root)?;
    let version = file_version(&source.join("cdb.exe"))?;
    let mut files = serde_json::Map::new();
    let mut total = 0u64;
    for name in [
        "cdb.exe",
        "dbgeng.dll",
        "dbghelp.dll",
        "dbgcore.dll",
        "symsrv.dll",
        "exts.dll",
    ] {
        let Some(info) = directory.info(name)? else {
            if ["cdb.exe", "dbgeng.dll", "dbghelp.dll"].contains(&name) {
                return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
            }
            continue;
        };
        if info.directory || info.link || info.size > 64 * 1024 * 1024 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        total += info.size;
        if total > 128 * 1024 * 1024 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let mut input = directory.open_read(name)?;
        let before = racp_core::FileInfo::from_file(&input)?;
        let mut output = copied.create(name)?;
        let mut hash = Sha256::new();
        let mut count = 0;
        let mut bytes = [0u8; 65536];
        loop {
            let n = input.read(&mut bytes)?;
            if n == 0 {
                break;
            }
            count += n as u64;
            if count > before.size {
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            std::io::Write::write_all(&mut output, &bytes[..n])?;
            hash.update(&bytes[..n]);
        }
        output.sync_all()?;
        if count != before.size
            || racp_core::FileInfo::from_file(&input)?.revision() != before.revision()
            || directory.require_file(name)?.revision() != before.revision()
        {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        files.insert(name.into(), json!(format!("{:x}", hash.finalize())));
    }
    let extension = if files.contains_key("exts.dll") {
        json!("exts.dll")
    } else {
        Value::Null
    };
    let manifest = serde_json::to_vec(
        &json!({"version":1,"engine":"cdb-win-x64","engine_version":version,"files":files,"extension":extension}),
    )?;
    racp_core::atomic_write(&root.join("manifest.json"), &manifest, false)?;
    racp_core::atomic_write(
        &state.join("native-runtime.json"),
        &serde_json::to_vec(&json!({"root":root,"manifest_sha256":digest(&manifest)}))?,
        false,
    )?;
    if let Err(error) = Runtime::load(state) {
        let _ = std::fs::remove_file(state.join("native-runtime.json"));
        return Err(error);
    }
    racp_core::atomic_write(
        &state.join("native-runtime-provenance.json"),
        &serde_json::to_vec(
            &json!({"version":1,"engine":"Microsoft Debugging Tools for Windows","engine_version":version,"source":source,"license":"Microsoft SDK license; locally provisioned","bundled":false}),
        )?,
        false,
    )?;
    Ok(
        json!({"configured":true,"backend":"cdb-win-x64","backend_version":version,"restart_required":true}),
    )
}
