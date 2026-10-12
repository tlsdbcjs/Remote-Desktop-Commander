//! Native state is opt-in and separate from legacy profiles; no implicit imports or downgrades.
use crate::{
    atomic_write, gateway_origin, read_bounded, validate_local_path, AgentSettings, SecretStore,
    WorkspaceSpec,
};
use racp_contract::{
    local_ipc::{reject_duplicate_keys, Grant, PolicyConstraints},
    RacpError,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub const STATE_SCHEMA_VERSION: u32 = 1;
pub const STATE_FILE: &str = "native-state.json";
pub const SECRET_FILE: &str = "native-credential.bin";
pub const POLICY_FILE: &str = "native-policy.json";
static EXPLICIT_USER_ROOT: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
/// Local GUI startup only. Initialize once before starting UI/runtime threads.
pub fn select_user_root(root: PathBuf) -> Result<(), RacpError> {
    let root = validate_local_path(&root)?;
    if root.exists() {
        crate::verify_private_native_directory(&root)?;
    }
    EXPLICIT_USER_ROOT
        .set(root)
        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))
}

pub fn default_user_root() -> Result<PathBuf, RacpError> {
    if let Some(root) = EXPLICIT_USER_ROOT.get() {
        return Ok(root.clone());
    }
    #[cfg(windows)]
    let root = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .map(|p| p.join("RACP/native-client"));
    #[cfg(target_os = "macos")]
    let root = std::env::var_os("HOME")
        .map(PathBuf::from)
        .map(|p| p.join("Library/Application Support/RACP/native-client"));
    #[cfg(all(unix, not(target_os = "macos")))]
    let root = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .map(PathBuf::from)
                .map(|p| p.join(".local/state"))
        })
        .map(|p| p.join("racp/native-client"));
    let root = root.ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
    // Resolve without filesystem I/O so presentation code can display the root safely.
    // Every load/create/endpoint operation separately validates and pins actual paths.
    if !root.is_absolute()
        || root.to_string_lossy().starts_with("\\\\")
        || root.to_string_lossy().starts_with("//")
    {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    Ok(root)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeState {
    pub schema_version: u32,
    #[serde(default)]
    pub settings_revision: u64,
    pub gateway: String,
    pub device_id: String,
    pub workspace: PathBuf,
    pub allowed_workspaces: Vec<WorkspaceSpec>,
    pub profile: String,
    pub ca_file: Option<PathBuf>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativePolicy {
    pub schema_version: u32,
    pub revision: u64,
    pub grants: BTreeMap<String, Grant>,
    pub disabled_categories: BTreeSet<String>,
    pub constraints: PolicyConstraints,
}
impl NativePolicy {
    pub fn deny_all() -> Self {
        Self {
            schema_version: STATE_SCHEMA_VERSION,
            revision: 0,
            grants: BTreeMap::new(),
            disabled_categories: BTreeSet::new(),
            constraints: PolicyConstraints {
                workspace_ids: vec![],
                max_timeout_ms: 86_400_000,
                max_output_bytes: 1_073_741_824,
                executable_allowlist: vec![],
                executable_denylist: vec![],
                strict_os_isolation: false,
            },
        }
    }
    pub fn permissions(&self) -> Result<serde_json::Value, RacpError> {
        if self.schema_version != STATE_SCHEMA_VERSION {
            return Err(RacpError::new("STATE_SCHEMA_UNSUPPORTED"));
        }
        let value = serde_json::json!({"version":1,"grants":self.grants,"disabled_categories":self.disabled_categories,"constraints":self.constraints});
        crate::validate_permissions(&value)?;
        Ok(value)
    }
}
fn document<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, RacpError> {
    let raw = read_bounded(path, 65_536, false)?;
    reject_duplicate_keys(&raw)?;
    Ok(serde_json::from_slice(&raw)?)
}
impl NativeState {
    pub fn configure(
        root: &Path,
        input: NativeSettingsUpdate,
    ) -> Result<serde_json::Value, RacpError> {
        crate::native_directory::verify(root)?;
        // Agent start must acquire the same lock BEFORE reading settings.
        let _agent = crate::InstanceLock::acquire(&root.join("native-agent.lock"))?;
        let _settings = crate::InstanceLock::acquire(&root.join("native-settings.lock"))?;
        let mut state = Self::load(root)?;
        if state.settings_revision != input.expected_revision {
            return Err(RacpError::new("STATE_REVISION_CONFLICT"));
        }
        let policy = load_policy(root)?;
        state.workspace = input.workspace;
        state.allowed_workspaces = input.allowed_workspaces;
        state.profile = input.profile;
        state.settings_revision = state
            .settings_revision
            .checked_add(1)
            .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
        state.agent_settings(root, &policy)?;
        // Gateway/device/CA and the encrypted credential remain enrollment authority.
        let bytes = serde_json::to_vec_pretty(&state)?;
        if bytes.len() > 65_536 {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        atomic_write(&root.join(STATE_FILE), &bytes, true)?;
        let actual = Self::load(root)?;
        if serde_json::to_value(&actual)? != serde_json::to_value(&state)? {
            return Err(RacpError::new("LOCAL_AGENT_OUTCOME_UNKNOWN"));
        }
        Ok(
            serde_json::json!({"committed":true,"revision":state.settings_revision,"restart_required":true,"requires_start":true}),
        )
    }
    pub fn load(root: &Path) -> Result<Self, RacpError> {
        validate_local_path(root)?;
        crate::native_directory::verify(root)?;
        crate::native_release::assert_compatible(root)?;
        let value: Self = document(&root.join(STATE_FILE))?;
        if value.schema_version != STATE_SCHEMA_VERSION {
            return Err(RacpError::new("STATE_SCHEMA_UNSUPPORTED"));
        }
        gateway_origin(&value.gateway)?;
        Ok(value)
    }
    pub fn agent_settings(
        &self,
        root: &Path,
        policy: &NativePolicy,
    ) -> Result<AgentSettings, RacpError> {
        if self.schema_version != STATE_SCHEMA_VERSION {
            return Err(RacpError::new("STATE_SCHEMA_UNSUPPORTED"));
        }
        let permissions = policy.permissions()?;
        let settings = AgentSettings {
            version: 2,
            gateway: self.gateway.clone(),
            device_id: self.device_id.clone(),
            workspace: self.workspace.clone(),
            data_dir: root.join("data"),
            allowed_workspaces: self.allowed_workspaces.clone(),
            profile: self.profile.clone(),
            ca_file: self.ca_file.clone(),
            desktop_enabled: crate::desktop_enabled(&permissions),
            permissions: Some(permissions),
        };
        settings.validate(true)?;
        Ok(settings)
    }
    /// Creation refuses registered or legacy credentials at this explicitly chosen root.
    /// State is the final commit marker; incomplete registration is diagnosed, never imported.
    pub fn create(
        &self,
        root: &Path,
        credential: &str,
        policy: &NativePolicy,
    ) -> Result<(), RacpError> {
        NativeRegistration::begin(root)?.commit(self, credential, policy)
    }
    pub fn credential(&self, root: &Path) -> Result<String, RacpError> {
        if !cfg!(windows) {
            return Err(RacpError::new("SECRET_BACKEND_UNAVAILABLE"));
        }
        let secrets = SecretStore::new(root.join(SECRET_FILE)).load()?;
        if secrets.get("gateway") != Some(&self.gateway)
            || secrets.get("device_id") != Some(&self.device_id)
        {
            return Err(RacpError::new("CONFIG_UNREADABLE"));
        }
        secrets
            .get("credential")
            .filter(|c| (20..=128).contains(&c.len()))
            .cloned()
            .ok_or_else(|| RacpError::new("CONFIG_UNREADABLE"))
    }
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeSettingsUpdate {
    pub expected_revision: u64,
    pub workspace: PathBuf,
    pub allowed_workspaces: Vec<WorkspaceSpec>,
    pub profile: String,
}
pub struct NativeRegistration {
    root: PathBuf,
    _lock: crate::InstanceLock,
}
impl NativeRegistration {
    fn ensure_empty(root: &Path, service: bool) -> Result<(), RacpError> {
        if root.try_exists()? {
            for item in root.read_dir()? {
                let name = item?.file_name();
                let fixed_service_input = service
                    && [
                        crate::native_service::CONFIG_FILE,
                        crate::native_service::BOOTSTRAP_FILE,
                        crate::native_service::CONNECTION_FILE,
                    ]
                    .iter()
                    .any(|value| name == *value);
                if name != "native-registration.lock" && !fixed_service_input {
                    return Err(RacpError::new("STATE_ALREADY_EXISTS"));
                }
            }
        }
        Ok(())
    }
    /// Reserve before consuming a one-use token. A crash marker blocks accidental reenrollment.
    pub fn begin(root: &Path) -> Result<Self, RacpError> {
        Self::begin_inner(root, false)
    }
    #[cfg(windows)]
    pub fn begin_service(root: &Path) -> Result<Self, RacpError> {
        crate::service_directory::require_service_actor()?;
        crate::native_service::NativeServiceConfig::load_inputs(root)?;
        Self::begin_inner(root, true)
    }
    fn begin_inner(root: &Path, service: bool) -> Result<Self, RacpError> {
        validate_local_path(root)?;
        if !cfg!(windows) {
            return Err(RacpError::new("SECRET_BACKEND_UNAVAILABLE"));
        }
        Self::ensure_empty(root, service)?;
        crate::native_directory::create(root)?;
        let lock = crate::InstanceLock::acquire(&root.join("native-registration.lock"))?;
        Self::ensure_empty(root, service)?;
        atomic_write(
            &root.join("native-registration.pending"),
            b"token_consumption_pending",
            false,
        )?;
        Ok(Self {
            root: root.to_owned(),
            _lock: lock,
        })
    }
    /// Called only on an authoritative token rejection, never a timeout/unknown server outcome.
    pub fn rejected(self) -> Result<(), RacpError> {
        std::fs::remove_file(self.root.join("native-registration.pending"))?;
        Ok(())
    }
    pub fn root(&self) -> &Path {
        &self.root
    }
    pub fn commit(
        self,
        state: &NativeState,
        credential: &str,
        policy: &NativePolicy,
    ) -> Result<(), RacpError> {
        let root = &self.root;
        if !(20..=128).contains(&credential.len()) {
            return Err(RacpError::new("CONFIG_UNREADABLE"));
        }
        state.agent_settings(root, policy)?;
        SecretStore::new(root.join(SECRET_FILE)).save(
            &BTreeMap::from([
                ("credential".into(), credential.into()),
                ("gateway".into(), state.gateway.clone()),
                ("device_id".into(), state.device_id.clone()),
            ]),
            false,
        )?;
        atomic_write(&root.join(POLICY_FILE), &serde_json::to_vec(policy)?, false)?;
        crate::native_lifecycle::initialize(root, state)?;
        atomic_write(&root.join(STATE_FILE), &serde_json::to_vec(state)?, false)?;
        std::fs::remove_file(root.join("native-registration.pending"))?;
        Ok(())
    }
}
pub fn load_policy(root: &Path) -> Result<NativePolicy, RacpError> {
    let policy: NativePolicy = document(&root.join(POLICY_FILE))?;
    policy.permissions()?;
    Ok(policy)
}
