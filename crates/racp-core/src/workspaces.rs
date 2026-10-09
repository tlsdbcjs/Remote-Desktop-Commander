//! Named roots retain their original filesystem identity. Every operation pins
//! ancestors and uses no-follow directory-relative access on Unix.
use crate::{
    paths::{OpenMode, Parent},
    validate_local_path, WorkspaceSpec,
};
use racp_contract::RacpError;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs::File,
    path::{Component, Path, PathBuf},
    sync::Arc,
};
#[derive(Clone)]
struct Root {
    path: PathBuf,
    identity: (u64, u64),
}
#[derive(Clone)]
pub struct Workspaces {
    roots: Arc<BTreeMap<String, Root>>,
}
#[derive(Clone, Debug)]
pub struct FileInfo {
    pub device: u64,
    pub inode: u64,
    pub mtime_ns: i128,
    pub size: u64,
    pub directory: bool,
    pub link: bool,
    pub mode: u32,
}
impl FileInfo {
    pub fn revision(&self) -> String {
        format!(
            "{:x}-{:x}-{:x}-{:x}",
            self.device, self.inode, self.mtime_ns, self.size
        )
    }
    pub fn metadata(&self, path: &Path) -> Value {
        json!({"path":path,"name":path.file_name().unwrap_or_default().to_string_lossy(),"size_bytes":self.size,"revision":self.revision(),"mtime_ns":self.mtime_ns.to_string(),"is_link":self.link,"type":if self.link{"link"}else if self.directory{"directory"}else{"file"}})
    }
}
pub struct Directory {
    inner: Parent,
    pub path: PathBuf,
}
fn denied(_: RacpError) -> RacpError {
    RacpError::new("PATH_ACCESS_DENIED")
}
fn os_error(error: std::io::Error) -> RacpError {
    RacpError::new(match error.kind() {
        std::io::ErrorKind::NotFound => "PATH_NOT_FOUND",
        std::io::ErrorKind::AlreadyExists => "CONFLICT",
        _ => "PATH_ACCESS_DENIED",
    })
}
fn file_info(file: &File) -> Result<FileInfo, RacpError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let m = file.metadata().map_err(os_error)?;
        Ok(FileInfo {
            device: m.dev(),
            inode: m.ino(),
            mtime_ns: m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128,
            size: m.len(),
            directory: m.is_dir(),
            link: m.file_type().is_symlink(),
            mode: m.mode(),
        })
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
        };
        let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        let time = ((info.ftLastWriteTime.dwHighDateTime as u64) << 32)
            | info.ftLastWriteTime.dwLowDateTime as u64;
        Ok(FileInfo {
            device: info.dwVolumeSerialNumber as u64,
            inode: ((info.nFileIndexHigh as u64) << 32) | info.nFileIndexLow as u64,
            mtime_ns: (time as i128 - 116444736000000000) * 100,
            size: ((info.nFileSizeHigh as u64) << 32) | info.nFileSizeLow as u64,
            directory: info.dwFileAttributes & 0x10 != 0,
            link: info.dwFileAttributes & 0x400 != 0,
            mode: 0,
        })
    }
}
impl Workspaces {
    pub fn new(root: &Path, additional: &[WorkspaceSpec]) -> Result<Self, RacpError> {
        let mut roots = BTreeMap::new();
        for (id, path) in std::iter::once(("default", root))
            .chain(additional.iter().map(|s| (s.id.as_str(), s.path.as_path())))
        {
            let path = validate_local_path(path).map_err(denied)?;
            let parent = Parent::open(&path).map_err(denied)?;
            let info = file_info(&parent._dir)?;
            if !info.directory || info.link || roots.contains_key(id) {
                return Err(RacpError::new("PATH_ACCESS_DENIED"));
            }
            roots.insert(
                id.into(),
                Root {
                    path,
                    identity: (info.device, info.inode),
                },
            );
        }
        Ok(Self {
            roots: Arc::new(roots),
        })
    }
    pub fn root(&self, id: &str) -> Result<&Path, RacpError> {
        self.roots
            .get(id)
            .map(|r| r.path.as_path())
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))
    }
    pub fn inventory(&self) -> Vec<Value> {
        self.roots
            .iter()
            .map(|(id, root)| json!({"id":id,"path":root.path}))
            .collect()
    }
    pub fn is_root(&self, path: &Path) -> bool {
        self.roots
            .values()
            .any(|r| path_within(&r.path, path) && path_within(path, &r.path))
    }
    pub fn protects_root(&self, path: &Path) -> bool {
        self.roots.values().any(|r| path_within(&r.path, path))
    }
    pub fn path(&self, id: &str, raw: &str) -> Result<PathBuf, RacpError> {
        let root = self.root(id)?;
        if raw.contains('\0')
            || ["~", "//", "\\\\"]
                .iter()
                .any(|prefix| raw.starts_with(prefix))
        {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        #[cfg(windows)]
        {
            // Reject ADS, device aliases, drive-relative and normalized-away names.
            let tail = if raw.as_bytes().get(1) == Some(&b':') {
                if raw.as_bytes().get(2) != Some(&b'\\') && raw.as_bytes().get(2) != Some(&b'/') {
                    return Err(RacpError::new("PATH_ACCESS_DENIED"));
                }
                &raw[2..]
            } else {
                raw
            };
            if tail.contains(':') {
                return Err(RacpError::new("PATH_ACCESS_DENIED"));
            }
            for part in tail.split(['/', '\\']) {
                if part == "." || part == ".." || part.is_empty() {
                    continue;
                }
                let stem = part.split('.').next().unwrap_or("").to_ascii_uppercase();
                if part.ends_with(['.', ' '])
                    || ["CON", "PRN", "AUX", "NUL"].contains(&stem.as_str())
                    || ((stem.starts_with("COM") || stem.starts_with("LPT"))
                        && stem.len() == 4
                        && matches!(stem.as_bytes()[3], b'1'..=b'9'))
                {
                    return Err(RacpError::new("PATH_ACCESS_DENIED"));
                }
            }
        }
        #[cfg(not(windows))]
        if raw.as_bytes().get(1) == Some(&b':') {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        let raw = Path::new(raw);
        let target = if raw.is_absolute() {
            raw.to_path_buf()
        } else {
            root.join(raw)
        };
        let mut normalized = PathBuf::new();
        for part in target.components() {
            match part {
                Component::ParentDir => {
                    normalized.pop();
                }
                Component::CurDir => {}
                _ => normalized.push(part.as_os_str()),
            }
        }
        if !path_within(&normalized, root) {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        #[cfg(windows)]
        {
            let relative: PathBuf = normalized
                .components()
                .skip(root.components().count())
                .collect();
            return Ok(root.join(relative));
        }
        #[cfg(not(windows))]
        Ok(normalized)
    }
    pub fn directory(&self, id: &str, path: &Path) -> Result<Directory, RacpError> {
        let root = self
            .roots
            .get(id)
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
        if !path_within(path, &root.path) {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        let inner = Parent::open(path).map_err(denied)?;
        let index = inner
            .locations
            .iter()
            .position(|p| path_within(p, &root.path) && path_within(&root.path, p))
            .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?;
        let file = if index == inner._ancestors.len() {
            &inner._dir
        } else {
            &inner._ancestors[index]
        };
        let info = file_info(file)?;
        if (info.device, info.inode) != root.identity {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        Ok(Directory {
            inner,
            path: path.into(),
        })
    }
    pub fn parent(&self, id: &str, path: &Path) -> Result<Directory, RacpError> {
        if self.is_root(path) {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        self.directory(
            id,
            path.parent()
                .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?,
        )
    }
}
impl Directory {
    fn name(name: &str) -> Result<(), RacpError> {
        if name.is_empty() || name == "." || name == ".." || name.contains(['/', '\\', '\0']) {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        Ok(())
    }
    #[allow(clippy::unnecessary_cast)] // libc stat field widths vary by target.
    pub fn info(&self, name: &str) -> Result<Option<FileInfo>, RacpError> {
        Self::name(name)?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            let name = std::ffi::CString::new(name).unwrap();
            let mut info: libc::stat = unsafe { std::mem::zeroed() };
            if unsafe {
                libc::fstatat(
                    self.inner._dir.as_raw_fd(),
                    name.as_ptr(),
                    &mut info,
                    libc::AT_SYMLINK_NOFOLLOW,
                )
            } != 0
            {
                let error = std::io::Error::last_os_error();
                if error.kind() == std::io::ErrorKind::NotFound {
                    return Ok(None);
                }
                return Err(os_error(error));
            }
            #[cfg(target_os = "macos")]
            let nanoseconds = info.st_mtimespec.tv_nsec;
            #[cfg(not(target_os = "macos"))]
            let nanoseconds = info.st_mtime_nsec;
            Ok(Some(FileInfo {
                device: info.st_dev as u64,
                inode: info.st_ino as u64,
                mtime_ns: info.st_mtime as i128 * 1_000_000_000 + nanoseconds as i128,
                size: info.st_size as u64,
                directory: info.st_mode & libc::S_IFMT == libc::S_IFDIR,
                link: info.st_mode & libc::S_IFMT == libc::S_IFLNK,
                mode: info.st_mode,
            }))
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            let file = std::fs::OpenOptions::new()
                .access_mode(0x80)
                .share_mode(3)
                .custom_flags(0x02000000 | 0x00200000)
                .open(self.path.join(name));
            match file {
                Ok(file) => Ok(Some(file_info(&file)?)),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
                Err(e) => Err(os_error(e)),
            }
        }
    }
    pub fn own_info(&self) -> Result<FileInfo, RacpError> {
        file_info(&self.inner._dir)
    }
    pub fn require_file(&self, name: &str) -> Result<FileInfo, RacpError> {
        let info = self
            .info(name)?
            .ok_or_else(|| RacpError::new("PATH_NOT_FOUND"))?;
        if info.link || info.directory {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        Ok(info)
    }
    pub fn open_read(&self, name: &str) -> Result<File, RacpError> {
        Self::name(name)?;
        self.require_file(name)?;
        self.inner
            .file(std::ffi::OsStr::new(name), OpenMode::Read)
            .map_err(denied)
    }
    pub fn create(&self, name: &str) -> Result<File, RacpError> {
        Self::name(name)?;
        if self.info(name)?.is_some() {
            return Err(RacpError::new("CONFLICT"));
        }
        self.inner
            .file(std::ffi::OsStr::new(name), OpenMode::Create)
            .map_err(denied)
    }
    pub fn append(&self, name: &str) -> Result<File, RacpError> {
        Self::name(name)?;
        self.require_file(name)?;
        self.inner
            .file(std::ffi::OsStr::new(name), OpenMode::ExistingAppend)
            .map_err(denied)
    }
    pub fn publish(&self, source: &str, target: &str, overwrite: bool) -> Result<(), RacpError> {
        Self::name(source)?;
        Self::name(target)?;
        if let Some(info) = self.info(target)? {
            if info.link || info.directory {
                return Err(RacpError::new("PATH_ACCESS_DENIED"));
            }
            if !overwrite {
                return Err(RacpError::new("CONFLICT"));
            }
        }
        self.inner
            .publish(source, std::ffi::OsStr::new(target), overwrite)
            .map_err(denied)
    }
    pub fn unlink(&self, name: &str, directory: bool) -> Result<(), RacpError> {
        Self::name(name)?;
        let info = self
            .info(name)?
            .ok_or_else(|| RacpError::new("PATH_NOT_FOUND"))?;
        if info.link || info.directory != directory {
            return Err(RacpError::new("PATH_ACCESS_DENIED"));
        }
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            let name = std::ffi::CString::new(name).unwrap();
            if unsafe {
                libc::unlinkat(
                    self.inner._dir.as_raw_fd(),
                    name.as_ptr(),
                    if directory { libc::AT_REMOVEDIR } else { 0 },
                )
            } != 0
            {
                return Err(os_error(std::io::Error::last_os_error()));
            }
        }
        #[cfg(windows)]
        {
            if directory {
                std::fs::remove_dir(self.path.join(name))
            } else {
                std::fs::remove_file(self.path.join(name))
            }
            .map_err(os_error)?;
        }
        Ok(())
    }
    pub fn mkdir(&self, name: &str) -> Result<(), RacpError> {
        Self::name(name)?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            let name = std::ffi::CString::new(name).unwrap();
            if unsafe { libc::mkdirat(self.inner._dir.as_raw_fd(), name.as_ptr(), 0o700) } != 0 {
                return Err(os_error(std::io::Error::last_os_error()));
            }
        }
        #[cfg(windows)]
        {
            std::fs::create_dir(self.path.join(name)).map_err(os_error)?;
        }
        Ok(())
    }
    pub fn names(&self) -> Result<Vec<String>, RacpError> {
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            let fd = unsafe { libc::dup(self.inner._dir.as_raw_fd()) };
            if fd < 0 {
                return Err(os_error(std::io::Error::last_os_error()));
            }
            let directory = unsafe { libc::fdopendir(fd) };
            if directory.is_null() {
                unsafe {
                    libc::close(fd);
                }
                return Err(RacpError::new("PATH_ACCESS_DENIED"));
            }
            unsafe {
                libc::rewinddir(directory);
            }
            let result = (|| {
                let mut names = vec![];
                loop {
                    let entry = unsafe { libc::readdir(directory) };
                    if entry.is_null() {
                        break;
                    }
                    let name = unsafe { std::ffi::CStr::from_ptr((*entry).d_name.as_ptr()) }
                        .to_str()
                        .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
                    if name != "." && name != ".." {
                        names.push(name.into());
                        if names.len() > 100000 {
                            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                        }
                    }
                }
                names.sort();
                Ok(names)
            })();
            unsafe {
                libc::closedir(directory);
            }
            result
        }
        #[cfg(windows)]
        {
            let mut names = vec![];
            for entry in std::fs::read_dir(&self.path).map_err(os_error)? {
                names.push(
                    entry
                        .map_err(os_error)?
                        .file_name()
                        .into_string()
                        .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?,
                );
                if names.len() > 100000 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                }
            }
            names.sort();
            Ok(names)
        }
    }
    pub fn sync(&self) -> Result<(), RacpError> {
        #[cfg(unix)]
        self.inner._dir.sync_all().map_err(os_error)?;
        Ok(())
    }
    pub fn preserve_permissions(&self, file: &File, mode: u32) -> Result<(), RacpError> {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(std::fs::Permissions::from_mode(mode & 0o7777))
                .map_err(os_error)?;
        }
        #[cfg(windows)]
        let _ = (file, mode);
        Ok(())
    }
    pub fn preserve_acl(&self, source: &str, temporary: &str) -> Result<(), RacpError> {
        Self::name(source)?;
        Self::name(temporary)?;
        #[cfg(windows)]
        {
            use std::os::windows::{fs::OpenOptionsExt, io::AsRawHandle};
            use windows_sys::Win32::{
                Foundation::LocalFree,
                Security::{
                    Authorization::{GetSecurityInfo, SetSecurityInfo, SE_FILE_OBJECT},
                    GetSecurityDescriptorControl,
                },
            };
            let source = self.open_read(source)?;
            let target = std::fs::OpenOptions::new()
                .access_mode(0x00040000 | 0x00020000 | 0x80)
                .share_mode(3)
                .custom_flags(0x00200000)
                .open(self.path.join(temporary))
                .map_err(os_error)?;
            if file_info(&target)?.link {
                return Err(RacpError::new("PATH_ACCESS_DENIED"));
            }
            let mut dacl = std::ptr::null_mut();
            let mut descriptor = std::ptr::null_mut();
            let status = unsafe {
                GetSecurityInfo(
                    source.as_raw_handle(),
                    SE_FILE_OBJECT,
                    4,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    &mut dacl,
                    std::ptr::null_mut(),
                    &mut descriptor,
                )
            };
            if status != 0 {
                return Err(RacpError::new("PATH_ACCESS_DENIED"));
            }
            let mut control = 0u16;
            let mut revision = 0u32;
            let valid =
                unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) };
            let flags = 4 | if control & 0x1000 != 0 {
                0x80000000
            } else {
                0x20000000
            };
            let status = if valid != 0 {
                unsafe {
                    SetSecurityInfo(
                        target.as_raw_handle(),
                        SE_FILE_OBJECT,
                        flags,
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                        dacl,
                        std::ptr::null_mut(),
                    )
                }
            } else {
                1
            };
            unsafe {
                LocalFree(descriptor);
            }
            if status != 0 {
                return Err(RacpError::new("PATH_ACCESS_DENIED"));
            }
        }
        Ok(())
    }
    pub fn move_to(
        &self,
        source: &str,
        other: &Self,
        target: &str,
        overwrite: bool,
    ) -> Result<(), RacpError> {
        Self::name(source)?;
        Self::name(target)?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            let source = std::ffi::CString::new(source).unwrap();
            let target = std::ffi::CString::new(target).unwrap();
            #[cfg(target_os = "linux")]
            let result = unsafe {
                libc::renameat2(
                    self.inner._dir.as_raw_fd(),
                    source.as_ptr(),
                    other.inner._dir.as_raw_fd(),
                    target.as_ptr(),
                    if overwrite { 0 } else { libc::RENAME_NOREPLACE },
                )
            };
            #[cfg(not(target_os = "linux"))]
            let result = if overwrite {
                unsafe {
                    libc::renameat(
                        self.inner._dir.as_raw_fd(),
                        source.as_ptr(),
                        other.inner._dir.as_raw_fd(),
                        target.as_ptr(),
                    )
                }
            } else {
                return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
            };
            if result != 0 {
                let error = std::io::Error::last_os_error();
                if error.raw_os_error() == Some(libc::EXDEV) {
                    return Err(RacpError::new("CROSS_VOLUME"));
                }
                return Err(os_error(error));
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;
            use windows_sys::Win32::Storage::FileSystem::MoveFileExW;
            let source: Vec<u16> = self
                .path
                .join(source)
                .as_os_str()
                .encode_wide()
                .chain(Some(0))
                .collect();
            let target: Vec<u16> = other
                .path
                .join(target)
                .as_os_str()
                .encode_wide()
                .chain(Some(0))
                .collect();
            if unsafe {
                MoveFileExW(
                    source.as_ptr(),
                    target.as_ptr(),
                    if overwrite { 1 } else { 0 },
                )
            } == 0
            {
                let error = std::io::Error::last_os_error();
                if error.raw_os_error() == Some(17) {
                    return Err(RacpError::new("CROSS_VOLUME"));
                }
                return Err(os_error(error));
            }
        }
        self.sync()?;
        other.sync()?;
        Ok(())
    }
}

pub fn path_within(path: &Path, root: &Path) -> bool {
    #[cfg(windows)]
    {
        let path: Vec<String> = path
            .components()
            .map(|c| c.as_os_str().to_string_lossy().to_lowercase())
            .collect();
        let root: Vec<String> = root
            .components()
            .map(|c| c.as_os_str().to_string_lossy().to_lowercase())
            .collect();
        path.starts_with(&root)
    }
    #[cfg(not(windows))]
    {
        path.starts_with(root)
    }
}
