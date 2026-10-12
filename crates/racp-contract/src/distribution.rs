//! Native base payload only. Optional runtimes and local state are never admitted here.
use crate::RacpError;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
pub const MANIFEST_FILE: &str = "payload-manifest.json";
pub const BASE_FILES: [&str; 4] = ["LICENSE", "racp-agent.exe", "racp-client.exe", "sbom.json"];
pub const MAX_PAYLOAD_BYTES: u64 = 1024 * 1024 * 1024;
pub const PORTABLE_MAGIC: &[u8; 16] = b"RACPNATIVE000001";
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PayloadFile {
    pub path: String,
    pub size_bytes: u64,
    pub sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativePayload {
    pub format_version: u32,
    pub product_version: String,
    pub platform: String,
    pub architecture: String,
    pub files: Vec<PayloadFile>,
}
impl NativePayload {
    pub fn validate(&self) -> Result<(), RacpError> {
        let invalid = || RacpError::new("PACKAGE_INVALID");
        if self.format_version != 1
            || self.product_version != crate::VERSION
            || self.platform != "win"
            || self.architecture != "x64"
            || self.files.len() != BASE_FILES.len()
        {
            return Err(invalid());
        }
        let mut names = BTreeSet::new();
        let mut size = 0u64;
        for file in &self.files {
            if !BASE_FILES.contains(&file.path.as_str())
                || !names.insert(&file.path)
                || file.size_bytes == 0
                || file.size_bytes > MAX_PAYLOAD_BYTES / 2
                || file.sha256.len() != 64
                || !file
                    .sha256
                    .bytes()
                    .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
            {
                return Err(invalid());
            }
            size = size.checked_add(file.size_bytes).ok_or_else(invalid)?;
        }
        if size > MAX_PAYLOAD_BYTES {
            return Err(invalid());
        }
        Ok(())
    }
}
