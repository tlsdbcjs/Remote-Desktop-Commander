use crate::{validate_ca, validate_local_path};
use racp_contract::{validate_schema, RacpError};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeSet, path::PathBuf};
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceSpec {
    pub id: String,
    pub path: PathBuf,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentSettings {
    #[serde(default = "two")]
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
fn two() -> u8 {
    2
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
    pub fn local_permissions(&self) -> Result<Value, RacpError> {
        let permissions = self
            .permissions
            .clone()
            .ok_or_else(|| RacpError::new("PERMISSIONS_INVALID"))?;
        crate::validate_permissions(&permissions)?;
        Ok(permissions)
    }
    pub fn validate(&self, live: bool) -> Result<(), RacpError> {
        if self.version != 2 {
            return Err(RacpError::new("STATE_SCHEMA_UNSUPPORTED"));
        }
        let schema: Value = serde_json::from_str(include_str!(
            "../../../docs/protocol/agent-settings-v1.schema.json"
        ))
        .expect("settings schema");
        let mut document = serde_json::to_value(self)?;
        document["permissions"] = self.local_permissions()?;
        if self.desktop_enabled != crate::desktop_enabled(&document["permissions"]) {
            return Err(RacpError::new("PERMISSIONS_INVALID"));
        }
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
