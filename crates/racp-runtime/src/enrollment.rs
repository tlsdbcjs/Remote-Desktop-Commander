use racp_contract::{decode_bridge, digest, validate_schema, RacpError};
use racp_core::{
    atomic_write, credential_document, gateway_origin, load_connection, private_dir, read_bounded,
    validate_ca, AgentSettings, InstanceLock, SecretStore,
};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    time::Duration,
};
pub fn network_error(error: &reqwest::Error) -> RacpError {
    if error.is_timeout() {
        return RacpError::new("GATEWAY_TIMEOUT");
    }
    use std::error::Error;
    let mut source = error.source();
    while let Some(cause) = source {
        if cause.downcast_ref::<rustls::Error>().is_some() {
            return RacpError::new("TLS_FAILED");
        }
        source = cause.source();
    }
    RacpError::new("GATEWAY_UNREACHABLE")
}
pub fn http_client(ca: Option<&Path>) -> Result<reqwest::Client, RacpError> {
    let mut builder = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(15));
    if let Some(ca) = ca {
        let raw = read_bounded(ca, 1024 * 1024, false).map_err(|_| RacpError::new("CA_INVALID"))?;
        for cert in validate_ca(&raw)? {
            builder = builder.add_root_certificate(
                reqwest::Certificate::from_der(cert.as_ref())
                    .map_err(|_| RacpError::new("CA_INVALID"))?,
            );
        }
    }
    builder.build().map_err(|_| RacpError::new("CA_INVALID"))
}
pub async fn enroll(request: Value, state: &Path) -> Result<Value, RacpError> {
    let request = decode_bridge(&serde_json::to_vec(&request)?)?;
    if request["action"] != "enroll" {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let gateway = gateway_origin(
        request["gateway"]
            .as_str()
            .ok_or_else(|| RacpError::new("GATEWAY_INVALID"))?,
    )?;
    let token = request["token"]
        .as_str()
        .ok_or_else(|| RacpError::new("TOKEN_INVALID"))?;
    if !(20..=128).contains(&token.chars().count()) || token.chars().any(char::is_whitespace) {
        return Err(RacpError::new("TOKEN_INVALID"));
    }
    racp_core::validate_local_path(state)?;
    let mut settings: AgentSettings = serde_json::from_value(
        json!({"version":1,"gateway":gateway,"device_id":"dev_pending","workspace":request["workspace"],"data_dir":state.join("data"),"profile":request["profile"],"ca_file":request["ca_file"],"allowed_workspaces":request["allowed_workspaces"],"desktop_enabled":false}),
    )?;
    // All local validations precede consumption of the one-use server token.
    settings.validate(true)?;
    let http = http_client(settings.ca_file.as_deref())?;
    let credential = state.join("credential.bin");
    if credential.try_exists()? {
        return Err(RacpError::new("REGISTRATION_CONFLICT"));
    }
    private_dir(state)?;
    private_dir(&settings.data_dir)?;
    let _lock = InstanceLock::acquire(&state.join("credential.bin.settings.lock"))
        .map_err(|_| RacpError::new("REGISTRATION_CONFLICT"))?;
    if credential.try_exists()? {
        return Err(RacpError::new("REGISTRATION_CONFLICT"));
    }
    let reservation = state.join(".enrollment-in-progress");
    atomic_write(&reservation, b"", false).map_err(|_| RacpError::new("REGISTRATION_CONFLICT"))?;
    let result=async {
  let mut response=http.post(format!("{gateway}/agent/v1/enroll")).json(&json!({"token":token})).send().await.map_err(|e|network_error(&e))?;
  if response.status()!=200{return Err(RacpError::new(if matches!(response.status().as_u16(),401|403){"TOKEN_REJECTED"}else{"GATEWAY_REJECTED"}));}
  let mut raw=vec![];
  while let Some(chunk)=response.chunk().await.map_err(|e|network_error(&e))?{if raw.len()+chunk.len()>4096{return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));}raw.extend_from_slice(&chunk);}
  let value:Value=serde_json::from_slice(&raw).map_err(|_|RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
  validate_schema(&json!({"type":"object","additionalProperties":false,"required":["device_id","credential"],"properties":{"device_id":{"type":"string","pattern":"^[A-Za-z0-9_-]{1,96}$"},"credential":{"type":"string","minLength":20,"maxLength":128}}}),&value).map_err(|_|RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
  settings.device_id=value["device_id"].as_str().unwrap().to_string();
  let document=credential_document(&settings,value["credential"].as_str().unwrap())?;
  SecretStore::new(credential).save(&document,false).map_err(|_|RacpError::new("REGISTRATION_STORAGE_FAILED"))?;
  racp_core::information(state)
 }.await;
    // As in the existing implementation, interrupted crashes retain the marker; normal
    // completion (including a rejected HTTP request) retires only our reservation.
    let _ = std::fs::remove_file(reservation);
    result
}
pub async fn enroll_connection(request: Value, state: &Path) -> Result<Value, RacpError> {
    let request = decode_bridge(&serde_json::to_vec(&request)?)?;
    if request["action"] != "enroll_connection" {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let path = PathBuf::from(
        request["path"]
            .as_str()
            .ok_or_else(|| RacpError::new("CONNECTION_FILE_INVALID"))?,
    );
    let (value, _) = load_connection(&path, request["file_sha256"].as_str())?;
    if state.join("credential.bin").try_exists()? {
        return Err(RacpError::new("REGISTRATION_CONFLICT"));
    }
    let ca = if let Some(pem) = &value.ca_pem {
        let target = state.join(format!("gateway-ca-{}.pem", digest(pem)));
        private_dir(state)?;
        if target.try_exists()? {
            if read_bounded(&target, 16384, false)? != pem.as_bytes() {
                return Err(RacpError::new("CONNECTION_FILE_INVALID"));
            }
        } else {
            atomic_write(&target, pem.as_bytes(), false)?;
        }
        Some(target)
    } else {
        None
    };
    enroll(json!({"action":"enroll","gateway":value.gateway,"token":value.token,"workspace":request["workspace"],"profile":request["profile"],"allowed_workspaces":request["allowed_workspaces"],"ca_file":ca}),state).await
}
