//! Slint frontend (ADR-0032), migration step 1: window, tray, Agent state feed and lifecycle.
//! The window is presentation only. All state lives in `Controller` on the UI thread, and
//! background work reports back through `post`, mirroring the Iced `update` rules.
slint::include_modules!();

use crate::{
    ipc::Feed,
    native::{Shell, ShellEvent},
    onboarding::LocalInfo,
    ui_model::UiModel,
};
use racp_contract::{
    local_ipc::{AgentStatus, ControlCommand, ControlReply, ControlResult, StateChange},
    service_scope::ControllerRole,
};
use slint::{CloseRequestResponse, ComponentHandle};
use std::{cell::RefCell, sync::Arc};

/// Results that background tasks deliver to the UI thread.
enum Msg {
    Feed(Feed),
    Local(Result<LocalInfo, String>),
    Started(Result<serde_json::Value, String>),
    Reply(Result<ControlReply, String>),
    ExitCheck(Result<bool, String>),
    Shell(ShellEvent),
}

struct Controller {
    window: AppWindow,
    runtime: tokio::runtime::Handle,
    shell: Option<Arc<Shell>>,
    model: UiModel,
    role: Option<ControllerRole>,
    local: Option<LocalInfo>,
    notice: Option<String>,
    state_root: String,
    can_hide: bool,
    quitting: bool,
    starting: bool,
    auto_start: bool,
}

thread_local! {
    static CONTROLLER: RefCell<Option<Controller>> = const { RefCell::new(None) };
}

fn with<T>(f: impl FnOnce(&mut Controller) -> T) -> Option<T> {
    CONTROLLER.with(|cell| cell.borrow_mut().as_mut().map(f))
}

/// Delivers a background result to the UI thread. False once the event loop is gone.
fn post(msg: Msg) -> bool {
    slint::invoke_from_event_loop(move || {
        with(|c| c.handle(msg));
    })
    .is_ok()
}

pub fn run(shell: Option<Arc<Shell>>, auto_start: bool) -> Result<(), String> {
    // Slint's default backend prefers OpenGL and aborts without it, so the software renderer
    // is selected explicitly. GPU-less, VM and RDP sessions are product targets (ADR-0032).
    slint::BackendSelector::new()
        .backend_name("winit".into())
        .renderer_name("software".into())
        .select()
        .map_err(|_| "GUI_RENDERER_OR_WINDOW_FAILED".to_owned())?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_name("racp-client-io")
        .enable_all()
        .build()
        .map_err(|_| "GUI_RUNTIME_FAILED".to_owned())?;
    if crate::i18n::detect_os_language() == crate::i18n::Language::Ko {
        let _ = slint::select_bundled_translation("ko");
    }
    let window = AppWindow::new().map_err(|_| "GUI_RENDERER_OR_WINDOW_FAILED".to_owned())?;
    window.set_version(racp_contract::VERSION.into());
    wire(&window);

    let receiver = shell.as_ref().map(|shell| shell.receiver.clone());
    let can_hide = shell.is_some();
    if let Some(shell) = &shell {
        shell.control(false);
    }
    let controller = Controller {
        window: window.clone_strong(),
        runtime: runtime.handle().clone(),
        shell,
        model: UiModel::new().map_err(|e| e.to_string())?,
        role: None,
        local: None,
        notice: None,
        state_root: state_root_label(),
        can_hide,
        quitting: false,
        starting: false,
        auto_start,
    };
    CONTROLLER.with(|cell| *cell.borrow_mut() = Some(controller));

    runtime.spawn(crate::ipc::subscribe(|feed| async move {
        post(Msg::Feed(feed))
    }));
    if let Some(receiver) = receiver {
        runtime.spawn(async move {
            let Some(mut receiver) = receiver.lock().await.take() else {
                return;
            };
            while let Some(event) = receiver.recv().await {
                if !post(Msg::Shell(event)) {
                    break;
                }
            }
        });
    }
    with(|c| {
        c.refresh_local();
        c.render();
    });
    window
        .show()
        .map_err(|_| "GUI_RENDERER_OR_WINDOW_FAILED".to_owned())?;
    let result = slint::run_event_loop_until_quit();
    // Dropping the controller drops the shell, which removes the tray icon.
    CONTROLLER.with(|cell| cell.borrow_mut().take());
    drop(window);
    runtime.shutdown_timeout(std::time::Duration::from_secs(2));
    result.map_err(|_| "GUI_RENDERER_OR_WINDOW_FAILED".to_owned())
}

fn wire(window: &AppWindow) {
    window.on_emergency_stop(|| {
        with(|c| c.stop());
    });
    window.on_hide_to_tray(|| {
        with(|c| c.hide());
    });
    window.on_quit(|| {
        with(|c| c.quit());
    });
    window.on_exit_anyway(|| {
        let _ = slint::quit_event_loop();
    });
    window.window().on_close_requested(|| {
        with(|c| {
            if c.can_hide {
                CloseRequestResponse::HideWindow
            } else {
                c.quit();
                CloseRequestResponse::KeepWindowShown
            }
        })
        .unwrap_or(CloseRequestResponse::HideWindow)
    });
}

/// Last two components of the state or service control root, e.g. `RACP\native-client`.
fn state_root_label() -> String {
    let Ok(root) = crate::control_target::root() else {
        return String::new();
    };
    let parts: Vec<_> = root
        .components()
        .rev()
        .take(2)
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect();
    parts.into_iter().rev().collect::<Vec<_>>().join("\\")
}

impl Controller {
    fn service(&self) -> bool {
        crate::control_target::service()
    }

    fn can_control(&self) -> bool {
        !self.service() || !self.model.needs_resync && self.role == Some(ControllerRole::Controller)
    }

    fn handle(&mut self, msg: Msg) {
        match msg {
            Msg::Feed(Feed::Snapshot(snapshot, role)) => {
                self.role = Some(role);
                self.tray_status(u32::from(snapshot.status == AgentStatus::Online));
                self.model.replace_snapshot(snapshot);
                self.tray_control();
                if self.quitting {
                    self.send(ControlCommand::Stop);
                }
            }
            Msg::Feed(Feed::Event(event)) => {
                if !self.model.apply_event(event.clone()) {
                    self.role = None;
                    self.tray_control();
                    self.render();
                    return;
                }
                self.tray_activity();
                match &event.change {
                    StateChange::CancelFinished { .. } => {
                        self.notice = Some(
                            "작업 취소 결과를 받았습니다. 상태 화면에서 정리 결과를 확인하세요."
                                .into(),
                        );
                    }
                    StateChange::PolicyFinished {
                        committed,
                        retirement_state,
                        failed_resource_ids,
                        ..
                    } => {
                        self.notice = Some(format!(
                            "정책 저장: {committed}, 회수: {retirement_state}, 실패 자원: {}",
                            failed_resource_ids.join(", ")
                        ));
                    }
                    StateChange::StopFinished {
                        cleanup_state,
                        failed_resource_ids,
                        ..
                    } => {
                        self.notice = Some(format!(
                            "Agent 정리: {cleanup_state}, 실패 자원: {}",
                            failed_resource_ids.join(", ")
                        ));
                        if self.quitting
                            && cleanup_state == "complete"
                            && failed_resource_ids.is_empty()
                        {
                            self.exit_check(true);
                        }
                    }
                    StateChange::ApprovalPending { .. } if self.can_control() => {
                        self.window.set_current_page(2);
                        self.show();
                    }
                    _ => (),
                }
            }
            Msg::Feed(Feed::Disconnected) => {
                self.role = None;
                self.model.needs_resync = true;
                self.tray_status(0);
                self.tray_control();
                if self.quitting {
                    self.exit_check(true);
                }
            }
            Msg::Local(Ok(info)) => {
                let start = self.auto_start && info.state.is_some() && !info.registration_pending;
                self.auto_start = false;
                self.local = Some(info);
                if start {
                    self.start_agent();
                }
            }
            Msg::Local(Err(code)) => self.notice = Some(code),
            Msg::Started(result) => {
                self.starting = false;
                let spawned = result.as_ref().is_ok_and(|value| value["spawned"] == true);
                self.notice = Some(match result {
                    Ok(value) => format!(
                        "Agent 시작 PID {} — 인증 IPC 연결을 기다립니다.",
                        value["pid"].as_u64().unwrap_or(0)
                    ),
                    Err(code) => format!("Agent 시작: {code}"),
                });
                if self.quitting && spawned {
                    self.send(ControlCommand::Stop);
                } else if self.quitting {
                    self.exit_check(true);
                }
            }
            Msg::Reply(reply) => {
                self.notice = Some(match reply {
                    Ok(reply) => match reply.result {
                        ControlResult::Accepted { receipt_id } => {
                            format!("접수: {receipt_id} — 최종 결과 대기")
                        }
                        ControlResult::Error { code } => format!("요청 거부: {code:?}"),
                        _ => "응답 수신".into(),
                    },
                    Err(error) => error,
                });
            }
            Msg::ExitCheck(Ok(true)) => {
                let _ = slint::quit_event_loop();
            }
            Msg::ExitCheck(Ok(false)) => {
                self.notice = Some(
                    "Agent 실행 또는 정리 결과가 확인되지 않았습니다. 종료 요청을 다시 확인하세요."
                        .into(),
                );
                self.show();
            }
            Msg::ExitCheck(Err(code)) => {
                self.notice = Some(format!("종료 확인: {code}"));
                self.show();
            }
            Msg::Shell(ShellEvent::Show) => self.show(),
            Msg::Shell(ShellEvent::StopAgent) => self.stop(),
            Msg::Shell(ShellEvent::Quit) => self.quit(),
            Msg::Shell(ShellEvent::Failed) => {
                self.can_hide = false;
                self.notice = Some("트레이를 복구하지 못했습니다. 창을 열어 둡니다.".into());
                self.show();
            }
        }
        self.render();
    }

    fn refresh_local(&mut self) {
        if self.service() {
            return;
        }
        self.runtime.spawn(async {
            let result = tokio::task::spawn_blocking(crate::onboarding::load_local)
                .await
                .map_err(|_| "LOCAL_STATE_FAILED".to_owned())
                .and_then(|result| result);
            post(Msg::Local(result));
        });
    }

    fn start_agent(&mut self) {
        if self.service()
            || !self.model.needs_resync
            || self.starting
            || self.local.as_ref().is_none_or(|info| info.state.is_none())
        {
            return;
        }
        self.starting = true;
        self.notice = Some("Agent 시작 요청 중".into());
        self.runtime.spawn(async {
            let result = tokio::task::spawn_blocking(crate::onboarding::start_local_agent)
                .await
                .map_err(|_| "EXECUTION_UNKNOWN".to_owned())
                .and_then(|result| result);
            post(Msg::Started(result));
        });
    }

    fn send(&mut self, command: ControlCommand) {
        self.runtime.spawn(async move {
            post(Msg::Reply(crate::ipc::request(command).await));
        });
    }

    fn stop(&mut self) {
        if !self.can_control() {
            self.notice = Some("현재 연결에는 조회 권한만 있습니다.".into());
        } else {
            self.send(ControlCommand::Stop);
        }
        self.render();
    }

    fn exit_check(&mut self, wait: bool) {
        if self.service() {
            post(Msg::ExitCheck(Ok(true)));
            return;
        }
        self.runtime.spawn(async move {
            let result =
                tokio::task::spawn_blocking(move || crate::onboarding::stopped_cleanly(wait))
                    .await
                    .map_err(|_| "LOCAL_STATE_FAILED".to_owned())
                    .and_then(|result| result);
            post(Msg::ExitCheck(result));
        });
    }

    fn quit(&mut self) {
        self.quitting = true;
        if self.service() {
            // Step 1 offers no service desktop session, so nothing needs cleanup here.
            let _ = slint::quit_event_loop();
            return;
        }
        if self.starting {
            self.notice = Some("Agent 시작 결과를 확인한 뒤 종료합니다.".into());
        } else if self.model.needs_resync {
            self.exit_check(false);
        } else {
            self.notice = Some("Agent 종료와 자원 회수 결과를 기다립니다.".into());
            self.send(ControlCommand::Stop);
        }
        self.render();
    }

    fn hide(&mut self) {
        if self.can_hide {
            // Hiding destroys the native window, so leave the current input event first.
            let window = self.window.as_weak();
            let _ = slint::invoke_from_event_loop(move || {
                if let Some(window) = window.upgrade() {
                    let _ = window.hide();
                }
            });
        }
    }

    fn show(&mut self) {
        let _ = self.window.show();
        use slint::winit_030::WinitWindowAccessor;
        self.window.window().with_winit_window(|window| {
            window.set_minimized(false);
            window.focus_window();
        });
    }

    fn tray_status(&self, status: u32) {
        if let Some(shell) = &self.shell {
            shell.status(status);
        }
    }

    fn tray_control(&self) {
        if let Some(shell) = &self.shell {
            shell.control(self.can_control() && !self.model.needs_resync);
        }
    }

    /// Offline 0, online 1, busy 2 (pending approval or running work).
    fn tray_activity(&self) {
        let Some(snapshot) = &self.model.snapshot else {
            return;
        };
        self.tray_status(if snapshot.status != AgentStatus::Online {
            0
        } else if !snapshot.pending_approvals.is_empty()
            || snapshot.activity.iter().any(|activity| {
                matches!(
                    activity.state.as_str(),
                    "RUNNING" | "DISPATCHED" | "CANCEL_REQUESTED"
                )
            })
        {
            2
        } else {
            1
        });
    }

    fn render(&self) {
        let snapshot = self.model.snapshot.as_ref();
        let registered = self.local.as_ref().is_some_and(|info| info.state.is_some());
        let agent_status = match snapshot {
            Some(snapshot) if !self.model.needs_resync => match snapshot.status {
                AgentStatus::Online => 3,
                AgentStatus::Connecting | AgentStatus::Draining => 2,
                AgentStatus::Stopped => 1,
                AgentStatus::Unregistered => 0,
                AgentStatus::Offline | AgentStatus::Failed => 4,
            },
            _ if self.service() => 4,
            _ if registered || self.local.is_none() => 1,
            _ => 0,
        };
        let device_id = snapshot
            .and_then(|s| s.identity.as_ref())
            .map(|identity| identity.device_id.clone())
            .or_else(|| {
                self.local
                    .as_ref()
                    .and_then(|info| info.state.as_ref())
                    .map(|state| state.device_id.clone())
            })
            .unwrap_or_default();
        let device_id_short = if device_id.chars().count() > 16 {
            device_id.chars().take(16).collect::<String>() + "…"
        } else if device_id.is_empty() {
            "—".into()
        } else {
            device_id.clone()
        };
        self.window.set_shell(ShellModel {
            agent_status,
            service: self.service(),
            role: match self.role {
                Some(ControllerRole::Controller) => 1,
                Some(ControllerRole::Observer) => 2,
                None => 0,
            },
            can_control: self.can_control() && !self.model.needs_resync,
            can_hide: self.can_hide,
            pending_approvals: snapshot.map_or(0, |s| s.pending_approvals.len() as i32),
            device_id: device_id.into(),
            device_id_short: device_id_short.into(),
            state_root: self.state_root.as_str().into(),
        });
        self.window
            .set_notice(self.notice.clone().unwrap_or_default().into());
        self.window.set_quitting(self.quitting);
    }
}
