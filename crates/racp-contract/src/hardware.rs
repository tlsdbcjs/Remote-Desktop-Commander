//! External hardware identity is separate from the Agent's registered PC identity.
//! No OS path, raw handle, driver change or unrestricted transfer is a wire input.
use crate::RacpError;
use serde::{Deserialize, Serialize};

pub const MAX_HARDWARE_INTERFACES: usize = 256;
pub const MAX_HARDWARE_SESSIONS: usize = 32;
pub const MAX_HARDWARE_LEASE_MS: u64 = 300_000;
pub const MAX_HARDWARE_IO_BYTES: u64 = 16 * 1024 * 1024;

pub fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
}
pub fn opaque_id(value: &str, prefix: &str) -> bool {
    value.strip_prefix(prefix).is_some_and(|tail| {
        tail.len() == 32
            && tail
                .bytes()
                .all(|b| b.is_ascii_digit() || matches!(b, b'a'..=b'f'))
    })
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareTarget {
    pub agent_device_id: String,
    pub agent_boot_id: String,
    pub hardware_id: String,
    pub interface_id: String,
    pub connection_generation: String,
}
impl HardwareTarget {
    pub fn validate(&self) -> Result<(), RacpError> {
        if !identifier(&self.agent_device_id)
            || !identifier(&self.agent_boot_id)
            || !opaque_id(&self.hardware_id, "hardware_")
            || !opaque_id(&self.interface_id, "hwif_")
            || !opaque_id(&self.connection_generation, "connection_")
        {
            return Err(RacpError::new("HARDWARE_TARGET_INVALID"));
        }
        Ok(())
    }
}
/// Trusted dispatch context; callers never select their own principal in an RPC payload.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HardwareCaller {
    pub agent_device_id: String,
    pub agent_boot_id: String,
    pub principal_id: String,
    pub workspace_id: String,
}
impl HardwareCaller {
    pub fn validate(&self) -> Result<(), RacpError> {
        if [
            &self.agent_device_id,
            &self.agent_boot_id,
            &self.principal_id,
            &self.workspace_id,
        ]
        .into_iter()
        .any(|s| !identifier(s))
        {
            return Err(RacpError::new("HARDWARE_CALLER_INVALID"));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareLease {
    pub session_id: String,
    pub lease_id: String,
    pub target: HardwareTarget,
}
impl HardwareLease {
    pub fn validate(&self) -> Result<(), RacpError> {
        self.target.validate()?;
        if !opaque_id(&self.session_id, "hwsession_") || !opaque_id(&self.lease_id, "hwlease_") {
            return Err(RacpError::new("HARDWARE_LEASE_INVALID"));
        }
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HardwarePhase {
    Reserved,
    Active,
    Closing,
    Closed,
    Unknown,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CollectionKind {
    DescriptorSnapshot,
    AssociatedFile,
    DocumentedReadProtocol,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PartialCollectionReason {
    UnsupportedField,
    ReadLimitReached,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareReadPlan {
    pub kind: CollectionKind,
    pub protocol_id: Option<String>,
    pub method_revision: String,
}
impl HardwareReadPlan {
    pub fn validate(&self) -> Result<(), RacpError> {
        if !identifier(&self.method_revision)
            || match self.kind {
                CollectionKind::DocumentedReadProtocol => {
                    self.protocol_id.as_deref().is_none_or(|s| !identifier(s))
                }
                _ => self.protocol_id.is_some(),
            }
        {
            return Err(RacpError::new("HARDWARE_READ_PLAN_INVALID"));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareCollectionManifest {
    pub version: u32,
    pub target: HardwareTarget,
    pub kind: CollectionKind,
    pub protocol_id: Option<String>,
    pub method_revision: String,
    pub captured_at: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub partial: bool,
    pub partial_reason: Option<PartialCollectionReason>,
}
impl HardwareCollectionManifest {
    pub fn validate(&self) -> Result<(), RacpError> {
        self.target.validate()?;
        if self.version != 1
            || !identifier(&self.method_revision)
            || self.captured_at.len() > 64
            || chrono::DateTime::parse_from_rfc3339(&self.captured_at).is_err()
            || self.size_bytes > MAX_HARDWARE_IO_BYTES
            || self.sha256.len() != 64
            || !self
                .sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || matches!(b, b'a'..=b'f'))
            || self.partial != self.partial_reason.is_some()
            || match self.kind {
                CollectionKind::DocumentedReadProtocol => {
                    self.protocol_id.as_deref().is_none_or(|s| !identifier(s))
                }
                _ => self.protocol_id.is_some(),
            }
        {
            return Err(RacpError::new("HARDWARE_COLLECTION_INVALID"));
        }
        Ok(())
    }
    pub fn validate_bytes(&self, bytes: &[u8]) -> Result<(), RacpError> {
        self.validate()?;
        if bytes.len() as u64 != self.size_bytes || crate::digest(bytes) != self.sha256 {
            return Err(RacpError::new("CHECKSUM_MISMATCH"));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareIoBudget {
    pub timeout_ms: u64,
    pub max_bytes: u64,
}
impl HardwareIoBudget {
    pub fn validate(&self) -> Result<(), RacpError> {
        if !(1..=30_000).contains(&self.timeout_ms)
            || !(1..=MAX_HARDWARE_IO_BYTES).contains(&self.max_bytes)
        {
            return Err(RacpError::new("HARDWARE_IO_BUDGET_INVALID"));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareIoRequest {
    pub operation_id: String,
    pub budget: HardwareIoBudget,
    pub plan: HardwareReadPlan,
}
impl HardwareIoRequest {
    pub fn validate(&self) -> Result<(), RacpError> {
        self.budget.validate()?;
        self.plan.validate()?;
        if !opaque_id(&self.operation_id, "op_") {
            return Err(RacpError::new("HARDWARE_IO_SCOPE_INVALID"));
        }
        Ok(())
    }
}
