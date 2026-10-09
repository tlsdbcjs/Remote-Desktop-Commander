use racp_contract::{new_id, RacpError};
#[cfg(windows)]
use std::fs::OpenOptions;
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};
pub fn validate_local_path(path: &Path) -> Result<PathBuf, RacpError> {
    let text = path.to_string_lossy();
    if !path.is_absolute()
        || text.starts_with("\\\\")
        || text.starts_with("//")
        || text.contains('\0')
    {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    let mut current = PathBuf::new();
    for part in path.components() {
        match part {
            Component::ParentDir => {
                current.pop();
            }
            Component::CurDir => (),
            _ => current.push(part.as_os_str()),
        }
        match fs::symlink_metadata(&current) {
            Ok(info) => {
                if is_link(&info) {
                    return Err(RacpError::new("LOCAL_STATE_FAILED"));
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(e.into()),
        }
    }
    Ok(current)
}
pub(crate) fn is_link(info: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        info.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        info.file_type().is_symlink()
    }
}
pub fn private_dir(path: &Path) -> Result<(), RacpError> {
    validate_local_path(path)?;
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path)?;
    validate_local_path(path)?;
    Ok(())
}
/// Ancestors are pinned until the operation finishes. Unix accesses use dirfd/openat;
/// Windows denies deletion/renaming of each open directory and rejects reparse points.
pub(crate) struct Parent {
    _dir: File,
    #[cfg(windows)]
    path: PathBuf,
    _ancestors: Vec<File>,
}
impl Parent {
    pub(crate) fn open(path: &Path) -> Result<Self, RacpError> {
        let path = validate_local_path(path)?;
        let mut handles = vec![];
        let mut current = PathBuf::new();
        #[cfg(unix)]
        {
            use std::os::fd::{AsRawFd, FromRawFd};
            use std::os::unix::ffi::OsStrExt;
            for component in path.components() {
                current.push(component.as_os_str());
                let name = std::ffi::CString::new(component.as_os_str().as_bytes())
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                let parent = handles
                    .last()
                    .map_or(libc::AT_FDCWD, |f: &File| f.as_raw_fd());
                let descriptor = unsafe {
                    libc::openat(
                        parent,
                        name.as_ptr(),
                        libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_DIRECTORY,
                    )
                };
                if descriptor < 0 {
                    return Err(std::io::Error::last_os_error().into());
                }
                handles.push(unsafe { File::from_raw_fd(descriptor) });
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            for component in path.components() {
                current.push(component.as_os_str());
                if matches!(component, Component::Prefix(_)) {
                    continue;
                }
                let file = OpenOptions::new()
                    .read(true)
                    .share_mode(3)
                    .custom_flags(0x02000000 | 0x00200000)
                    .open(&current)?;
                let info = file.metadata()?;
                if is_link(&info) || !info.is_dir() {
                    return Err(RacpError::new("LOCAL_STATE_FAILED"));
                }
                handles.push(file);
            }
        }
        let dir = handles
            .pop()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        Ok(Self {
            _dir: dir,
            #[cfg(windows)]
            path,
            _ancestors: handles,
        })
    }
    pub(crate) fn file(&self, name: &std::ffi::OsStr, mode: OpenMode) -> Result<File, RacpError> {
        #[cfg(unix)]
        {
            use std::os::fd::{AsRawFd, FromRawFd};
            use std::os::unix::ffi::OsStrExt;
            let name = std::ffi::CString::new(name.as_bytes())
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            let flags = match mode {
                OpenMode::Read => libc::O_RDONLY,
                OpenMode::Create => libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
                OpenMode::Lock => libc::O_RDWR | libc::O_CREAT,
                OpenMode::Append => libc::O_WRONLY | libc::O_CREAT | libc::O_APPEND,
            };
            let fd = unsafe {
                libc::openat(
                    self._dir.as_raw_fd(),
                    name.as_ptr(),
                    flags | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                    0o600,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            let file = unsafe { File::from_raw_fd(fd) };
            if !file.metadata()?.is_file() {
                return Err(RacpError::new("LOCAL_STATE_FAILED"));
            }
            Ok(file)
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            let mut options = OpenOptions::new();
            options.share_mode(3).custom_flags(0x00200000);
            match mode {
                OpenMode::Read => {
                    options.read(true);
                }
                OpenMode::Create => {
                    options.write(true).create_new(true);
                }
                OpenMode::Lock => {
                    options.read(true).write(true).create(true);
                }
                OpenMode::Append => {
                    options.append(true).create(true);
                }
            }
            let file = options.open(self.path.join(name))?;
            let info = file.metadata()?;
            if is_link(&info) || !info.is_file() {
                return Err(RacpError::new("LOCAL_STATE_FAILED"));
            }
            Ok(file)
        }
    }
    fn publish(
        &self,
        source: &str,
        target: &std::ffi::OsStr,
        overwrite: bool,
    ) -> Result<(), RacpError> {
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            use std::os::unix::ffi::OsStrExt;
            let s = std::ffi::CString::new(source).unwrap();
            let t = std::ffi::CString::new(target.as_bytes())
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            let fd = self._dir.as_raw_fd();
            let result = unsafe {
                if overwrite {
                    libc::renameat(fd, s.as_ptr(), fd, t.as_ptr())
                } else {
                    libc::linkat(fd, s.as_ptr(), fd, t.as_ptr(), 0)
                }
            };
            if result != 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            if !overwrite {
                self.remove(source)?;
            }
            self._dir.sync_all()?;
        }
        #[cfg(windows)]
        {
            if overwrite {
                fs::rename(self.path.join(source), self.path.join(target))?;
            } else {
                fs::hard_link(self.path.join(source), self.path.join(target))?;
                self.remove(source)?;
            }
        }
        Ok(())
    }
    pub(crate) fn remove(&self, name: &str) -> Result<(), RacpError> {
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            let n = std::ffi::CString::new(name).unwrap();
            if unsafe { libc::unlinkat(self._dir.as_raw_fd(), n.as_ptr(), 0) } != 0 {
                return Err(std::io::Error::last_os_error().into());
            }
        }
        #[cfg(windows)]
        {
            fs::remove_file(self.path.join(name))?;
        }
        Ok(())
    }
}
#[derive(Clone, Copy)]
pub(crate) enum OpenMode {
    Read,
    Create,
    Lock,
    Append,
}
pub fn read_bounded(path: &Path, limit: usize, private: bool) -> Result<Vec<u8>, RacpError> {
    let parent = Parent::open(
        path.parent()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
    )?;
    let file = parent.file(
        path.file_name()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
        OpenMode::Read,
    )?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if private && file.metadata()?.mode() & 0o077 != 0 {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
    }
    #[cfg(not(unix))]
    let _ = private;
    let mut raw = vec![];
    file.take((limit + 1) as u64).read_to_end(&mut raw)?;
    if raw.len() > limit {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    Ok(raw)
}
pub fn atomic_write(path: &Path, raw: &[u8], overwrite: bool) -> Result<(), RacpError> {
    validate_local_path(path)?;
    let directory = path
        .parent()
        .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
    private_dir(directory)?;
    let parent = Parent::open(directory)?;
    let temporary = new_id("state");
    let result = (|| {
        let mut file = parent.file(std::ffi::OsStr::new(&temporary), OpenMode::Create)?;
        file.write_all(raw)?;
        file.sync_all()?;
        drop(file);
        parent.publish(
            &temporary,
            path.file_name()
                .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
            overwrite,
        )
    })();
    let _ = parent.remove(&temporary);
    result
}
pub struct InstanceLock {
    _file: File,
}
impl InstanceLock {
    pub fn acquire(path: &Path) -> Result<Self, RacpError> {
        let directory = path
            .parent()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        private_dir(directory)?;
        let parent = Parent::open(directory)?;
        let mut file = parent.file(
            path.file_name()
                .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
            OpenMode::Lock,
        )?;
        fs2::FileExt::try_lock_exclusive(&file).map_err(|_| RacpError::new("SETTINGS_BUSY"))?;
        if file.metadata()?.len() == 0 {
            file.write_all(&[0])?;
            file.sync_all()?;
        }
        Ok(Self { _file: file })
    }
}

pub fn secure_read_file(path: &Path) -> Result<File, RacpError> {
    let parent = Parent::open(
        path.parent()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
    )?;
    parent.file(
        path.file_name()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
        OpenMode::Read,
    )
}
pub fn secure_append_file(path: &Path) -> Result<File, RacpError> {
    let parent = Parent::open(
        path.parent()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
    )?;
    parent.file(
        path.file_name()
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
        OpenMode::Append,
    )
}
