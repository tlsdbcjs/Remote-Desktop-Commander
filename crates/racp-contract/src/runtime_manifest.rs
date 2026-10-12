//! Admission metadata for optional tools. Recipes are fixed IDs; remote paths/argv are forbidden.
use crate::RacpError;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeManifest {
    pub id: RuntimeId,
    pub version: String,
    pub os: RuntimeOs,
    pub arch: RuntimeArch,
    pub sha256: String,
    pub size_bytes: u64,
    pub source: String,
    pub license: String,
    pub recipe: RuntimeRecipe,
}
/// A directory package copied only after explicit local consent to the complete manifest hash.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeBundleManifest {
    pub format_version: u32,
    pub runtime: RuntimeManifest,
    pub entrypoint: String,
    pub files: Vec<RuntimeFile>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeFile {
    pub path: String,
    pub sha256: String,
    pub size_bytes: u64,
    pub role: RuntimeFileRole,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeFileRole {
    NativeBinary,
    Data,
    License,
}
pub fn relative_runtime_path(path: &str) -> bool {
    if path.is_empty()
        || path.len() > 2048
        || path.starts_with('/')
        || path.contains(['\\', ':', '\0'])
    {
        return false;
    }
    path.split('/').all(|part| {
        if part.is_empty()
            || part == "."
            || part == ".."
            || part.ends_with(['.', ' '])
            || part
                .chars()
                .any(|c| c.is_control() || "<>\"|?*".contains(c))
        {
            return false;
        }
        let device = part.split('.').next().unwrap_or("").to_ascii_uppercase();
        !["CON", "PRN", "AUX", "NUL"].contains(&device.as_str())
            && !(device.len() == 4
                && (device.starts_with("COM") || device.starts_with("LPT"))
                && matches!(device.as_bytes()[3], b'1'..=b'9'))
    })
}
impl RuntimeBundleManifest {
    pub fn validate(&self, os: RuntimeOs, arch: RuntimeArch) -> Result<(), RacpError> {
        self.runtime.validate(os, arch)?;
        if self.format_version != 1
            || !relative_runtime_path(&self.entrypoint)
            || self.files.is_empty()
            || self.files.len() > 4096
        {
            return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
        }
        let mut paths = std::collections::BTreeSet::new();
        let mut total = 0u64;
        let mut license = false;
        for file in &self.files {
            if !relative_runtime_path(&file.path)
                || ["admitted-manifest.json", "admission.pending"]
                    .contains(&file.path.to_ascii_lowercase().as_str())
                || !paths.insert(file.path.to_ascii_lowercase())
                || file.sha256.len() != 64
                || !file.sha256.bytes().all(|v| v.is_ascii_hexdigit())
                || file.size_bytes > 2 * 1024 * 1024 * 1024
            {
                return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
            }
            total = total
                .checked_add(file.size_bytes)
                .filter(|v| *v <= 4 * 1024 * 1024 * 1024)
                .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
            license |= file.role == RuntimeFileRole::License && file.size_bytes > 0;
            let lower = file.path.to_ascii_lowercase();
            if (lower.ends_with(".exe")
                || lower.ends_with(".dll")
                || lower.ends_with(".so")
                || lower.ends_with(".dylib"))
                && file.role != RuntimeFileRole::NativeBinary
            {
                return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
            }
        }
        let entry = self
            .files
            .iter()
            .find(|file| file.path == self.entrypoint)
            .ok_or_else(|| RacpError::new("RUNTIME_MANIFEST_INVALID"))?;
        for file in &self.files {
            let mut parent = std::path::Path::new(&file.path).parent();
            while let Some(path) = parent {
                if paths.contains(&path.to_string_lossy().to_ascii_lowercase()) {
                    return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
                }
                parent = path.parent();
            }
        }
        if !license
            || entry.role != RuntimeFileRole::NativeBinary
            || entry.size_bytes == 0
            || !entry.sha256.eq_ignore_ascii_case(&self.runtime.sha256)
            || entry.size_bytes != self.runtime.size_bytes
            || os == RuntimeOs::Windows && !self.entrypoint.to_ascii_lowercase().ends_with(".exe")
        {
            return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
        }
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeId {
    Chromium,
    Cdb,
    Gdb,
    PacketDriver,
    Ghidra,
    Proxy,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeOs {
    Windows,
    Macos,
    Linux,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeArch {
    X64,
    Arm64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeRecipe {
    IsolatedCdp,
    NativeDebugger,
    PacketCapture,
    HostAnalysis,
    HttpProxy,
}
impl RuntimeManifest {
    pub fn validate(&self, os: RuntimeOs, arch: RuntimeArch) -> Result<(), RacpError> {
        if self.os != os || self.arch != arch {
            return Err(RacpError::new("RUNTIME_PLATFORM_MISMATCH"));
        }
        let source = url::Url::parse(&self.source)
            .map_err(|_| RacpError::new("RUNTIME_MANIFEST_INVALID"))?;
        let recipe = match self.id {
            RuntimeId::Chromium => RuntimeRecipe::IsolatedCdp,
            RuntimeId::Cdb | RuntimeId::Gdb => RuntimeRecipe::NativeDebugger,
            RuntimeId::PacketDriver => RuntimeRecipe::PacketCapture,
            RuntimeId::Ghidra => RuntimeRecipe::HostAnalysis,
            RuntimeId::Proxy => RuntimeRecipe::HttpProxy,
        };
        if self.sha256.len() != 64
            || !self.sha256.bytes().all(|v| v.is_ascii_hexdigit())
            || self.size_bytes == 0
            || self.size_bytes > 4 * 1024 * 1024 * 1024
            || self.version.is_empty()
            || self.version.len() > 128
            || self.license.is_empty()
            || self.license.len() > 256
            || self.recipe != recipe
            || self.source.len() > 2048
            || source.scheme() != "https"
            || source.host_str().is_none()
            || !source.username().is_empty()
            || source.password().is_some()
            || source.query().is_some()
            || source.fragment().is_some()
        {
            return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
        }
        Ok(())
    }
}
