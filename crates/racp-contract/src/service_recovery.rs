//! Bounded crash recovery, independent of SCM. Intentional/reported stops stay stopped.
use crate::RacpError;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum RecoveryAction {
    Restart { delay_ms: u32 },
    None {},
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServiceRecoveryPolicy {
    pub format_version: u32,
    pub reset_after_seconds: u32,
    pub on_non_crash_failures: bool,
    pub actions: Vec<RecoveryAction>,
}

impl Default for ServiceRecoveryPolicy {
    fn default() -> Self {
        Self {
            format_version: 1,
            reset_after_seconds: 86400,
            on_non_crash_failures: false,
            actions: vec![RecoveryAction::None {}],
        }
    }
}

impl ServiceRecoveryPolicy {
    pub fn bounded_crash_only() -> Self {
        Self {
            format_version: 1,
            reset_after_seconds: 86400,
            on_non_crash_failures: false,
            actions: vec![
                RecoveryAction::Restart { delay_ms: 5000 },
                RecoveryAction::Restart { delay_ms: 30000 },
                RecoveryAction::Restart { delay_ms: 120000 },
                RecoveryAction::None {},
            ],
        }
    }

    pub fn validate(&self) -> Result<(), RacpError> {
        let invalid = || RacpError::new("SERVICE_RECOVERY_POLICY_INVALID");
        if self.format_version != 1
            || self.on_non_crash_failures
            || !(3600..=604800).contains(&self.reset_after_seconds)
            || !(1..=4).contains(&self.actions.len())
            || self.actions.last() != Some(&RecoveryAction::None {})
        {
            return Err(invalid());
        }
        let mut previous = 0;
        for action in &self.actions[..self.actions.len() - 1] {
            let RecoveryAction::Restart { delay_ms } = action else {
                return Err(invalid());
            };
            if *delay_ms <= previous || *delay_ms > 3600000 {
                return Err(invalid());
            }
            previous = *delay_ms;
        }
        Ok(())
    }
}
