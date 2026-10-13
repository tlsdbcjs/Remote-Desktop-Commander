//! Virtual-account, stopped-Agent removal preflight. Credential/state remain untouched.
use racp_contract::{digest, RacpError};
use racp_core::{native_service::*, state_schema::*, InstanceLock};
use serde_json::{json, Value};
use std::path::Path;
pub const RESULT_FILE: &str = racp_core::service_start::MAINTENANCE_RECEIPT_FILE;
pub fn inspect(root: &Path, nonce: &str) -> Result<(), RacpError> {
    if !nonce.strip_prefix("maintenance_").is_some_and(|id| {
        id.len() == 32
            && id
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    }) {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    racp_core::service_directory::require_service_actor()?;
    racp_core::verify_private_native_directory(root)?;
    let _config = InstanceLock::acquire(&root.join(CONFIG_LOCK))?;
    let config = NativeServiceConfig::load_inputs(root)?;
    let registered = root.join(STATE_FILE).try_exists()?;
    let _agent = if registered {
        racp_core::native_lifecycle::pin_stopped(root)?
    } else {
        InstanceLock::acquire(&root.join("native-agent.lock"))?
    };
    let _settings = InstanceLock::acquire(&root.join("native-settings.lock"))?;
    for name in [
        "native-registration.pending",
        CONFIG_PENDING,
        "native-service-bootstrap-cleanup.json",
    ] {
        if root.join(name).try_exists()? {
            return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
        }
    }
    let device_id = if registered {
        let state = NativeState::load(root)?;
        load_policy(root)?;
        racp_core::runtime_admission::NativeRuntimeConfig::load(root)?;
        let journal = root.join("data/execution.db");
        if journal.try_exists()? {
            racp_core::journal_schema::inspect_existing(&journal)?;
        }
        Some(state.device_id)
    } else {
        let guards = racp_core::Workspaces::new(root, &[])?;
        let directory = guards.directory("default", root)?;
        validate_unregistered_files(&directory.names()?)?;
        None
    };
    let _control = racp_core::service_directory::pin(&config.endpoint_dir, &config.scope)?;
    for path in [
        root.join("desktop-session-records"),
        config.endpoint_dir.join("desktop-sessions"),
    ] {
        if path.try_exists()? {
            let guards = racp_core::Workspaces::new(&path, &[])?;
            if !guards.directory("default", &path)?.names()?.is_empty() {
                return Err(RacpError::new("SERVICE_SESSION_RECOVERY_REQUIRED"));
            }
        }
    }
    let result: Value = json!({"format_version":1,"request_id":nonce,"preflight_complete":true,
        "config_sha256":digest(racp_core::read_bounded(&root.join(CONFIG_FILE),16384,false)?),
        "device_id":device_id,"registered":registered,"state_preserved":true,"credential_accessed":false});
    racp_core::atomic_write(&root.join(RESULT_FILE), &serde_json::to_vec(&result)?, true)
}

fn validate_unregistered_files(names: &[String]) -> Result<(), RacpError> {
    for name in names {
        if ![
            CONFIG_FILE,
            BOOTSTRAP_FILE,
            CONNECTION_FILE,
            CONFIG_LOCK,
            "native-agent.lock",
            "native-settings.lock",
            RESULT_FILE,
            "native-service-detach.json",
            "native-service-restore.json",
            racp_core::service_start::INTENT_FILE,
            racp_core::service_start::INTENT_LOCK,
            racp_core::service_start::ADMIN_LOCK,
            racp_core::service_start::EXECUTION_LOCK,
        ]
        .contains(&name.as_str())
        {
            return Err(RacpError::new("SERVICE_MAINTENANCE_UNCONFIRMED"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unregistered_maintenance_accepts_durable_start_artifacts_and_rejects_unknown_files() {
        let mut names: Vec<String> = [
            CONFIG_FILE,
            BOOTSTRAP_FILE,
            CONNECTION_FILE,
            "native-service-start-intent.json",
            "native-service-start-intent.lock",
            "native-service-admin.lock",
            "native-service-execution.lock",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect();
        assert!(validate_unregistered_files(&names).is_ok());
        names.push("native-service-start-intent-extra.json".into());
        assert!(validate_unregistered_files(&names).is_err());
        names.pop();
        names.push(SECRET_FILE.into());
        assert!(validate_unregistered_files(&names).is_err());
    }
}
