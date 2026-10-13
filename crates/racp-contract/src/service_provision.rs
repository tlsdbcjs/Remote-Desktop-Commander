//! Recorded fresh-provision progress is evidence of completed steps only.
//! It never authorizes namespace adoption, enrollment, restart or rollback.
use crate::{
    service_scope::SERVICE_NAME,
    service_start::{hash, id},
    RacpError,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ProvisionRequest {
    InspectProvisionReceipt { expected_receipt_sha256: String },
}
impl ProvisionRequest {
    pub fn expected_hash(&self) -> Result<&str, RacpError> {
        let Self::InspectProvisionReceipt {
            expected_receipt_sha256,
        } = self;
        if !hash(expected_receipt_sha256) {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        Ok(expected_receipt_sha256)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProvisionPhase {
    Reserved,
    ServiceCreatedDisabled,
    InputsCommitted,
    RecoveryCommitted,
    PreparedDemandStart,
}
impl ProvisionPhase {
    pub fn next(self) -> Option<Self> {
        match self {
            Self::Reserved => Some(Self::ServiceCreatedDisabled),
            Self::ServiceCreatedDisabled => Some(Self::InputsCommitted),
            Self::InputsCommitted => Some(Self::RecoveryCommitted),
            Self::RecoveryCommitted => Some(Self::PreparedDemandStart),
            Self::PreparedDemandStart => None,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProvisionReceipt {
    pub format_version: u32,
    pub nonce: String,
    pub phase: ProvisionPhase,
    pub service_name: String,
    pub payload_manifest_sha256: String,
    pub bootstrap_sha256: String,
    pub registered: bool,
    pub ready: bool,
}
impl ProvisionReceipt {
    pub fn reserved(nonce: String, payload: String, bootstrap: String) -> Result<Self, RacpError> {
        let value = Self {
            format_version: 1,
            nonce,
            phase: ProvisionPhase::Reserved,
            service_name: SERVICE_NAME.into(),
            payload_manifest_sha256: payload,
            bootstrap_sha256: bootstrap,
            registered: false,
            ready: false,
        };
        value.validate()?;
        Ok(value)
    }
    pub fn validate(&self) -> Result<(), RacpError> {
        if self.format_version != 1
            || !id(&self.nonce, "provision_")
            || self.service_name != SERVICE_NAME
            || !hash(&self.payload_manifest_sha256)
            || !hash(&self.bootstrap_sha256)
            || self.registered
            || self.ready
        {
            return Err(RacpError::new("SERVICE_PROVISION_RECEIPT_INVALID"));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn receipt() -> ProvisionReceipt {
        ProvisionReceipt::reserved(crate::new_id("provision"), "a".repeat(64), "b".repeat(64))
            .unwrap()
    }
    #[test]
    fn inspection_requires_exact_hash_and_cannot_accept_namespace_or_restart_claims() {
        let mut value = serde_json::json!({"type":"inspect_provision_receipt","expected_receipt_sha256":"a".repeat(64)});
        serde_json::from_value::<ProvisionRequest>(value.clone())
            .unwrap()
            .expected_hash()
            .unwrap();
        value["service_absent"] = serde_json::json!(true);
        assert!(serde_json::from_value::<ProvisionRequest>(value.clone()).is_err());
        value.as_object_mut().unwrap().remove("service_absent");
        value["expected_receipt_sha256"] = serde_json::json!("invalid");
        assert!(serde_json::from_value::<ProvisionRequest>(value)
            .unwrap()
            .expected_hash()
            .is_err());
    }
    #[test]
    fn receipt_preserves_legacy_fields_without_claiming_registration_or_readiness() {
        let original = receipt();
        let mut value = serde_json::to_value(&original).unwrap();
        let decoded: ProvisionReceipt = serde_json::from_value(value.clone()).unwrap();
        assert_eq!(decoded, original);
        value["phase"] = serde_json::json!("prepared_demand_start");
        let mut prepared: ProvisionReceipt = serde_json::from_value(value).unwrap();
        prepared.validate().unwrap();
        prepared.ready = true;
        assert!(prepared.validate().is_err());
        prepared.ready = false;
        prepared.registered = true;
        assert!(prepared.validate().is_err());
    }
    #[test]
    fn receipt_rejects_foreign_namespace_nonce_hash_and_unknown_credentials() {
        for key in [
            "service_name",
            "nonce",
            "payload_manifest_sha256",
            "bootstrap_sha256",
        ] {
            let mut value = serde_json::to_value(receipt()).unwrap();
            value[key] = serde_json::json!("invalid");
            let decoded: ProvisionReceipt = serde_json::from_value(value).unwrap();
            assert!(decoded.validate().is_err());
        }
        let mut value = serde_json::to_value(receipt()).unwrap();
        value["enrollment_token"] = serde_json::json!("fixture-secret");
        assert!(serde_json::from_value::<ProvisionReceipt>(value).is_err());
    }
}
