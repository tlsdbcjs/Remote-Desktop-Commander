use crate::{controller::Controller, login};
use racp_contract::{decode_bridge, RacpError};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::atomic::Ordering,
    sync::Arc,
};
use tauri::{Manager, State, WebviewWindow};
use tauri_plugin_dialog::DialogExt;
type Host<'a> = State<'a, Arc<Controller>>;
fn safe(error: RacpError) -> String {
    error.code.0.into()
}
fn main_window(window: &WebviewWindow) -> Result<(), String> {
    let url = window.url().map_err(|_| "REQUEST_INVALID")?;
    let allowed = url.scheme() == "tauri" && url.host_str() == Some("localhost")
        || matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost")
        || cfg!(debug_assertions)
            && url.scheme() == "http"
            && url.host_str() == Some("localhost")
            && url.port() == Some(5173);
    if window.label() != "main" || !allowed {
        return Err("PERMISSION_DENIED".into());
    }
    Ok(())
}
#[tauri::command]
pub async fn client_request(
    window: WebviewWindow,
    request: Value,
    state: Host<'_>,
) -> Result<Value, String> {
    main_window(&window)?;
    if matches!(
        request["action"].as_str(),
        Some("inspect_connection" | "enroll_connection")
    ) {
        return Err("REQUEST_INVALID".into());
    }
    state.request(request).await.map_err(safe)
}
#[tauri::command]
pub fn client_overview(window: WebviewWindow, state: Host<'_>) -> Result<Value, String> {
    main_window(&window)?;
    Ok(state.snapshot())
}
#[tauri::command]
pub async fn client_refresh(window: WebviewWindow, state: Host<'_>) -> Result<Value, String> {
    main_window(&window)?;
    Ok(state.refresh().await)
}
#[tauri::command]
pub async fn client_exit(window: WebviewWindow, state: Host<'_>) -> Result<(), String> {
    main_window(&window)?;
    let app = window.app_handle().clone();
    let result = state.exit(app.clone()).await.map_err(safe);
    if result.is_err() {
        Controller::show(&app);
    }
    result
}
async fn picker(
    window: &WebviewWindow,
    folder: bool,
    extensions: &[&str],
) -> Result<Option<PathBuf>, String> {
    let app = window.app_handle().clone();
    let extensions: Vec<String> = extensions.iter().map(|s| (*s).into()).collect();
    tauri::async_runtime::spawn_blocking(move || {
        let dialog = app.dialog().file().set_title(if folder {
            "작업 폴더 선택"
        } else {
            "연결 파일 선택"
        });
        let chosen = if folder {
            dialog.blocking_pick_folder()
        } else {
            let refs: Vec<_> = extensions.iter().map(String::as_str).collect();
            dialog.add_filter("RACP", &refs).blocking_pick_file()
        };
        chosen
            .map(|path| path.into_path().map_err(|_| "REQUEST_INVALID".to_owned()))
            .transpose()
    })
    .await
    .map_err(|_| "REQUEST_FAILED".to_owned())?
}
#[tauri::command]
pub async fn client_folder(window: WebviewWindow) -> Result<String, String> {
    main_window(&window)?;
    let path = picker(&window, true, &[]).await?;
    if let Some(path) = path {
        racp_core::validate_local_path(&path).map_err(safe)?;
        Ok(path.to_string_lossy().into_owned())
    } else {
        Ok(String::new())
    }
}
#[tauri::command]
pub async fn client_ca(window: WebviewWindow) -> Result<String, String> {
    main_window(&window)?;
    let path = picker(&window, false, &["pem", "crt", "cer"]).await?;
    if let Some(path) = path {
        let checked = path.clone();
        tauri::async_runtime::spawn_blocking(move || {
            racp_core::validate_local_path(&checked)?;
            let bytes = racp_core::read_bounded(&checked, 1024 * 1024, false)?;
            racp_core::validate_ca(&bytes)
        })
        .await
        .map_err(|_| "CA_INVALID".to_owned())?
        .map_err(safe)?;
        Ok(path.to_string_lossy().into_owned())
    } else {
        Ok(String::new())
    }
}
#[tauri::command]
pub async fn client_connection(window: WebviewWindow, state: Host<'_>) -> Result<Value, String> {
    main_window(&window)?;
    *state.selection.lock().map_err(|_| "LOCAL_STATE_FAILED")? = None;
    let Some(path) = picker(&window, false, &["racp"]).await? else {
        return Ok(Value::Null);
    };
    let selected = path.clone();
    let preview = tauri::async_runtime::spawn_blocking(move || {
        racp_core::inspect_connection(&selected, None)
    })
    .await
    .map_err(|_| "CONNECTION_FILE_INVALID".to_owned())?
    .map_err(safe)?;
    let hash = preview["file_sha256"]
        .as_str()
        .ok_or_else(|| "CONNECTION_FILE_INVALID".to_owned())?
        .to_owned();
    *state.selection.lock().map_err(|_| "LOCAL_STATE_FAILED")? = Some((path, hash));
    let mut visible = preview;
    visible
        .as_object_mut()
        .ok_or_else(|| "CONNECTION_FILE_INVALID".to_owned())?
        .remove("file_sha256");
    visible.as_object_mut().unwrap().remove("path");
    Ok(visible)
}
#[tauri::command]
pub async fn client_enroll_connection(
    window: WebviewWindow,
    value: Value,
    state: Host<'_>,
) -> Result<Value, String> {
    main_window(&window)?;
    let allowed = [
        "workspace",
        "profile",
        "allowed_workspaces",
        "desktop_enabled",
        "permissions",
    ];
    if value
        .as_object()
        .is_none_or(|o| o.keys().any(|key| !allowed.contains(&key.as_str())))
    {
        return Err("REQUEST_INVALID".into());
    }
    let (path, hash) = state
        .selection
        .lock()
        .map_err(|_| "LOCAL_STATE_FAILED")?
        .clone()
        .ok_or_else(|| "CONNECTION_FILE_INVALID".to_owned())?;
    let mut request = value;
    request["action"] = json!("enroll_connection");
    request["path"] = json!(path);
    request["file_sha256"] = json!(hash);
    let request = decode_bridge(&serde_json::to_vec(&request).map_err(|_| "REQUEST_INVALID")?)
        .map_err(safe)?;
    let result = state.request(request).await.map_err(safe);
    if result.is_ok() {
        *state.selection.lock().map_err(|_| "LOCAL_STATE_FAILED")? = None;
    }
    result
}
#[tauri::command]
pub fn client_login_settings(window: WebviewWindow) -> Result<Value, String> {
    main_window(&window)?;
    login::settings().map_err(safe)
}
#[tauri::command]
pub async fn client_set_login(
    window: WebviewWindow,
    enabled: bool,
    state: Host<'_>,
) -> Result<Value, String> {
    main_window(&window)?;
    if enabled
        && state
            .request(json!({"action":"info"}))
            .await
            .map_err(safe)?["configured"]
            != true
    {
        return Err("LOGIN_NOT_CONFIGURED".into());
    }
    login::set(enabled).map_err(safe)
}
