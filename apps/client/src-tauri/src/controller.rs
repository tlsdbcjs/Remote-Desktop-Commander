use racp_contract::{decode_bridge, RacpError};
use racp_runtime::ControlClient;
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::Manager;
pub struct Controller {
    pub state_dir: PathBuf,
    pub control: ControlClient,
    pub overview: Mutex<Value>,
    pub selection: Mutex<Option<(PathBuf, String)>>,
    pub busy: AtomicBool,
    pub quitting: AtomicBool,
    pub tray: AtomicBool,
    serial: tokio::sync::Mutex<()>,
}
fn legacy_state() -> Result<PathBuf, RacpError> {
    let args: Vec<String> = std::env::args().collect();
    if let Some(pair) = args.windows(2).find(|p| p[0] == "--portable-state") {
        let requested = PathBuf::from(&pair[1]);
        let allowed = PathBuf::from(
            std::env::var_os("LOCALAPPDATA").ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
        )
        .join("RACP/portable-state");
        let path = racp_core::validate_local_path(&requested)?;
        if !racp_core::path_within(&path, &allowed) || path == allowed {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        return Ok(path);
    }
    #[cfg(windows)]
    {
        let current = std::env::current_exe()?;
        if current
            .parent()
            .ok_or_else(|| RacpError::new("RUNTIME_UNAVAILABLE"))?
            .join("portable.json")
            .try_exists()?
        {
            let marker = racp_core::read_bounded(
                &current.parent().unwrap().join("portable.json"),
                1024,
                false,
            )?;
            if serde_json::from_slice::<Value>(&marker)? != json!({"version":1,"mode":"isolated"}) {
                return Err(RacpError::new("LOCAL_STATE_FAILED"));
            }
            let local = PathBuf::from(
                std::env::var_os("LOCALAPPDATA")
                    .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
            );
            return racp_core::validate_local_path(&local.join("RACP/portable-state").join(
                racp_contract::digest(current.to_string_lossy().to_lowercase()),
            ));
        }
        racp_runtime::maintenance::client_state()
    }
    #[cfg(not(windows))]
    {
        let root = std::env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/state")))
            .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?;
        Ok(root.join("racp/client/agent"))
    }
}
impl Controller {
    pub fn new() -> Result<Self, RacpError> {
        let state_dir = legacy_state()?;
        racp_core::private_dir(&state_dir)?;
        racp_core::private_dir(&state_dir.join("webview-cache"))?;
        let current = std::env::current_exe()?;
        let executable = current
            .parent()
            .ok_or_else(|| RacpError::new("RUNTIME_UNAVAILABLE"))?
            .join("agent")
            .join(if cfg!(windows) {
                "racp-agent.exe"
            } else {
                "racp-agent"
            });
        racp_core::validate_local_path(&executable)?;
        Ok(Self {
            state_dir: state_dir.clone(),
            control: ControlClient::new(state_dir, executable),
            overview: Mutex::new(
                json!({"status":null,"activity":{"events":[]},"updatedAt":null,"error":"","busy":false,"tray_available":false}),
            ),
            selection: Mutex::new(None),
            busy: AtomicBool::new(false),
            quitting: AtomicBool::new(false),
            tray: AtomicBool::new(false),
            serial: tokio::sync::Mutex::new(()),
        })
    }
    pub fn snapshot(&self) -> Value {
        let mut value=self.overview.lock().map(|s|s.clone()).unwrap_or_else(|_|json!({"status":null,"activity":{"events":[]},"error":"상태를 확인할 수 없습니다."}));
        value["busy"] = json!(self.busy.load(Ordering::Acquire));
        value["tray_available"] = json!(self.tray.load(Ordering::Acquire));
        value
    }
    async fn sample(&self) -> Value {
        let sampled = async {
            let status = self.control.status().await?;
            let state = self.state_dir.clone();
            let activity =
                tauri::async_runtime::spawn_blocking(move || racp_runtime::activity(&state))
                    .await
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))??;
            Ok::<_, RacpError>((status, activity))
        }
        .await;
        if let Ok(mut value) = self.overview.lock() {
            match sampled {
                Ok((status, activity)) => {
                    value["status"] = status;
                    value["activity"] = activity;
                    value["updatedAt"] = json!(racp_contract::timestamp());
                    value["error"] = json!("");
                }
                Err(_) => {
                    value["status"] = Value::Null;
                    value["error"] = json!(
                        "Agent 상태를 확인할 수 없습니다. 연결 설정과 Agent를 확인해 주세요."
                    );
                }
            }
        }
        self.snapshot()
    }
    pub async fn refresh(&self) -> Value {
        let _serial = self.serial.lock().await;
        self.sample().await
    }
    pub async fn request(&self, request: Value) -> Result<Value, RacpError> {
        let request = decode_bridge(&serde_json::to_vec(&request)?)?;
        let action = request["action"].as_str().unwrap_or("");
        if self
            .busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(RacpError::new("BUSY"));
        }
        struct Busy<'a>(&'a AtomicBool);
        impl Drop for Busy<'_> {
            fn drop(&mut self) {
                self.0.store(false, Ordering::Release);
            }
        }
        let _busy = Busy(&self.busy);
        let _serial = self.serial.lock().await;
        let state = self.state_dir.clone();
        let result = match action {
            "start" => self.control.start().await,
            "status" => self.control.status().await,
            "stop" => self.control.stop().await,
            "enroll" => racp_runtime::enroll(request, &state).await,
            "enroll_connection" => racp_runtime::enroll_connection(request, &state).await,
            "info" | "settings" | "update_settings" | "activity" => {
                tauri::async_runtime::spawn_blocking(move || {
                    match request["action"].as_str().unwrap_or("") {
                        "info" => racp_core::information(&state),
                        "settings" => racp_core::editable_settings(&state),
                        "update_settings" => racp_core::update_settings(&state, request),
                        "activity" => racp_runtime::activity(&state),
                        _ => Err(RacpError::new("REQUEST_INVALID")),
                    }
                })
                .await
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            }
            _ => Err(RacpError::new("REQUEST_INVALID")),
        };
        self.sample().await;
        result
    }
    pub fn show(app: &tauri::AppHandle) {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.unminimize();
            let _ = window.show();
            let _ = window.set_focus();
        }
    }
    pub async fn exit(&self, app: tauri::AppHandle) -> Result<(), RacpError> {
        let result = self.request(json!({"action":"stop"})).await?;
        if result["state"] != "STOPPED"
            || matches!(
                result["cleanup_status"].as_str(),
                Some("unknown" | "partial" | "pending")
            )
        {
            Self::show(&app);
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        self.quitting.store(true, Ordering::Release);
        app.exit(0);
        Ok(())
    }
}
