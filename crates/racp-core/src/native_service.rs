//! Service config is private; only separate read-only endpoint metadata is shared.
use crate::{state_schema::NativeState, validate_local_path, Workspaces};
use racp_contract::{local_ipc::reject_duplicate_keys, service_scope::ServiceScope, RacpError};
use serde::{Deserialize, Serialize};
use std::{
    io::Read,
    path::{Path, PathBuf},
};
pub const CONFIG_FILE: &str = "native-service.json";
pub const BOOTSTRAP_FILE: &str = "native-service-bootstrap.json";
pub const CONNECTION_FILE: &str = "native-service-connection.racp";
pub const CONFIG_LOCK: &str = "native-service-config.lock";
pub const CONFIG_PENDING: &str = "native-service-config.pending.json";
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeServiceConfig {
    pub format_version: u32,
    pub revision: u64,
    pub state_dir: PathBuf,
    pub endpoint_dir: PathBuf,
    pub scope: ServiceScope,
}
impl NativeServiceConfig {
    /// SYSTEM-only fixed-namespace restore. Checks private ACL against the sealed config;
    /// the caller must recompare this SID with the OS after creating the fixed service.
    #[cfg(windows)]
    pub fn read_restore(root: &Path, expected_hash: &str) -> Result<Self, RacpError> {
        crate::machine_service::require_system()?;
        let parent = crate::machine_service::data_parent()?;
        if validate_local_path(root)? != parent.join("state")
            || root.join(CONFIG_PENDING).try_exists()?
        {
            return Err(RacpError::new("SERVICE_RESTORE_CONFLICT"));
        }
        let _parent = crate::machine_service::pin_secure_parent(&parent)?;
        let raw = crate::read_bounded(&root.join(CONFIG_FILE), 16384, false)?;
        reject_duplicate_keys(&raw)?;
        if racp_contract::digest(&raw) != expected_hash {
            return Err(RacpError::new("STATE_REVISION_CONFLICT"));
        }
        let value: Self = serde_json::from_slice(&raw)?;
        value.scope.validate()?;
        if value.format_version != 1
            || validate_local_path(&value.state_dir)? != parent.join("state")
            || validate_local_path(&value.endpoint_dir)? != parent.join("control")
        {
            return Err(RacpError::new("SERVICE_RESTORE_CONFLICT"));
        }
        let _private = crate::machine_service::pin_private_sid(root, &value.scope.service_sid)?;
        let guards = Workspaces::new(root, &[])?;
        let directory = guards.directory("default", root)?;
        let mut pinned = directory.open_pinned_read(CONFIG_FILE)?;
        let mut bytes = vec![];
        (&mut pinned).take(16385).read_to_end(&mut bytes)?;
        if bytes != raw {
            return Err(RacpError::new("STATE_REVISION_CONFLICT"));
        }
        Ok(value)
    }
    pub fn initial(
        state_dir: PathBuf,
        endpoint_dir: PathBuf,
        scope: ServiceScope,
    ) -> Result<Self, RacpError> {
        let mut config = Self {
            format_version: 1,
            revision: 0,
            state_dir: state_dir.clone(),
            endpoint_dir,
            scope,
        };
        config.validate(&state_dir)?;
        Ok(config)
    }
    pub fn load(root: &Path) -> Result<Self, RacpError> {
        let config = Self::load_inputs(root)?;
        NativeState::load(root)?;
        Ok(config)
    }
    pub fn load_inputs(root: &Path) -> Result<Self, RacpError> {
        if root.join(CONFIG_PENDING).try_exists()? {
            return Err(RacpError::new("SERVICE_CONFIG_PENDING"));
        }
        Self::read_for_change(root).map(|(config, _)| config)
    }
    pub(crate) fn read_for_change(root: &Path) -> Result<(Self, String), RacpError> {
        #[cfg(windows)]
        crate::service_directory::require_service_actor()?;
        crate::verify_private_native_directory(root)?;
        let guards = Workspaces::new(root, &[])?;
        let dir = guards.directory("default", root)?;
        let mut file = dir.open_pinned_read(CONFIG_FILE)?;
        let mut raw = vec![];
        (&mut file).take(16385).read_to_end(&mut raw)?;
        if raw.len() > 16384 {
            return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
        }
        reject_duplicate_keys(&raw)?;
        let mut value: Self = serde_json::from_slice(&raw)?;
        value.validate(root)?;
        Ok((value, racp_contract::digest(&raw)))
    }
    pub(crate) fn validate(&mut self, root: &Path) -> Result<(), RacpError> {
        let value = self;
        value.state_dir = validate_local_path(&value.state_dir)?;
        value.endpoint_dir = validate_local_path(&value.endpoint_dir)?;
        if value.format_version != 1
            || validate_local_path(&value.state_dir)? != validate_local_path(root)?
            || crate::path_within(&value.endpoint_dir, root)
            || crate::path_within(root, &value.endpoint_dir)
        {
            return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
        }
        value.scope.validate()?;
        #[cfg(windows)]
        if value.scope.service_sid != crate::service_directory::expected_service_sid()? {
            return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
        }
        Ok(())
    }
}
