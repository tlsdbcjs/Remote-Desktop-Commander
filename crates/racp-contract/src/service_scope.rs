//! Native service controller scope. No enrollment tokens, passwords or RPC payloads.
use crate::RacpError;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
pub const SERVICE_NAME: &str = "RACPNativeAgent";
pub const SERVICE_ACCOUNT: &str = "NT SERVICE\\RACPNativeAgent";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ControllerRole {
    Observer,
    Controller,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServiceController {
    pub user_sid: String,
    pub session_ids: Vec<u32>,
    pub role: ControllerRole,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServiceScope {
    pub service_name: String,
    pub service_sid: String,
    pub controllers: Vec<ServiceController>,
}
impl ServiceScope {
    pub fn validate(&self) -> Result<(), RacpError> {
        let invalid = || RacpError::new("SERVICE_SCOPE_INVALID");
        if self.service_name != SERVICE_NAME
            || !service_sid(&self.service_sid)
            || self.controllers.is_empty()
            || self.controllers.len() > 16
        {
            return Err(invalid());
        }
        let mut users = BTreeSet::new();
        for controller in &self.controllers {
            let sessions: BTreeSet<_> = controller.session_ids.iter().copied().collect();
            if !interactive_user_sid(&controller.user_sid)
                || !users.insert(&controller.user_sid)
                || sessions.is_empty()
                || sessions.len() > 16
                || sessions.contains(&0)
                || sessions.len() != controller.session_ids.len()
            {
                return Err(invalid());
            }
        }
        Ok(())
    }
    pub fn role(&self, sid: &str, session: u32) -> Option<ControllerRole> {
        self.controllers
            .iter()
            .find(|entry| entry.user_sid == sid && entry.session_ids.contains(&session))
            .map(|entry| entry.role)
    }
}
fn components(value: &str, prefix: &str, count: usize) -> bool {
    let Some(tail) = value.strip_prefix(prefix) else {
        return false;
    };
    let parts: Vec<_> = tail.split('-').collect();
    parts.len() == count
        && parts.iter().all(|part| {
            !part.is_empty()
                && part.bytes().all(|v| v.is_ascii_digit())
                && part.parse::<u32>().is_ok_and(|n| n.to_string() == *part)
        })
}
pub fn service_sid(value: &str) -> bool {
    components(value, "S-1-5-80-", 5)
}
pub fn interactive_user_sid(value: &str) -> bool {
    components(value, "S-1-5-21-", 4) || components(value, "S-1-12-1-", 4)
}
