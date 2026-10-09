//! Existing RACP schemas are the authority at every untrusted JSON boundary.
mod error;
mod operations;
mod schema;
pub use error::{ErrorCode, RacpError};
pub use operations::{registry, validate_operation};
pub use schema::{decode_bridge, decode_message, normalize, validate_schema};
use serde_json::Value;
use sha2::{Digest, Sha256};
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub type BridgeRequest = Value;
pub type Message = Value;
pub type Request = Value;
pub type OperationResult = Value;
pub fn digest(raw: impl AsRef<[u8]>) -> String {
    format!("{:x}", Sha256::digest(raw.as_ref()))
}
pub fn canonical_digest(value: &Value) -> String {
    digest(serde_json::to_vec(value).expect("JSON value is serializable"))
}
pub fn new_id(prefix: &str) -> String {
    format!("{}_{}", prefix, uuid::Uuid::new_v4().simple())
}
pub fn timestamp() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, true)
}
