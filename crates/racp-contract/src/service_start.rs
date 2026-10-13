//! Durable administrative start identity. SCM arguments carry only the intent ID.
use crate::RacpError;
use serde::{Deserialize, Serialize};

pub fn id(value: &str, prefix: &str) -> bool {
    value.strip_prefix(prefix).is_some_and(|tail| {
        tail.len() == 32
            && tail
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
pub fn hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MaintenancePurpose {
    Remove,
    Restore,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum StartCommand {
    Configure {
        input_sha256: String,
    },
    Reconcile {},
    RecoverSessions {
        request_id: String,
    },
    Maintenance {
        purpose: MaintenancePurpose,
        request_id: String,
    },
}
impl StartCommand {
    pub fn validate(&self) -> Result<(), RacpError> {
        let valid = match self {
            Self::Configure { input_sha256 } => hash(input_sha256),
            Self::Reconcile {} => true,
            Self::RecoverSessions { request_id } => id(request_id, "session_recovery_"),
            Self::Maintenance { request_id, .. } => id(request_id, "maintenance_"),
        };
        if valid {
            Ok(())
        } else {
            Err(RacpError::new("SERVICE_START_INTENT_INVALID"))
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum StartPhase {
    Prepared {},
    Claimed {
        attempt_id: String,
    },
    Completed {
        attempt_id: String,
        receipt_sha256: String,
    },
    Failed {
        attempt_id: String,
        error_code: String,
    },
    Acknowledged {
        attempt_id: String,
        receipt_sha256: String,
    },
    Aborted {},
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StartIntent {
    pub format_version: u32,
    pub root_identity: String,
    pub request_id: String,
    pub command: StartCommand,
    pub phase: StartPhase,
}
impl StartIntent {
    pub fn validate(&self) -> Result<(), RacpError> {
        self.command.validate()?;
        let valid_root = self.root_identity.split_once(':').is_some_and(|(a, b)| {
            [a, b].iter().all(|part| {
                !part.is_empty()
                    && part.len() <= 16
                    && part
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
        });
        let valid_phase = match &self.phase {
            StartPhase::Prepared {} | StartPhase::Aborted {} => true,
            StartPhase::Claimed { attempt_id } => id(attempt_id, "service_attempt_"),
            StartPhase::Completed {
                attempt_id,
                receipt_sha256,
            }
            | StartPhase::Acknowledged {
                attempt_id,
                receipt_sha256,
            } => id(attempt_id, "service_attempt_") && hash(receipt_sha256),
            StartPhase::Failed {
                attempt_id,
                error_code,
            } => id(attempt_id, "service_attempt_") && error_code_valid(error_code),
        };
        if self.format_version != 1
            || !valid_root
            || !id(&self.request_id, "service_start_")
            || !valid_phase
        {
            return Err(RacpError::new("SERVICE_START_INTENT_INVALID"));
        }
        Ok(())
    }
    pub fn permits_normal_run(&self) -> bool {
        matches!(
            self.phase,
            StartPhase::Acknowledged { .. } | StartPhase::Aborted {}
        )
    }
}
pub fn error_code_valid(code: &str) -> bool {
    !code.is_empty()
        && code.len() <= 96
        && code
            .bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
}
