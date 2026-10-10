//! Private pairing binds challenges to identities obtained from the OS pipe peer, never request fields.
use hmac::{Hmac, Mac};
use racp_contract::RacpError;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::BTreeSet;
#[derive(Clone, Debug, PartialEq)]
pub struct PeerIdentity {
    pub pid: u32,
    pub created: f64,
    pub sid: String,
    pub session: u32,
    pub integrity: u32,
    pub service_sids: BTreeSet<String>,
    pub administrator: bool,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PairConfig {
    #[serde(default = "version")]
    pub version: u8,
    pub pair_id: String,
    pub session_id: u32,
    pub user_sid: String,
    pub agent_sid: String,
    #[serde(default)]
    pub agent_service_sid: Option<String>,
    pub agent_pid: u32,
    pub agent_created: f64,
    pub agent_session: u32,
    pub secret: String,
    #[serde(default)]
    pub job_name: Option<String>,
}
fn version() -> u8 {
    1
}
fn hex(value: &str, limit: usize) -> bool {
    value.len() == limit
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
pub(super) fn sid(value: &str) -> bool {
    if value.len() > 184 {
        return false;
    }
    let Some(raw) = value.strip_prefix("S-1-") else {
        return false;
    };
    let mut parts = raw.split('-');
    let Some(authority) = parts.next() else {
        return false;
    };
    if authority.is_empty()
        || !authority.bytes().all(|byte| byte.is_ascii_digit())
        || !authority
            .parse::<u64>()
            .is_ok_and(|number| number <= 0xffff_ffff_ffff)
    {
        return false;
    }
    let sub: Vec<_> = parts.collect();
    !sub.is_empty()
        && sub.len() <= 15
        && sub.iter().all(|part| {
            !part.is_empty()
                && part.bytes().all(|byte| byte.is_ascii_digit())
                && part.parse::<u32>().is_ok()
        })
}
impl PairConfig {
    pub fn decode(raw: &[u8]) -> Result<Self, RacpError> {
        if raw.len() > 4096 {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let config: Self = serde_json::from_slice(raw)?;
        config.validate()?;
        Ok(config)
    }
    pub fn validate(&self) -> Result<(), RacpError> {
        if self.version != 1
            || !hex(&self.pair_id, 32)
            || self.session_id == 0
            || self.agent_pid == 0
            || !self.agent_created.is_finite()
            || self.agent_created <= 0.0
            || !sid(&self.user_sid)
            || !sid(&self.agent_sid)
            || !(43..=128).contains(&self.secret.len())
            || !self
                .secret
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
            || self.agent_service_sid.as_ref().is_some_and(|value| {
                !sid(value)
                    || value
                        .strip_prefix("S-1-5-80-")
                        .is_none_or(|raw| raw.split('-').count() != 5)
            })
            || self
                .job_name
                .as_ref()
                .is_some_and(|name| name != &format!("Global\\RACP-Broker-Job-{}", self.pair_id))
        {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        Ok(())
    }
    pub fn agent_acl_sid(&self) -> &str {
        self.agent_service_sid.as_deref().unwrap_or(&self.agent_sid)
    }
    pub fn pipe(&self) -> String {
        format!("\\\\.\\pipe\\LOCAL\\racp-session-{}", self.pair_id)
    }
    pub fn require_agent(&self, peer: &PeerIdentity) -> Result<(), RacpError> {
        if peer.pid != self.agent_pid
            || !peer.created.is_finite()
            || (peer.created - self.agent_created).abs() > 0.000001
            || peer.sid != self.agent_sid
            || peer.session != self.agent_session
            || self
                .agent_service_sid
                .as_ref()
                .is_some_and(|sid| !peer.service_sids.contains(sid))
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(())
    }
    pub fn require_broker(&self, peer: &PeerIdentity) -> Result<(), RacpError> {
        if peer.sid != self.user_sid || peer.session != self.session_id {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(())
    }
    pub fn proof(&self, role: &str, server: &str, client: &str) -> Result<String, RacpError> {
        self.validate()?;
        if !matches!(role, "broker" | "agent") || !hex(server, 64) || !hex(client, 64) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        // Pair IDs, roles and nonces are validated ASCII. Preserve Python json.dumps spacing.
        let raw = format!(
            "[{}, \"{}\", \"{}\", \"{}\", \"{}\"]",
            self.version, self.pair_id, role, server, client
        );
        let mut mac = Hmac::<Sha256>::new_from_slice(self.secret.as_bytes())
            .map_err(|_| RacpError::new("PERMISSION_DENIED"))?;
        mac.update(raw.as_bytes());
        Ok(mac
            .finalize()
            .into_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect())
    }
    pub fn verify(
        &self,
        role: &str,
        server: &str,
        client: &str,
        proof: &str,
    ) -> Result<(), RacpError> {
        if !hex(proof, 64) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let expected = self.proof(role, server, client)?;
        if !bool::from(subtle::ConstantTimeEq::ct_eq(
            proof.as_bytes(),
            expected.as_bytes(),
        )) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(())
    }
}
