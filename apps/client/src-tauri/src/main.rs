#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod commands;
mod controller;
mod login;
use controller::Controller;
use std::sync::{atomic::Ordering, Arc};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, WebviewUrl, WebviewWindowBuilder,
};
fn navigation(url: &tauri::Url) -> bool {
    url.scheme() == "tauri" && url.host_str() == Some("localhost")
        || matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost")
        || cfg!(debug_assertions)
            && url.scheme() == "http"
            && url.host_str() == Some("localhost")
            && url.port() == Some(5173)
}
fn tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "현황 창 열기", true, None::<&str>)?;
    let start = MenuItem::with_id(app, "start", "Agent 시작", true, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "Agent 종료", true, None::<&str>)?;
    let exit = MenuItem::with_id(app, "exit", "완전 종료 · Agent와 앱", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &start, &stop, &exit])?;
    let icon = tauri::image::Image::from_bytes(include_bytes!("../../assets/offline.png"))?;
    TrayIconBuilder::with_id("racp-status")
        .icon(icon)
        .menu(&menu)
        .tooltip("RACP · 상태 확인 중")
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if matches!(
                event,
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                }
            ) {
                Controller::show(tray.app_handle());
            }
        })
        .on_menu_event(|app, event| {
            if event.id().as_ref() == "show" {
                Controller::show(app);
                return;
            }
            let action = event.id().as_ref().to_owned();
            let app = app.clone();
            let state = app.state::<Arc<Controller>>().inner().clone();
            tauri::async_runtime::spawn(async move {
                let result = if action == "exit" {
                    state
                        .exit(app.clone())
                        .await
                        .map(|_| serde_json::Value::Null)
                } else {
                    state.request(serde_json::json!({"action":action})).await
                };
                if result.is_err() {
                    Controller::show(&app);
                }
            });
        })
        .build(app)?;
    let state = app.state::<Arc<Controller>>();
    state.tray.store(true, Ordering::Release);
    Ok(())
}
fn refresh_tray(app: &tauri::AppHandle, state: &Controller) {
    let value = state.snapshot();
    let connected = value["status"]["connected"] == true;
    let busy = state.busy.load(Ordering::Acquire)
        || value["status"]["active_operations"].as_u64().unwrap_or(0) > 0;
    if let Some(tray) = app.tray_by_id("racp-status") {
        let bytes: &[u8] = if busy {
            include_bytes!("../../assets/busy.png")
        } else if connected {
            include_bytes!("../../assets/connected.png")
        } else {
            include_bytes!("../../assets/offline.png")
        };
        if let Ok(image) = tauri::image::Image::from_bytes(bytes) {
            let _ = tray.set_icon(Some(image));
        }
        let label = if connected {
            "Gateway 연결됨"
        } else if value["status"]["state"] == "RUNNING" {
            "연결 중"
        } else {
            "Agent 종료됨"
        };
        let _ = tray.set_tooltip(Some(format!("RACP · {label}")));
    }
}
fn main() {
    // Fixed WebView2 is selected before Tauri starts any WebView thread.
    if let Ok(executable) = std::env::current_exe() {
        if let Some(root) = executable.parent() {
            let runtime = root.join("webview2");
            if runtime.join("msedgewebview2.exe").is_file()
                && racp_core::validate_local_path(&runtime).is_ok()
            {
                std::env::set_var("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", &runtime);
            }
        }
    }
    let controller = match Controller::new() {
        Ok(state) => Arc::new(state),
        Err(_) => {
            std::process::exit(4);
        }
    };
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            Controller::show(app)
        }))
        .plugin(tauri_plugin_dialog::init())
        .manage(controller.clone())
        .invoke_handler(tauri::generate_handler![
            commands::client_request,
            commands::client_overview,
            commands::client_refresh,
            commands::client_exit,
            commands::client_folder,
            commands::client_ca,
            commands::client_connection,
            commands::client_enroll_connection,
            commands::client_login_settings,
            commands::client_set_login
        ])
        .setup(|app| {
            let login_launch = std::env::args().any(|arg| arg == login::ARGUMENT);
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("RACP Client")
                    .inner_size(860.0, 760.0)
                    .min_inner_size(620.0, 620.0)
                    .visible(!login_launch)
                    .on_navigation(navigation)
                    .build()?;
            if tray(app.handle()).is_err() {
                window.show()?;
                if let Ok(mut view) = app.state::<Arc<Controller>>().overview.lock() {
                    view["error"] =
                        serde_json::json!("트레이 아이콘을 만들 수 없습니다. 창을 열어 둡니다.");
                }
            }
            let handle = app.handle().clone();
            let state = app.state::<Arc<Controller>>().inner().clone();
            tauri::async_runtime::spawn(async move {
                if login_launch {
                    let enabled = login::settings().is_ok_and(|v| v["enabled"] == true);
                    if enabled {
                        if state
                            .request(serde_json::json!({"action":"start"}))
                            .await
                            .is_err()
                        {
                            Controller::show(&handle);
                        }
                    } else {
                        state.quitting.store(true, Ordering::Release);
                        handle.exit(0);
                        return;
                    }
                }
                while !state.quitting.load(Ordering::Acquire) {
                    state.refresh().await;
                    refresh_tray(&handle, &state);
                    tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<Arc<Controller>>();
                if !state.quitting.load(Ordering::Acquire) && state.tray.load(Ordering::Acquire) {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        });
    let app = match builder.build(tauri::generate_context!()) {
        Ok(app) => app,
        Err(_) => std::process::exit(4),
    };
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event {
            let state = app.state::<Arc<Controller>>();
            if !state.quitting.load(Ordering::Acquire) {
                api.prevent_exit();
                let state = state.inner().clone();
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    if state.exit(app.clone()).await.is_err() {
                        Controller::show(&app);
                    }
                });
            }
        }
    });
}
