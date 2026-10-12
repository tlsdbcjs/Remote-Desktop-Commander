//! Local consent and immutable, manifest-addressed native runtime copies. Never imports legacy tools.
use crate::{atomic_write, private_dir, read_bounded, validate_local_path, Workspaces};
use racp_contract::{
    digest,
    local_ipc::reject_duplicate_keys,
    new_id,
    runtime_manifest::{RuntimeArch, RuntimeBundleManifest, RuntimeFileRole, RuntimeId, RuntimeOs},
    RacpError,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Instant,
};

const MANIFEST_FILE: &str = "admitted-manifest.json";
const CONFIG_FILE: &str = "native-runtime-config.json";
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeRuntimeConfig {
    pub schema_version: u32,
    pub revision: u64,
    pub chromium_manifest: Option<String>,
    #[serde(default)]
    pub cdb_manifest: Option<String>,
    #[serde(default)]
    pub gdb_manifest: Option<String>,
    #[serde(default)]
    pub analysis_manifest: Option<String>,
    pub browser_allow_origins: Vec<String>,
    pub external_cdp: bool,
}
impl Default for NativeRuntimeConfig {
    fn default() -> Self {
        Self {
            schema_version: 1,
            revision: 0,
            chromium_manifest: None,
            cdb_manifest: None,
            gdb_manifest: None,
            analysis_manifest: None,
            browser_allow_origins: vec![],
            external_cdp: false,
        }
    }
}
impl NativeRuntimeConfig {
    pub fn validate(&self) -> Result<(), RacpError> {
        if self.schema_version != 1
            || self.revision > i64::MAX as u64
            || self.chromium_manifest.as_ref().is_some_and(|s| !hash(s))
            || [
                &self.cdb_manifest,
                &self.gdb_manifest,
                &self.analysis_manifest,
            ]
            .into_iter()
            .flatten()
            .any(|s| !hash(s))
            || self.browser_allow_origins.len() > 64
        {
            return Err(RacpError::new("RUNTIME_CONFIG_INVALID"));
        }
        let mut origins = std::collections::BTreeSet::new();
        for origin in &self.browser_allow_origins {
            let url =
                url::Url::parse(origin).map_err(|_| RacpError::new("RUNTIME_CONFIG_INVALID"))?;
            if origin.len() > 2048
                || !matches!(url.scheme(), "http" | "https")
                || url.host_str().is_none()
                || !url.username().is_empty()
                || url.password().is_some()
                || url.path() != "/"
                || url.query().is_some()
                || url.fragment().is_some()
                || url.origin().ascii_serialization() != *origin
                || !origins.insert(origin)
            {
                return Err(RacpError::new("RUNTIME_CONFIG_INVALID"));
            }
        }
        Ok(())
    }
    pub fn load(root: &Path) -> Result<Self, RacpError> {
        validate_local_path(root)?;
        let path = root.join(CONFIG_FILE);
        if !path.try_exists()? {
            return Ok(Self::default());
        }
        let bytes = read_bounded(&path, 65536, false)?;
        reject_duplicate_keys(&bytes)?;
        let config: Self = serde_json::from_slice(&bytes)?;
        config.validate()?;
        Ok(config)
    }
    pub fn save(
        &self,
        root: &Path,
        expected: u64,
        budget: &AdmissionBudget,
    ) -> Result<(), RacpError> {
        crate::state_schema::NativeState::load(root)?;
        let _lock = crate::InstanceLock::acquire(&root.join("native-agent.lock"))?;
        self.validate()?;
        chromium(root, self, budget)?;
        for (id, selected) in [
            (RuntimeId::Cdb, &self.cdb_manifest),
            (RuntimeId::Gdb, &self.gdb_manifest),
            (RuntimeId::Ghidra, &self.analysis_manifest),
        ] {
            if let Some(hash) = selected {
                let manifest = verify(root, hash, budget)?;
                if manifest.runtime.id != id {
                    return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
                }
                validate_selection(&manifest)?;
            }
        }
        if Self::load(root)?.revision != expected
            || self.revision
                != expected
                    .checked_add(1)
                    .ok_or_else(|| RacpError::new("STATE_REVISION_CONFLICT"))?
        {
            return Err(RacpError::new("STATE_REVISION_CONFLICT"));
        }
        atomic_write(&root.join(CONFIG_FILE), &serde_json::to_vec(self)?, false)
    }
}
pub struct AdmissionBudget {
    pub deadline: Instant,
    pub cancelled: Arc<AtomicBool>,
}
impl AdmissionBudget {
    fn check(&self) -> Result<(), RacpError> {
        if self.cancelled.load(Ordering::SeqCst) {
            Err(RacpError::new("CANCELLED"))
        } else if Instant::now() >= self.deadline {
            Err(RacpError::new("TIMEOUT"))
        } else {
            Ok(())
        }
    }
}
fn hash(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|v| v.is_ascii_hexdigit())
}
pub fn host() -> (RuntimeOs, RuntimeArch) {
    let os = if cfg!(target_os = "windows") {
        RuntimeOs::Windows
    } else if cfg!(target_os = "macos") {
        RuntimeOs::Macos
    } else {
        RuntimeOs::Linux
    };
    let arch = if cfg!(target_arch = "aarch64") {
        RuntimeArch::Arm64
    } else {
        RuntimeArch::X64
    };
    (os, arch)
}
fn parse(bytes: &[u8], expected: &str) -> Result<RuntimeBundleManifest, RacpError> {
    if bytes.len() > 1048576 || !hash(expected) || !digest(bytes).eq_ignore_ascii_case(expected) {
        return Err(RacpError::new("RUNTIME_MANIFEST_UNTRUSTED"));
    }
    reject_duplicate_keys(bytes)?;
    let manifest: RuntimeBundleManifest = serde_json::from_slice(bytes)?;
    let (os, arch) = host();
    manifest.validate(os, arch)?;
    Ok(manifest)
}
/// Hash must be supplied through an explicitly local command/UI; no remote operation calls this API.
pub fn admit(
    root: &Path,
    source: &Path,
    bytes: &[u8],
    expected: &str,
    budget: &AdmissionBudget,
) -> Result<RuntimeBundleManifest, RacpError> {
    budget.check()?;
    crate::state_schema::NativeState::load(root)?;
    let _lock = crate::InstanceLock::acquire(&root.join("native-agent.lock"))?;
    validate_local_path(root)?;
    validate_local_path(source)?;
    let manifest = parse(bytes, expected)?;
    let runtime_root = root.join("runtimes");
    private_dir(&runtime_root)?;
    let final_path = runtime_root.join(expected.to_ascii_lowercase());
    if final_path.try_exists()? {
        let existing = verify(root, expected, budget)?;
        if digest(serde_json::to_vec(&existing)?) != digest(serde_json::to_vec(&manifest)?) {
            return Err(RacpError::new("RUNTIME_MANIFEST_UNTRUSTED"));
        }
        return Ok(existing);
    }
    let candidate = runtime_root.join(new_id("admission"));
    std::fs::create_dir(&candidate)?;
    private_dir(&candidate)?;
    // A failed candidate is preserved with no accepted manifest/activation pointer. It is never executed.
    let pending = candidate.join("admission.pending");
    atomic_write(&pending, b"runtime admission incomplete", false)?;
    let source_guard = Workspaces::new(source, &[])?;
    let output_guard = Workspaces::new(&candidate, &[])?;
    for entry in &manifest.files {
        budget.check()?;
        let input = source_guard.path("default", &entry.path)?;
        let parent = source_guard.parent("default", &input)?;
        let name = input
            .file_name()
            .ok_or_else(|| RacpError::new("RUNTIME_MANIFEST_INVALID"))?
            .to_string_lossy();
        let metadata = parent.require_file(&name)?;
        if metadata.size != entry.size_bytes {
            return Err(RacpError::new("RUNTIME_FILE_MISMATCH"));
        }
        let mut source_file = parent.open_read(&name)?;
        if entry.role == RuntimeFileRole::NativeBinary {
            native_binary(&mut source_file, manifest.runtime.os, manifest.runtime.arch)?;
            source_file.seek(SeekFrom::Start(0))?;
        }
        let output = output_guard.path("default", &entry.path)?;
        let relative = Path::new(&entry.path);
        let mut directory = candidate.clone();
        if let Some(parent) = relative.parent() {
            for part in parent.components() {
                directory.push(part.as_os_str());
                if !directory.try_exists()? {
                    std::fs::create_dir(&directory)?;
                    private_dir(&directory)?;
                }
                output_guard.directory("default", &directory)?;
            }
        }
        let dest_parent = output_guard.parent("default", &output)?;
        let mut dest = dest_parent.create(&name)?;
        let mut digest = Sha256::new();
        let mut count = 0u64;
        let mut buffer = [0u8; 65536];
        loop {
            budget.check()?;
            let n = source_file.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            count = count
                .checked_add(n as u64)
                .filter(|v| *v <= entry.size_bytes)
                .ok_or_else(|| RacpError::new("RUNTIME_FILE_MISMATCH"))?;
            digest.update(&buffer[..n]);
            dest.write_all(&buffer[..n])?;
        }
        dest.sync_all()?;
        if count != entry.size_bytes
            || !format!("{:x}", digest.finalize()).eq_ignore_ascii_case(&entry.sha256)
            || parent.require_file(&name)?.revision() != metadata.revision()
        {
            return Err(RacpError::new("RUNTIME_FILE_MISMATCH"));
        }
        #[cfg(unix)]
        if entry.role == RuntimeFileRole::NativeBinary {
            use std::os::unix::fs::PermissionsExt;
            dest.set_permissions(std::fs::Permissions::from_mode(0o700))?;
        }
    }
    budget.check()?;
    atomic_write(&candidate.join(MANIFEST_FILE), bytes, false)?;
    std::fs::remove_file(pending)?;
    drop(output_guard);
    let root_guard = Workspaces::new(&runtime_root, &[])?;
    let directory = root_guard.directory("default", &runtime_root)?;
    directory.move_to(
        candidate.file_name().unwrap().to_string_lossy().as_ref(),
        &directory,
        expected.to_ascii_lowercase().as_str(),
        false,
    )?;
    directory.sync()?;
    Ok(manifest)
}
pub fn verify(
    root: &Path,
    manifest_hash: &str,
    budget: &AdmissionBudget,
) -> Result<RuntimeBundleManifest, RacpError> {
    pin(root, manifest_hash, budget).map(|runtime| runtime.manifest)
}
pub struct PinnedRuntime {
    pub manifest: RuntimeBundleManifest,
    pub entrypoint: PathBuf,
    _guards: Workspaces,
    _parents: Vec<crate::Directory>,
    _files: Vec<std::fs::File>,
}
pub fn validate_selection(manifest: &RuntimeBundleManifest) -> Result<(), RacpError> {
    let name = std::path::Path::new(&manifest.entrypoint)
        .file_name()
        .and_then(|v| v.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    match manifest.runtime.id {
        RuntimeId::Cdb => {
            let parent = std::path::Path::new(&manifest.entrypoint)
                .parent()
                .unwrap_or_else(|| std::path::Path::new(""));
            if name != "cdb.exe"
                || !["dbgeng.dll", "dbghelp.dll"].into_iter().all(|name| {
                    manifest.files.iter().any(|file| {
                        std::path::Path::new(&file.path).parent() == Some(parent)
                            && std::path::Path::new(&file.path)
                                .file_name()
                                .is_some_and(|v| v.to_string_lossy().eq_ignore_ascii_case(name))
                            && file.role == RuntimeFileRole::NativeBinary
                    })
                })
            {
                return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
            }
        }
        RuntimeId::Gdb if !["gdb", "gdb.exe"].contains(&name.as_str()) => {
            return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"))
        }
        // Host speaks the bounded RACP native plugin protocol. A java.exe wrapper is not a native host.
        RuntimeId::Ghidra
            if !["racp-analysis-host", "racp-analysis-host.exe"].contains(&name.as_str()) =>
        {
            return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"))
        }
        _ => (),
    }
    Ok(())
}
pub fn pin(
    root: &Path,
    manifest_hash: &str,
    budget: &AdmissionBudget,
) -> Result<PinnedRuntime, RacpError> {
    if !hash(manifest_hash) {
        return Err(RacpError::new("RUNTIME_MANIFEST_UNTRUSTED"));
    }
    let path = root
        .join("runtimes")
        .join(manifest_hash.to_ascii_lowercase());
    if path.join("admission.pending").try_exists()? {
        return Err(RacpError::new("RUNTIME_MANIFEST_UNTRUSTED"));
    }
    let guard = Workspaces::new(&path, &[])?;
    let root_parent = guard.directory("default", &path)?;
    let mut manifest_file = root_parent.open_pinned_read(MANIFEST_FILE)?;
    let mut bytes = vec![];
    Read::by_ref(&mut manifest_file)
        .take(1048577)
        .read_to_end(&mut bytes)?;
    let manifest = parse(&bytes, manifest_hash)?;
    // Exact inventory: DLL/engine search must not admit unlisted adjacent files.
    let mut expected: std::collections::BTreeSet<String> = manifest
        .files
        .iter()
        .map(|file| file.path.to_ascii_lowercase())
        .collect();
    expected.insert(MANIFEST_FILE.into());
    let mut actual = std::collections::BTreeSet::new();
    let mut pending = vec![(path.clone(), 0u8)];
    let mut count = 0usize;
    while let Some((directory, depth)) = pending.pop() {
        if depth > 32 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let dir = guard.directory("default", &directory)?;
        for name in dir.names()? {
            budget.check()?;
            count += 1;
            if count > 8192 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let info = dir
                .info(&name)?
                .ok_or_else(|| RacpError::new("RUNTIME_FILE_MISMATCH"))?;
            if info.link {
                return Err(RacpError::new("RUNTIME_FILE_MISMATCH"));
            }
            let child = directory.join(name);
            if info.directory {
                pending.push((child, depth + 1));
            } else {
                actual.insert(
                    child
                        .strip_prefix(&path)
                        .map_err(|_| RacpError::new("RUNTIME_FILE_MISMATCH"))?
                        .to_string_lossy()
                        .replace('\\', "/")
                        .to_ascii_lowercase(),
                );
            }
        }
    }
    if actual != expected {
        return Err(RacpError::new("RUNTIME_FILE_MISMATCH"));
    }
    let mut pins = vec![manifest_file];
    let mut parents = vec![root_parent];
    for entry in &manifest.files {
        budget.check()?;
        let target = guard.path("default", &entry.path)?;
        let parent = guard.parent("default", &target)?;
        let name = target.file_name().unwrap().to_string_lossy();
        let metadata = parent.require_file(&name)?;
        if metadata.size != entry.size_bytes {
            return Err(RacpError::new("RUNTIME_FILE_MISMATCH"));
        }
        let mut file = parent.open_pinned_read(&name)?;
        if entry.role == RuntimeFileRole::NativeBinary {
            native_binary(&mut file, manifest.runtime.os, manifest.runtime.arch)?;
            file.seek(SeekFrom::Start(0))?;
        }
        let mut hash = Sha256::new();
        let mut size = 0u64;
        let mut block = [0u8; 65536];
        loop {
            budget.check()?;
            let count = file.read(&mut block)?;
            if count == 0 {
                break;
            }
            size += count as u64;
            if size > entry.size_bytes {
                return Err(RacpError::new("RUNTIME_FILE_MISMATCH"));
            }
            hash.update(&block[..count]);
        }
        if size != entry.size_bytes
            || !format!("{:x}", hash.finalize()).eq_ignore_ascii_case(&entry.sha256)
            || parent.require_file(&name)?.revision() != metadata.revision()
        {
            return Err(RacpError::new("RUNTIME_FILE_MISMATCH"));
        }
        pins.push(file);
        parents.push(parent);
    }
    Ok(PinnedRuntime {
        entrypoint: path.join(&manifest.entrypoint),
        manifest,
        _guards: guard,
        _parents: parents,
        _files: pins,
    })
}
pub fn chromium(
    root: &Path,
    config: &NativeRuntimeConfig,
    budget: &AdmissionBudget,
) -> Result<Option<PathBuf>, RacpError> {
    config.validate()?;
    let Some(id) = config.chromium_manifest.as_ref() else {
        return Ok(None);
    };
    let manifest = verify(root, id, budget)?;
    if manifest.runtime.id != RuntimeId::Chromium {
        return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
    }
    Ok(Some(
        root.join("runtimes")
            .join(id.to_ascii_lowercase())
            .join(manifest.entrypoint),
    ))
}
fn native_binary(
    file: &mut std::fs::File,
    os: RuntimeOs,
    arch: RuntimeArch,
) -> Result<(), RacpError> {
    let mut header = [0u8; 64];
    file.read_exact(&mut header)
        .map_err(|_| RacpError::new("RUNTIME_BINARY_INVALID"))?;
    let matched = match os {
        RuntimeOs::Windows => {
            if &header[..2] != b"MZ" {
                return Err(RacpError::new("RUNTIME_BINARY_INVALID"));
            }
            let offset = u32::from_le_bytes(header[60..64].try_into().unwrap());
            if !(64..=1048576).contains(&offset) {
                return Err(RacpError::new("RUNTIME_BINARY_INVALID"));
            }
            file.seek(SeekFrom::Start(offset as u64))?;
            let mut pe = [0u8; 26];
            file.read_exact(&mut pe)
                .map_err(|_| RacpError::new("RUNTIME_BINARY_INVALID"))?;
            let machine = u16::from_le_bytes(pe[4..6].try_into().unwrap());
            &pe[..4] == b"PE\0\0"
                && u16::from_le_bytes(pe[24..26].try_into().unwrap()) == 0x20b
                && machine
                    == if arch == RuntimeArch::X64 {
                        0x8664
                    } else {
                        0xaa64
                    }
        }
        RuntimeOs::Linux => {
            &header[..4] == b"\x7fELF"
                && header[4] == 2
                && header[5] == 1
                && u16::from_le_bytes(header[18..20].try_into().unwrap())
                    == if arch == RuntimeArch::X64 { 62 } else { 183 }
        }
        RuntimeOs::Macos => {
            u32::from_le_bytes(header[..4].try_into().unwrap()) == 0xfeedfacf
                && u32::from_le_bytes(header[4..8].try_into().unwrap())
                    == if arch == RuntimeArch::X64 {
                        0x01000007
                    } else {
                        0x0100000c
                    }
        }
    };
    if matched {
        Ok(())
    } else {
        Err(RacpError::new("RUNTIME_PLATFORM_MISMATCH"))
    }
}
pub fn validate_host_binary(file: &mut std::fs::File) -> Result<(), RacpError> {
    let (os, arch) = host();
    native_binary(file, os, arch)
}
