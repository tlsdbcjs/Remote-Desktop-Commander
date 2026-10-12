use super::*;

use windows_sys::Win32::{Foundation::*, NetworkManagement::IpHelper::*, Storage::FileSystem::*};
pub(super) struct Runtime {
    pub executable: PathBuf,
    extension: Option<PathBuf>,
    pub version: String,
    _admitted: Arc<racp_core::runtime_admission::PinnedRuntime>,
}
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}
impl Runtime {
    pub fn from_admitted(
        runtime: Arc<racp_core::runtime_admission::PinnedRuntime>,
    ) -> Result<Self, RacpError> {
        use racp_contract::runtime_manifest::RuntimeId;
        racp_core::runtime_admission::validate_selection(&runtime.manifest)?;
        if runtime.manifest.runtime.id != RuntimeId::Cdb
            || file_version(&runtime.entrypoint)? != runtime.manifest.runtime.version
        {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let parent = runtime
            .entrypoint
            .parent()
            .ok_or_else(|| RacpError::new("RUNTIME_MANIFEST_INVALID"))?;
        let extension = runtime
            .manifest
            .files
            .iter()
            .find(|file| {
                PathBuf::from(&file.path).parent()
                    == std::path::Path::new(&runtime.manifest.entrypoint).parent()
                    && PathBuf::from(&file.path)
                        .file_name()
                        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("exts.dll"))
            })
            .map(|file| parent.join(PathBuf::from(&file.path).file_name().unwrap()));
        Ok(Self {
            executable: runtime.entrypoint.clone(),
            extension,
            version: runtime.manifest.runtime.version.clone(),
            _admitted: runtime,
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
