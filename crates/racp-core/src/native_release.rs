//! Monotonic native storage version watermark; no automatic downgrade or schema conversion.
use crate::{atomic_write, read_bounded, state_schema::STATE_SCHEMA_VERSION};
use racp_contract::{local_ipc::reject_duplicate_keys, RacpError, VERSION};
use serde::{Deserialize, Serialize};
use std::path::Path;
const FILE: &str = "native-release.json";
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Release {
    format_version: u8,
    version: String,
    state_schema: u32,
    policy_schema: u32,
    journal_schema: u32,
}
fn version(value: &str) -> Result<(u32, u32, u32), RacpError> {
    let parts: Vec<_> = value.split('.').collect();
    if parts.len() != 3
        || parts.iter().any(|part| {
            part.is_empty()
                || part.len() > 10
                || !part.bytes().all(|byte| byte.is_ascii_digit())
                || (part.len() > 1 && part.starts_with('0'))
        })
    {
        return Err(RacpError::new("NATIVE_RELEASE_INVALID"));
    }
    let parse = |part: &str| {
        part.parse::<u32>()
            .map_err(|_| RacpError::new("NATIVE_RELEASE_INVALID"))
    };
    Ok((parse(parts[0])?, parse(parts[1])?, parse(parts[2])?))
}
pub fn assert_compatible(root: &Path) -> Result<(), RacpError> {
    if !root.join(FILE).try_exists()? {
        return Ok(());
    }
    let raw = read_bounded(&root.join(FILE), 4096, true)?;
    reject_duplicate_keys(&raw)?;
    let release: Release = serde_json::from_slice(&raw)?;
    if release.format_version != 1
        || release.state_schema != STATE_SCHEMA_VERSION
        || release.policy_schema != STATE_SCHEMA_VERSION
        || release.journal_schema != crate::journal_schema::VERSION as u32
    {
        return Err(RacpError::new("NATIVE_RELEASE_UNSUPPORTED"));
    }
    if version(VERSION)? < version(&release.version)? {
        return Err(RacpError::new("NATIVE_DOWNGRADE_NOT_SUPPORTED"));
    }
    Ok(())
}
/// Registration/Agent startup hold their existing namespace/Agent lock around this commit.
pub fn publish(root: &Path) -> Result<(), RacpError> {
    assert_compatible(root)?;
    let release = Release {
        format_version: 1,
        version: VERSION.into(),
        state_schema: STATE_SCHEMA_VERSION,
        policy_schema: STATE_SCHEMA_VERSION,
        journal_schema: crate::journal_schema::VERSION as u32,
    };
    atomic_write(&root.join(FILE), &serde_json::to_vec(&release)?, true)
}
