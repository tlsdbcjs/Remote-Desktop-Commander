//! The GUI makes a decision about this binding; it cannot grant a reusable capability.
use racp_contract::{canonical_digest, RacpError};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApprovalBinding {
    pub request_id: String,
    pub operation_id: String,
    pub operation: String,
    pub request_digest: String,
    pub target_digest: String,
    pub owner: String,
    pub device: String,
    pub boot_id: String,
    pub epoch: u64,
    pub workspace_id: String,
    pub policy_revision: u64,
    pub nonce: String,
    /// Display only. The runtime's monotonic deadline is authoritative.
    pub expires_at: String,
}
impl ApprovalBinding {
    pub fn matches(&self, request: &Value, revision: u64) -> bool {
        self.policy_revision == revision
            && request["request_id"] == self.request_id
            && request["operation_id"] == self.operation_id
            && request["operation"] == self.operation
            && request["device_id"] == self.device
            && request["agent_boot_id"] == self.boot_id
            && request["connection_epoch"] == self.epoch
            && request["context"]["principal_id"] == self.owner
            && request["context"]["workspace_id"]
                .as_str()
                .unwrap_or("default")
                == self.workspace_id
            && Self::digest(request) == self.request_digest
    }
    pub fn digest(request: &Value) -> String {
        canonical_digest(
            &serde_json::json!({"operation":request["operation"],"payload":request["payload"],"context":request["context"],"device_id":request["device_id"],"agent_boot_id":request["agent_boot_id"],"connection_epoch":request["connection_epoch"],"request_id":request["request_id"],"operation_id":request["operation_id"]}),
        )
    }
    pub fn from_request(
        request: &Value,
        target_digest: String,
        revision: u64,
        nonce: String,
        expires_at: String,
    ) -> Result<Self, RacpError> {
        fn field(request: &Value, key: &str) -> Result<String, RacpError> {
            request[key]
                .as_str()
                .filter(|s| !s.is_empty() && s.len() <= 256)
                .map(str::to_owned)
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))
        }
        Ok(Self {
            request_id: field(request, "request_id")?,
            operation_id: field(request, "operation_id")?,
            operation: field(request, "operation")?,
            request_digest: Self::digest(request),
            target_digest,
            owner: field(&request["context"], "principal_id")?,
            device: field(request, "device_id")?,
            boot_id: field(request, "agent_boot_id")?,
            epoch: request["connection_epoch"]
                .as_u64()
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?,
            workspace_id: request["context"]["workspace_id"]
                .as_str()
                .unwrap_or("default")
                .into(),
            policy_revision: revision,
            nonce,
            expires_at,
        })
    }
}
