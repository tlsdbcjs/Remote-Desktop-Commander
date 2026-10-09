use base64::Engine;
use hmac::{Hmac, Mac};
use racp_contract::RacpError;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
#[derive(Clone)]
pub struct Cursor {
    key: [u8; 32],
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Payload {
    offset: usize,
    scope: String,
    revision: String,
    expires: i64,
}
impl Default for Cursor {
    fn default() -> Self {
        Self {
            key: rand::random(),
        }
    }
}
impl Cursor {
    pub fn encode(&self, offset: usize, scope: &str, revision: &str) -> Result<String, RacpError> {
        let raw = serde_json::to_vec(&Payload {
            offset,
            scope: scope.into(),
            revision: revision.into(),
            expires: chrono::Utc::now().timestamp() + 300,
        })?;
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.key).expect("HMAC key");
        mac.update(&raw);
        let signature = mac.finalize().into_bytes();
        Ok(base64::engine::general_purpose::URL_SAFE
            .encode([signature.as_slice(), raw.as_slice()].concat()))
    }
    pub fn decode(
        &self,
        raw: Option<&str>,
        scope: &str,
        revision: &str,
    ) -> Result<usize, RacpError> {
        let Some(raw) = raw else { return Ok(0) };
        let invalid = || RacpError::new("CURSOR_EXPIRED");
        if raw.len() > 2048 {
            return Err(invalid());
        }
        let bytes = base64::engine::general_purpose::URL_SAFE
            .decode(raw)
            .map_err(|_| invalid())?;
        if bytes.len() < 32 {
            return Err(invalid());
        }
        let (signature, payload) = bytes.split_at(32);
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.key).expect("HMAC key");
        mac.update(payload);
        mac.verify_slice(signature).map_err(|_| invalid())?;
        let value: Payload = serde_json::from_slice(payload).map_err(|_| invalid())?;
        if value.scope != scope
            || value.revision != revision
            || value.expires <= chrono::Utc::now().timestamp()
        {
            return Err(invalid());
        }
        Ok(value.offset)
    }
}
