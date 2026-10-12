//! Versioned, bounded local control contract. Contains no credential or RPC payload fields.
use crate::RacpError;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const PROTOCOL: u16 = 1;
pub const MAX_FRAME_BYTES: usize = 64 * 1024;
pub const MAX_IN_FLIGHT: usize = 16;
pub const MAX_RECEIPT_TIMEOUT_MS: u32 = 5_000;
pub const EVENT_QUEUE_CAPACITY: usize = 256;
pub const MAX_RECENT_CONTROL_RESULTS: usize = 16;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControlRequest {
    pub protocol: u16,
    pub request_id: String,
    /// Relative receipt budget, not an operation deadline or approval TTL.
    pub deadline_ms: u32,
    pub command: ControlCommand,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ControlCommand {
    RemoveDesktop {
        boot_id: String,
        pair_id: String,
    },
    PrepareDesktop {
        boot_id: String,
    },
    RegisterDesktop {
        boot_id: String,
        pair_id: String,
        broker_pid: u32,
        broker_created: f64,
        guardian_pid: u32,
        guardian_created: f64,
    },
    Snapshot,
    Subscribe {
        boot_id: String,
        after_sequence: u64,
    },
    UpdatePolicy {
        expected_revision: u64,
        changes: PolicyChanges,
    },
    DecideApproval {
        nonce: String,
        allow: bool,
    },
    /// Local owner control; binds a displayed execution, never an arbitrary PID.
    CancelOperation {
        boot_id: String,
        operation_id: String,
        target_request_id: String,
    },
    Stop,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Grant {
    Deny,
    Allow,
    RequireApproval,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PolicyChanges {
    pub grants: BTreeMap<String, Grant>,
    pub disabled_categories: Option<Vec<String>>,
    pub constraints: Option<PolicyConstraints>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PolicyConstraints {
    pub workspace_ids: Vec<String>,
    pub max_timeout_ms: u64,
    pub max_output_bytes: u64,
    pub executable_allowlist: Vec<String>,
    pub executable_denylist: Vec<String>,
    pub strict_os_isolation: bool,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentStatus {
    Unregistered,
    Offline,
    Connecting,
    Online,
    Draining,
    Stopped,
    Failed,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AvailabilityState {
    Supported,
    NeedsPermission,
    NeedsRuntime,
    UnsupportedOs,
    UnsupportedProtectedTarget,
    NotImplemented,
    Unverified,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Availability {
    pub state: AvailabilityState,
    /// Fixed reason code, never an arbitrary OS diagnostic string.
    pub reason: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ActivitySummary {
    pub request_id: String,
    #[serde(default)]
    pub operation_id: String,
    pub operation: String,
    pub state: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateSnapshot {
    pub boot_id: String,
    pub sequence: u64,
    pub policy_revision: u64,
    pub status: AgentStatus,
    pub activity: Vec<ActivitySummary>,
    pub availability: BTreeMap<String, Availability>,
    #[serde(default)]
    pub policy: Option<PolicyView>,
    #[serde(default)]
    pub pending_approvals: Vec<ApprovalPrompt>,
    /// Bounded, same-boot completion history for reconnects; not crash-durable.
    #[serde(default)]
    pub recent_control_results: Vec<ControlCompletion>,
    #[serde(default)]
    pub identity: Option<AgentIdentity>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentIdentity {
    pub device_id: String,
    pub gateway_origin: String,
    pub execution_scope: ExecutionScope,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionScope {
    User,
    Service,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ControlCompletion {
    Cancellation {
        receipt_id: String,
        operation_id: String,
        cancel_requested: bool,
        state: String,
        terminal: bool,
        cleanup_status: String,
        error_code: Option<String>,
    },
    Policy {
        receipt_id: String,
        revision: u64,
        committed: bool,
        retirement_state: String,
        failed_resource_ids: Vec<String>,
    },
    Stop {
        receipt_id: String,
        cleanup_state: String,
        failed_resource_ids: Vec<String>,
    },
}
impl ControlCompletion {
    pub fn from_change(change: &StateChange) -> Option<Self> {
        match change {
            StateChange::CancelFinished {
                receipt_id,
                operation_id,
                cancel_requested,
                state,
                terminal,
                cleanup_status,
                error_code,
            } => Some(Self::Cancellation {
                receipt_id: receipt_id.clone(),
                operation_id: operation_id.clone(),
                cancel_requested: *cancel_requested,
                state: state.clone(),
                terminal: *terminal,
                cleanup_status: cleanup_status.clone(),
                error_code: error_code.clone(),
            }),
            StateChange::PolicyFinished {
                receipt_id,
                revision,
                committed,
                retirement_state,
                failed_resource_ids,
            } => Some(Self::Policy {
                receipt_id: receipt_id.clone(),
                revision: *revision,
                committed: *committed,
                retirement_state: retirement_state.clone(),
                failed_resource_ids: failed_resource_ids.clone(),
            }),
            StateChange::StopFinished {
                receipt_id,
                cleanup_state,
                failed_resource_ids,
            } => Some(Self::Stop {
                receipt_id: receipt_id.clone(),
                cleanup_state: cleanup_state.clone(),
                failed_resource_ids: failed_resource_ids.clone(),
            }),
            _ => None,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PolicyView {
    pub revision: u64,
    pub grants: BTreeMap<String, Grant>,
    pub disabled_categories: Vec<String>,
    pub constraints: PolicyConstraints,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApprovalPrompt {
    pub nonce: String,
    pub request_id: String,
    pub operation: String,
    pub target_digest: String,
    pub expires_at: String,
    pub target_summary: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateEvent {
    pub boot_id: String,
    pub sequence: u64,
    pub policy_revision: u64,
    pub change: StateChange,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum StateChange {
    /// Observation after bounded cancellation wait, not a promise of target cleanup.
    CancelFinished {
        receipt_id: String,
        operation_id: String,
        cancel_requested: bool,
        state: String,
        terminal: bool,
        cleanup_status: String,
        error_code: Option<String>,
    },
    Status {
        status: AgentStatus,
    },
    Activity {
        activity: Vec<ActivitySummary>,
    },
    Availability {
        availability: BTreeMap<String, Availability>,
    },
    PolicyCommitted {
        policy: PolicyView,
    },
    PolicyFinished {
        receipt_id: String,
        revision: u64,
        committed: bool,
        retirement_state: String,
        failed_resource_ids: Vec<String>,
    },
    StopFinished {
        receipt_id: String,
        cleanup_state: String,
        failed_resource_ids: Vec<String>,
    },
    Retirement {
        failed_resource_ids: Vec<String>,
    },
    ApprovalPending {
        nonce: String,
        request_id: String,
        operation: String,
        target_digest: String,
        expires_at: String,
        target_summary: String,
    },
    ApprovalFinished {
        nonce: String,
        state: String,
    },
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControlReply {
    pub request_id: String,
    pub result: ControlResult,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ControlResult {
    DesktopRemoved {
        pair_id: String,
    },
    DesktopPrepared {
        seed: DesktopSeed,
    },
    DesktopRegistered {
        pair_id: String,
        session_id: u32,
    },
    Snapshot {
        snapshot: StateSnapshot,
        role: crate::service_scope::ControllerRole,
    },
    Subscribed {
        snapshot: StateSnapshot,
        role: crate::service_scope::ControllerRole,
    },
    Accepted {
        receipt_id: String,
    },
    PolicyReceipt {
        revision: u64,
        committed: bool,
        retirement_state: String,
        failed_resource_ids: Vec<String>,
    },
    Error {
        code: ControlError,
    },
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DesktopSeed {
    pub pair_id: String,
    pub root: String,
    pub secret: String,
    pub device_id: String,
    pub agent_pid: u32,
    pub agent_created: f64,
    pub agent_sid: String,
    pub user_sid: String,
    pub session_id: u32,
}
impl std::fmt::Debug for DesktopSeed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DesktopSeed")
            .field("pair_id", &self.pair_id)
            .field("session_id", &self.session_id)
            .field("secret", &"[REDACTED]")
            .finish_non_exhaustive()
    }
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ControlError {
    InvalidRequest,
    ProtocolMismatch,
    PermissionDenied,
    StaleConnection,
    OperationNotFound,
    DeviceOffline,
    RevisionConflict,
    ResourceExhausted,
    RequestTimeout,
    ResyncRequired,
    NotImplemented,
}
fn identifier(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
}
impl ControlRequest {
    pub fn validate(&self) -> Result<(), RacpError> {
        if self.protocol != PROTOCOL {
            return Err(RacpError::new("PROTOCOL_MISMATCH"));
        }
        if !identifier(&self.request_id)
            || !(1..=MAX_RECEIPT_TIMEOUT_MS).contains(&self.deadline_ms)
        {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        match &self.command {
            ControlCommand::RemoveDesktop { boot_id, pair_id }
                if !identifier(boot_id)
                    || pair_id.len() != 32
                    || !pair_id
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) =>
            {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
            ControlCommand::PrepareDesktop { boot_id } if !identifier(boot_id) => {
                return Err(RacpError::new("REQUEST_INVALID"))
            }
            ControlCommand::RegisterDesktop {
                boot_id,
                pair_id,
                broker_pid,
                broker_created,
                guardian_pid,
                guardian_created,
            } if !identifier(boot_id)
                || pair_id.len() != 32
                || !pair_id
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                || *broker_pid == 0
                || *guardian_pid == 0
                || broker_pid == guardian_pid
                || !broker_created.is_finite()
                || !guardian_created.is_finite()
                || *broker_created <= 0.0
                || *guardian_created <= 0.0 =>
            {
                return Err(RacpError::new("REQUEST_INVALID"))
            }
            ControlCommand::Subscribe { boot_id, .. } if !identifier(boot_id) => {
                return Err(RacpError::new("REQUEST_INVALID"))
            }
            ControlCommand::CancelOperation {
                boot_id,
                operation_id,
                target_request_id,
            } if !identifier(boot_id)
                || !identifier(operation_id)
                || !identifier(target_request_id) =>
            {
                return Err(RacpError::new("REQUEST_INVALID"))
            }
            ControlCommand::DecideApproval { nonce, .. }
                if nonce.len() != 64 || !nonce.bytes().all(|v| v.is_ascii_hexdigit()) =>
            {
                return Err(RacpError::new("REQUEST_INVALID"))
            }
            ControlCommand::UpdatePolicy { changes, .. } => {
                if changes.grants.len() > 143
                    || changes.grants.keys().any(|id| !identifier(id))
                    || changes
                        .disabled_categories
                        .as_ref()
                        .is_some_and(|v| v.len() > 18 || v.iter().any(|s| !identifier(s)))
                {
                    return Err(RacpError::new("REQUEST_INVALID"));
                }
            }
            _ => (),
        }
        Ok(())
    }
}
pub fn encode<T: Serialize>(value: &T) -> Result<Vec<u8>, RacpError> {
    let raw = serde_json::to_vec(value)?;
    if raw.is_empty() || raw.len() > MAX_FRAME_BYTES {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    Ok(raw)
}
pub fn decode_request(raw: &[u8]) -> Result<ControlRequest, RacpError> {
    if raw.is_empty() || raw.len() > MAX_FRAME_BYTES {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    // Struct fields reject duplicates. Maps are checked recursively before typed decoding.
    reject_duplicate_keys(raw)?;
    let request: ControlRequest = serde_json::from_slice(raw)?;
    request.validate()?;
    Ok(request)
}
pub fn reject_duplicate_keys(raw: &[u8]) -> Result<(), RacpError> {
    use serde::de::{MapAccess, SeqAccess, Visitor};
    struct Unique;
    impl<'de> Deserialize<'de> for Unique {
        fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
            d.deserialize_any(Self)
        }
    }
    impl<'de> Visitor<'de> for Unique {
        type Value = Unique;
        fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
            f.write_str("JSON with unique keys")
        }
        fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Unique, M::Error> {
            let mut seen = std::collections::BTreeSet::new();
            while let Some(key) = map.next_key::<String>()? {
                if !seen.insert(key) {
                    return Err(serde::de::Error::custom("duplicate key"));
                }
                map.next_value::<Unique>()?;
            }
            Ok(Unique)
        }
        fn visit_seq<S: SeqAccess<'de>>(self, mut seq: S) -> Result<Unique, S::Error> {
            while seq.next_element::<Unique>()?.is_some() {}
            Ok(Unique)
        }
        fn visit_bool<E: serde::de::Error>(self, _: bool) -> Result<Unique, E> {
            Ok(Unique)
        }
        fn visit_i64<E: serde::de::Error>(self, _: i64) -> Result<Unique, E> {
            Ok(Unique)
        }
        fn visit_u64<E: serde::de::Error>(self, _: u64) -> Result<Unique, E> {
            Ok(Unique)
        }
        fn visit_f64<E: serde::de::Error>(self, _: f64) -> Result<Unique, E> {
            Ok(Unique)
        }
        fn visit_str<E: serde::de::Error>(self, _: &str) -> Result<Unique, E> {
            Ok(Unique)
        }
        fn visit_unit<E: serde::de::Error>(self) -> Result<Unique, E> {
            Ok(Unique)
        }
    }
    let mut d = serde_json::Deserializer::from_slice(raw);
    Unique::deserialize(&mut d)?;
    d.end()?;
    Ok(())
}
