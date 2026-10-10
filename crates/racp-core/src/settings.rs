use crate::{validate_ca, validate_local_path, InstanceLock, SecretStore};
use racp_contract::{decode_bridge, digest, new_id, validate_schema, RacpError};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceSpec {
    pub id: String,
    pub path: PathBuf,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentSettings {
    #[serde(default = "one")]
    pub version: u8,
    pub gateway: String,
    pub device_id: String,
    pub workspace: PathBuf,
    pub data_dir: PathBuf,
    #[serde(default)]
    pub allowed_workspaces: Vec<WorkspaceSpec>,
    #[serde(default = "read_only")]
    pub profile: String,
    #[serde(default)]
    pub ca_file: Option<PathBuf>,
    #[serde(default)]
    pub desktop_enabled: bool,
    #[serde(default)]
    pub permissions: Option<Value>,
}
fn one() -> u8 {
    1
}
fn read_only() -> String {
    "read_only".into()
}
pub fn gateway_origin(text: &str) -> Result<String, RacpError> {
    if text.chars().count() > 2048 {
        return Err(RacpError::new("GATEWAY_INVALID"));
    }
    let u = url::Url::parse(text).map_err(|_| RacpError::new("GATEWAY_INVALID"))?;
    let host = u.host_str().unwrap_or("").trim_matches(['[', ']']);
    let local = ["localhost", "127.0.0.1", "::1"].contains(&host);
    if u.host_str().is_none()
        || u.scheme() != "https" && !(u.scheme() == "http" && local)
        || !u.username().is_empty()
        || u.password().is_some()
        || u.query().is_some()
        || u.fragment().is_some()
        || u.path() != "/"
        || u.port() == Some(0)
        || text.chars().any(|c| c < ' ')
    {
        return Err(RacpError::new("GATEWAY_INVALID"));
    }
    Ok(text.trim_end_matches('/').to_string())
}
impl AgentSettings {
    pub fn validate(&self, live: bool) -> Result<(), RacpError> {
        let schema: Value = serde_json::from_str(include_str!(
            "../../../docs/protocol/agent-settings-v1.schema.json"
        ))
        .expect("settings schema");
        let mut document = serde_json::to_value(self)?;
        document["version"] = json!(2);
        document["permissions"] = self
            .permissions
            .clone()
            .unwrap_or_else(|| crate::legacy_permissions(self.desktop_enabled));
        validate_schema(&schema, &document)?;
        crate::validate_permissions(&document["permissions"])?;
        gateway_origin(&self.gateway)?;
        let mut ids = BTreeSet::new();
        if self.allowed_workspaces.len() > 15 {
            return Err(RacpError::new("WORKSPACE_INVALID"));
        }
        for (id, path) in std::iter::once(("default", &self.workspace)).chain(
            self.allowed_workspaces
                .iter()
                .map(|w| (w.id.as_str(), &w.path)),
        ) {
            if !ids.insert(id)
                || id == "default" && path != &self.workspace
                || !path.is_absolute()
                || ["//", "\\\\"]
                    .iter()
                    .any(|prefix| path.to_string_lossy().starts_with(prefix))
            {
                return Err(RacpError::new("WORKSPACE_INVALID"));
            }
            if live {
                validate_local_path(path).map_err(|_| RacpError::new("WORKSPACE_INVALID"))?;
                if !path.is_dir() {
                    return Err(RacpError::new("WORKSPACE_INVALID"));
                }
            }
        }
        if live {
            validate_local_path(&self.data_dir)?;
            if let Some(ca) = &self.ca_file {
                validate_ca(
                    &crate::read_bounded(ca, 1024 * 1024, false)
                        .map_err(|_| RacpError::new("CA_INVALID"))?,
                )
                .map_err(|_| RacpError::new("CA_INVALID"))?;
            }
        }
        Ok(())
    }
}
pub fn load_settings(
    state: &Path,
    live: bool,
) -> Result<(AgentSettings, BTreeMap<String, String>), RacpError> {
    let value = SecretStore::new(state.join("credential.bin")).load()?;
    let document = value
        .get("agent_settings")
        .ok_or_else(|| RacpError::new("CONFIG_UNREADABLE"))?;
    if document.len() > 16384 {
        return Err(RacpError::new("CONFIG_UNREADABLE"));
    }
    let mut raw: Value = serde_json::from_str(document)?;
    if raw["version"].as_u64().unwrap_or(1) == 1 {
        if raw.get("permissions").is_some() {
            return Err(RacpError::new("CONFIG_UNREADABLE"));
        }
        raw["permissions"] = crate::legacy_permissions(raw["desktop_enabled"] == true);
        raw["version"] = json!(2);
    } else if raw["version"] != 2 || raw["permissions"].is_null() {
        return Err(RacpError::new("CONFIG_UNREADABLE"));
    }
    let settings: AgentSettings = serde_json::from_value(raw)?;
    settings.validate(live)?;
    if value.get("gateway") != Some(&settings.gateway)
        || value.get("device_id") != Some(&settings.device_id)
        || !value
            .get("credential")
            .is_some_and(|s| (20..=128).contains(&s.len()))
    {
        return Err(RacpError::new("CONFIG_UNREADABLE"));
    }
    Ok((settings, value))
}
pub fn credential_document(
    settings: &AgentSettings,
    credential: &str,
) -> Result<BTreeMap<String, String>, RacpError> {
    let mut raw = serde_json::to_value(settings)?;
    raw["version"] = json!(2);
    raw["permissions"] = settings
        .permissions
        .clone()
        .unwrap_or_else(|| crate::legacy_permissions(settings.desktop_enabled));
    let document = serde_json::to_string(&raw)?;
    if document.len() > 16384 {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    let mut value = BTreeMap::from([
        ("gateway".into(), settings.gateway.clone()),
        ("device_id".into(), settings.device_id.clone()),
        ("credential".into(), credential.into()),
        ("agent_settings".into(), document),
    ]);
    if let Some(ca) = &settings.ca_file {
        value.insert("ca_file".into(), ca.to_string_lossy().to_string());
    }
    Ok(value)
}
fn view(settings: &AgentSettings) -> Result<Value, RacpError> {
    let mut v = serde_json::to_value(settings)?;
    v.as_object_mut().unwrap().remove("data_dir");
    v.as_object_mut().unwrap().remove("version");
    Ok(v)
}
pub fn information(state: &Path) -> Result<Value, RacpError> {
    validate_local_path(&state.join("credential.bin"))?;
    let mut info = json!({"configured":false,"execution_identity":whoami::username(),"desktop_supported":cfg!(windows)});
    if state.join("credential.bin").try_exists()? {
        let (settings, _) =
            load_settings(state, true).map_err(|_| RacpError::new("CONFIG_UNREADABLE"))?;
        info.as_object_mut()
            .unwrap()
            .extend(view(&settings)?.as_object().unwrap().clone());
        info["configured"] = json!(true);
    }
    Ok(info)
}
pub fn editable_settings(state: &Path) -> Result<Value, RacpError> {
    let _lock = InstanceLock::acquire(&state.join("credential.bin.settings.lock"))?;
    let (settings, _) =
        load_settings(state, false).map_err(|_| RacpError::new("CONFIG_UNREADABLE"))?;
    let mut value = view(&settings)?;
    value["revision"] = json!(digest(crate::read_bounded(
        &state.join("credential.bin"),
        65536,
        true
    )?));
    Ok(value)
}
pub fn update_settings(state: &Path, request: Value) -> Result<Value, RacpError> {
    let request = decode_bridge(&serde_json::to_vec(&request)?)?;
    if request["action"] != "update_settings" {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let _lock = InstanceLock::acquire(&state.join("credential.bin.settings.lock"))?;
    let (previous, value) = load_settings(state, false)?;
    let current = digest(crate::read_bounded(
        &state.join("credential.bin"),
        65536,
        true,
    )?);
    if request["revision"] != current {
        return Err(RacpError::new("SETTINGS_CHANGED"));
    }
    let _agent =
        InstanceLock::acquire(&state.join(format!("agent-{}.lock", digest(&previous.device_id))))?;
    let mut pending = serde_json::to_value(&previous)?;
    for key in [
        "gateway",
        "workspace",
        "profile",
        "ca_file",
        "allowed_workspaces",
        "desktop_enabled",
        "permissions",
    ] {
        if let Some(v) = request.get(key) {
            if !matches!(key, "desktop_enabled" | "permissions") || !v.is_null() {
                pending[key] = v.clone();
            }
        }
    }
    let mut settings: AgentSettings = serde_json::from_value(pending)?;
    settings.gateway = gateway_origin(&settings.gateway)?;
    if let Some(p) = &settings.permissions {
        settings.desktop_enabled = crate::desktop_enabled(p);
    }
    settings.validate(true)?;
    let replacement = credential_document(&settings, &value["credential"])?;
    SecretStore::new(
        state
            .join("settings-backups")
            .join(format!("{}.bin", new_id("backup"))),
    )
    .save(&value, false)?;
    SecretStore::new(state.join("credential.bin")).save(&replacement, true)?;
    information(state)
}
