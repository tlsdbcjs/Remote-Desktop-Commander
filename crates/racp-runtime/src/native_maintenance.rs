//! Fixed local preflight for a packaged user install. Never stops processes or removes state.
use racp_contract::RacpError;
use racp_core::{state_schema::*, InstanceLock};
use serde_json::{json, Value};
use std::path::Path;

#[cfg(windows)]
pub fn inspect(root: &Path) -> Result<Value, RacpError> {
    // Validate the OS user/session even when this user has never registered.
    crate::local_ipc::LocalEndpoint::for_root(root)?;
    let exe = std::env::current_exe()?;
    let payload = exe
        .parent()
        .ok_or_else(|| RacpError::new("PACKAGE_INVALID"))?;
    let _package = racp_core::distribution::pin_payload(payload)?;
    let registered = root.join(STATE_FILE).try_exists()?;
    if root.try_exists()? {
        racp_core::verify_private_native_directory(root)?;
        for pending in ["native-registration.pending", "native-login.pending.json"] {
            if root.join(pending).try_exists()? {
                return Err(RacpError::new("MAINTENANCE_OUTCOME_UNKNOWN"));
            }
        }
        if !registered {
            for entry in root.read_dir()? {
                let entry = entry?;
                if !entry.file_type()?.is_file()
                    || ![
                        "native-registration.lock",
                        "native-agent.lock",
                        "native-settings.lock",
                        "native-login.lock",
                    ]
                    .iter()
                    .any(|name| entry.file_name() == *name)
                {
                    return Err(RacpError::new("MAINTENANCE_OUTCOME_UNKNOWN"));
                }
            }
        }
    }
    let _agent = if registered {
        Some(racp_core::native_lifecycle::pin_stopped(root)?)
    } else {
        None
    };
    let _settings = if registered {
        Some(InstanceLock::acquire(&root.join("native-settings.lock"))?)
    } else {
        None
    };
    let _login = if registered {
        Some(InstanceLock::acquire(&root.join("native-login.lock"))?)
    } else {
        None
    };
    if registered {
        NativeState::load(root)?;
        load_policy(root)?;
        racp_core::runtime_admission::NativeRuntimeConfig::load(root)?;
        let login = crate::native_login::status(root)?;
        if login["pending"] == true || login["owned"] != true {
            return Err(RacpError::new("LOGIN_OWNERSHIP_CONFLICT"));
        }
        let journal = root.join("data/execution.db");
        if journal.try_exists()? {
            racp_core::journal_schema::inspect_existing(&journal)?;
        }
    }
    crate::native_login::assert_payload_unused(payload)?;
    Ok(
        json!({"preflight_complete":true,"registered":registered,"state_preserved":true,
        "current_user_only":true,"gui_must_be_closed":true,"operation_performed":false,
        "storage_schema":{"state":STATE_SCHEMA_VERSION,"policy":STATE_SCHEMA_VERSION,
            "journal":racp_core::journal_schema::VERSION},"rollback_performed":false}),
    )
}
