//! Slint frontend (ADR-0032). Step 1: window, tray, Agent state feed and lifecycle.
//! Step 2: Status view (registration, Agent start/stop, metrics, operations, receipts).
//! The window is presentation only. All state lives in `Controller` on the UI thread, and
//! background work reports back through `post`, mirroring the Iced `update` rules.
slint::include_modules!();

use crate::{
    ipc::Feed,
    native::{Shell, ShellEvent},
    onboarding::{Action, LocalInfo},
    ui_model::UiModel,
};
use racp_contract::{
    local_ipc::{AgentStatus, ControlCommand, ControlReply, ControlResult, StateChange},
    service_scope::ControllerRole,
};
use slint::{CloseRequestResponse, ComponentHandle, ModelRc, VecModel};
use std::{cell::RefCell, collections::BTreeSet, rc::Rc, sync::Arc};
use tokio_util::sync::CancellationToken;

/// Results that background tasks deliver to the UI thread.
enum Msg {
    Feed(Feed),
    Local(Result<LocalInfo, String>),
    Connection(Result<serde_json::Value, String>),
    LocalAction(Action, Result<serde_json::Value, String>),
    Started(Result<serde_json::Value, String>),
    Reply(Result<ControlReply, String>),
    ExitCheck(Result<bool, String>),
    DesktopFinished(Result<(), String>),
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
    state_root_full: String,
    connection_preview: Option<serde_json::Value>,
    local_cancel: Option<CancellationToken>,
    desktop_cancel: Option<CancellationToken>,
    desktop_cleanup_failed: bool,
    /// Receipts the user cleared from the audit log view. The Agent history is unchanged.
    cleared_receipts: BTreeSet<String>,
    activity: Rc<VecModel<ActivityRow>>,
    log_lines: Rc<VecModel<LogLine>>,
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
    let activity = Rc::new(VecModel::default());
    let log_lines = Rc::new(VecModel::default());
    window.set_activity(ModelRc::from(activity.clone()));
    window.set_log_lines(ModelRc::from(log_lines.clone()));
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
        state_root_full: crate::control_target::root()
            .map(|path| path.display().to_string())
            .unwrap_or_else(|e| e.to_string()),
        connection_preview: None,
        local_cancel: None,
        desktop_cancel: None,
        desktop_cleanup_failed: false,
        cleared_receipts: BTreeSet::new(),
        activity,
        log_lines,
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
    window.on_refresh_local(|| {
        with(|c| c.refresh_local());
    });
    window.on_start_agent(|| {
        with(|c| {
            c.start_agent();
            c.render();
        });
    });
    window.on_cancel_local(|| {
        with(|c| {
            if let Some(cancel) = &c.local_cancel {
                cancel.cancel();
            }
        });
    });
    window.on_connect_desktop(|| {
        with(|c| c.connect_desktop());
    });
    window.on_disconnect_desktop(|| {
        with(|c| c.disconnect_desktop());
    });
    window.on_connection_path_edited(|_| {
        with(|c| {
            // A different file invalidates the verified preview and its hash.
            c.connection_preview = None;
            c.window.set_connection_hash("".into());
            c.render();
        });
    });
    window.on_inspect_connection(|| {
        with(|c| c.inspect_connection());
    });
    window.on_enroll(|| {
        with(|c| c.enroll());
    });
    window.on_cancel_operation(|index| {
        with(|c| c.cancel_operation(index));
    });
    window.on_clear_log(|| {
        with(|c| {
            if let Some(snapshot) = &c.model.snapshot {
                for receipt in &snapshot.recent_control_results {
                    c.cleared_receipts
                        .insert(crate::ui_model::completion_line(receipt).0);
                }
            }
            c.render();
        });
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

fn is_cancellable_state(state: &str) -> bool {
    matches!(state, "ACCEPTED" | "DISPATCHED" | "RUNNING" | "RECONCILING")
}

impl Controller {
    fn service(&self) -> bool {
        crate::control_target::service()
    }

    fn can_control(&self) -> bool {
        !self.service() || !self.model.needs_resync && self.role == Some(ControllerRole::Controller)
    }

    fn registered(&self) -> bool {
        self.local.as_ref().is_some_and(|info| info.state.is_some())
    }

    fn registration_pending(&self) -> bool {
        self.local
            .as_ref()
            .is_some_and(|info| info.registration_pending)
    }

    fn handle(&mut self, msg: Msg) {
        match msg {
            Msg::Feed(Feed::Snapshot(snapshot, role)) => {
                self.role = Some(role);
                if self
                    .model
                    .snapshot
                    .as_ref()
                    .is_some_and(|old| old.boot_id != snapshot.boot_id)
                {
                    self.cleared_receipts.clear();
                }
                self.tray_status(u32::from(snapshot.status == AgentStatus::Online));
                self.model.replace_snapshot(snapshot);
                self.tray_control();
                if self.quitting && self.local_cancel.is_none() {
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
                            "작업 취소 결과를 받았습니다. 감사 로그에서 정리 결과를 확인하세요."
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
                if self.quitting && self.local_cancel.is_none() {
                    self.exit_check(true);
                }
            }
            Msg::Local(Ok(info)) => {
                if let Some(state) = &info.state {
                    self.window
                        .set_workspace_draft(state.workspace.display().to_string().into());
                    self.window.set_profile(state.profile.as_str().into());
                }
                if self.model.needs_resync {
                    if let Some(policy) = &info.policy {
                        self.model.grants.extend(policy.grants.clone());
                        self.model.disabled_categories = policy.disabled_categories.clone();
                        self.model.constraints = Some(policy.constraints.clone());
                    }
                }
                let start = self.auto_start && info.state.is_some() && !info.registration_pending;
                self.auto_start = false;
                self.local = Some(info);
                if start {
                    self.start_agent();
                }
            }
            Msg::Local(Err(code)) => self.notice = Some(code),
            Msg::Connection(Ok(info)) => {
                self.window
                    .set_connection_hash(info["file_sha256"].as_str().unwrap_or("").into());
                self.connection_preview = Some(info);
                self.notice = None;
            }
            Msg::Connection(Err(code)) => {
                self.connection_preview = None;
                self.notice = Some(code);
            }
            Msg::LocalAction(action, result) => {
                self.local_cancel = None;
                self.notice = Some(match result {
                    Ok(value) => match action {
                        Action::Enroll => format!(
                            "새 등록 완료: {}",
                            value["device_id"].as_str().unwrap_or("")
                        ),
                        Action::AdmitRuntime => format!(
                            "런타임 승인 완료: {} — 실행 선택은 별도로 저장합니다.",
                            value["manifest_sha256"].as_str().unwrap_or("")
                        ),
                        Action::ConfigureRuntime => {
                            "런타임 설정 저장 완료. 다음 Agent 시작부터 적용됩니다.".into()
                        }
                        Action::ConfigureSettings => format!(
                            "설정 revision {} 저장 완료 — Agent를 다시 시작하세요.",
                            value["revision"]
                        ),
                        Action::ConfigureLogin => format!(
                            "로그인 시작: {} · 소유 확인: {} · 미확정 변경: {}",
                            if value["enabled"] == true {
                                "등록됨"
                            } else {
                                "해제됨"
                            },
                            value["owned"],
                            value["pending"]
                        ),
                    },
                    Err(code) => {
                        format!("로컬 요청: {code}. 결과가 불확실하면 상태를 새로 확인하세요.")
                    }
                });
                if matches!(action, Action::Enroll) {
                    self.connection_preview = None;
                }
                if self.quitting {
                    self.exit_check(false);
                } else {
                    self.refresh_local();
                }
            }
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
            Msg::DesktopFinished(result) => {
                self.desktop_cancel = None;
                self.desktop_cleanup_failed = result.as_ref().is_err_and(|error| {
                    error.contains("CLEANUP_FAILED") || error.contains("CLEANUP_UNKNOWN")
                });
                self.notice = Some(match result {
                    Ok(()) => "현재 사용자 화면 연결을 정리했습니다.".into(),
                    Err(error) => format!("현재 사용자 화면 연결: {error}"),
                });
                if self.quitting && !self.desktop_cleanup_failed {
                    let _ = slint::quit_event_loop();
                }
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
            || self.local_cancel.is_some()
            || !self.registered()
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

    fn inspect_connection(&mut self) {
        if self.service() {
            return;
        }
        let path = self.window.get_connection_path().to_string();
        self.runtime.spawn(async move {
            let result =
                tokio::task::spawn_blocking(move || crate::onboarding::inspect_file(&path))
                    .await
                    .map_err(|_| "LOCAL_STATE_FAILED".to_owned())
                    .and_then(|result| result);
            post(Msg::Connection(result));
        });
    }

    fn enroll(&mut self) {
        if self.service()
            || self.local_cancel.is_some()
            || self.registered()
            || self.registration_pending()
        {
            return;
        }
        if self.connection_preview.is_none() {
            self.notice = Some("연결 파일을 먼저 확인하세요.".into());
            self.render();
            return;
        }
        // Constraint editing moves in step 3; until then the current model constraints apply.
        let mut policy = racp_core::state_schema::NativePolicy::deny_all();
        policy.grants = self.model.grants.clone();
        policy.disabled_categories = self.model.disabled_categories.clone();
        if let Some(constraints) = &self.model.constraints {
            policy.constraints = constraints.clone();
        }
        let named: Vec<racp_core::WorkspaceSpec> = Vec::new();
        let input = serde_json::json!({
            "connection_file": self.window.get_connection_path().as_str(),
            "connection_sha256": self.window.get_connection_hash().as_str(),
            "workspace": self.window.get_workspace_draft().as_str(),
            "allowed_workspaces": named,
            "profile": self.window.get_profile().as_str(),
            "policy": policy,
        });
        self.local_action(Action::Enroll, input);
    }

    fn local_action(&mut self, action: Action, input: serde_json::Value) {
        if self.local_cancel.is_some() {
            return;
        }
        let cancel = CancellationToken::new();
        self.local_cancel = Some(cancel.clone());
        self.notice = Some("로컬 요청 처리 중".into());
        self.runtime.spawn(async move {
            let result = crate::onboarding::run_action(action, input, cancel).await;
            post(Msg::LocalAction(action, result));
        });
        self.render();
    }

    fn cancel_operation(&mut self, index: i32) {
        let target = self.model.snapshot.as_ref().and_then(|snapshot| {
            let position = snapshot.activity.len().checked_sub(1 + index as usize)?;
            let item = snapshot.activity.get(position)?;
            Some((
                snapshot.boot_id.clone(),
                item.operation_id.clone(),
                item.request_id.clone(),
                item.state.clone(),
            ))
        });
        let Some((boot_id, operation_id, target_request_id, state)) = target else {
            return;
        };
        if !self.can_control()
            || self.model.needs_resync
            || self.quitting
            || operation_id.is_empty()
            || target_request_id.is_empty()
            || !is_cancellable_state(&state)
        {
            self.notice =
                Some("작업 상태가 변경되었습니다. 현재 Agent 상태를 다시 확인하세요.".into());
            self.render();
            return;
        }
        self.send(ControlCommand::CancelOperation {
            boot_id,
            operation_id,
            target_request_id,
        });
    }

    fn connect_desktop(&mut self) {
        if !self.service()
            || !self.can_control()
            || self.desktop_cancel.is_some()
            || self.quitting
            || self.desktop_cleanup_failed
        {
            return;
        }
        let root = match crate::control_target::root() {
            Ok(root) => root,
            Err(error) => {
                self.notice = Some(error.to_string());
                self.render();
                return;
            }
        };
        let cancel = CancellationToken::new();
        self.desktop_cancel = Some(cancel.clone());
        self.notice =
            Some("현재 사용자 화면 연결 중 — 상태는 Agent의 세션 목록에서 확인합니다.".into());
        self.runtime.spawn(async move {
            let result = racp_runtime::providers::desktop::run_service_session(&root, cancel)
                .await
                .map_err(|error| error.to_string());
            post(Msg::DesktopFinished(result));
        });
        self.render();
    }

    fn disconnect_desktop(&mut self) {
        if let Some(cancel) = &self.desktop_cancel {
            cancel.cancel();
            self.notice = Some("현재 사용자 화면 연결과 입력 정리를 기다립니다.".into());
            self.render();
        }
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
            if self.desktop_cancel.is_some() {
                self.disconnect_desktop();
            } else if self.desktop_cleanup_failed {
                self.notice = Some(
                    "화면 입력 정리를 확인하지 못했습니다. 정리 결과를 확인한 뒤 종료하세요."
                        .into(),
                );
                self.show();
            } else {
                let _ = slint::quit_event_loop();
            }
            self.render();
            return;
        }
        if self.starting {
            self.notice = Some("Agent 시작 결과를 확인한 뒤 종료합니다.".into());
        } else if let Some(cancel) = &self.local_cancel {
            cancel.cancel();
            self.notice = Some("로컬 요청 정리 후 종료합니다.".into());
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
        let registered = self.registered();
        let connected = snapshot.is_some() && !self.model.needs_resync;
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
        let local_state = self.local.as_ref().and_then(|info| info.state.as_ref());
        let identity = snapshot.and_then(|s| s.identity.as_ref());
        let device_id = identity
            .map(|identity| identity.device_id.clone())
            .or_else(|| local_state.map(|state| state.device_id.clone()))
            .unwrap_or_default();
        let device_id_short = if device_id.chars().count() > 16 {
            device_id.chars().take(16).collect::<String>() + "…"
        } else if device_id.is_empty() {
            "—".into()
        } else {
            device_id.clone()
        };
        let can_control = self.can_control() && !self.model.needs_resync;
        self.window.set_shell(ShellModel {
            agent_status,
            service: self.service(),
            role: match self.role {
                Some(ControllerRole::Controller) => 1,
                Some(ControllerRole::Observer) => 2,
                None => 0,
            },
            can_control,
            can_hide: self.can_hide,
            pending_approvals: snapshot.map_or(0, |s| s.pending_approvals.len() as i32),
            device_id: device_id.as_str().into(),
            device_id_short: device_id_short.into(),
            state_root: self.state_root.as_str().into(),
        });

        let active_count = snapshot.map_or(0, |s| {
            s.activity
                .iter()
                .filter(|a| matches!(a.state.as_str(), "RUNNING" | "DISPATCHED" | "ACCEPTED"))
                .count() as i32
        });
        let busy_lifecycle = snapshot.is_some_and(|s| {
            matches!(
                s.status,
                AgentStatus::Draining | AgentStatus::Stopped | AgentStatus::Failed
            )
        });
        self.window.set_status(StatusModel {
            connected,
            sequence: snapshot.map_or(0, |s| s.sequence).to_string().into(),
            boot_id: snapshot
                .map(|s| s.boot_id.chars().take(12).collect::<String>())
                .unwrap_or_default()
                .into(),
            policy_revision: snapshot.map_or(0, |s| s.policy_revision).to_string().into(),
            active_count,
            pending_count: snapshot.map_or(0, |s| s.pending_approvals.len() as i32),
            activity_stale: self.model.needs_resync,
            registered: registered || self.service(),
            registration_pending: self.registration_pending(),
            device_id: device_id.into(),
            gateway: identity
                .map(|identity| identity.gateway_origin.clone())
                .or_else(|| local_state.map(|state| state.gateway.clone()))
                .unwrap_or_default()
                .into(),
            workspace: local_state
                .map(|state| state.workspace.display().to_string())
                .unwrap_or_default()
                .into(),
            profile: local_state
                .map(|state| state.profile.clone())
                .unwrap_or_default()
                .into(),
            state_root: self.state_root_full.as_str().into(),
            can_start: !self.service()
                && registered
                && !self.registration_pending()
                && self.model.needs_resync
                && !self.starting
                && self.local_cancel.is_none(),
            can_stop: can_control && !self.quitting,
            local_busy: self.local_cancel.is_some(),
            can_enroll: !self.service()
                && !registered
                && !self.registration_pending()
                && self.local_cancel.is_none(),
            can_connect_desktop: self.service()
                && can_control
                && self.desktop_cancel.is_none()
                && !self.quitting
                && !self.desktop_cleanup_failed,
            desktop_connected: self
                .desktop_cancel
                .as_ref()
                .is_some_and(|token| !token.is_cancelled()),
        });

        let rows: Vec<ActivityRow> = snapshot
            .map(|s| {
                s.activity
                    .iter()
                    .rev()
                    .map(|item| ActivityRow {
                        operation_id: item.operation_id.as_str().into(),
                        operation: item.operation.as_str().into(),
                        state: item.state.as_str().into(),
                        request_id: item.request_id.as_str().into(),
                        cancellable: can_control
                            && !self.quitting
                            && !busy_lifecycle
                            && !item.operation_id.is_empty()
                            && !item.request_id.is_empty()
                            && is_cancellable_state(&item.state),
                    })
                    .collect()
            })
            .unwrap_or_default();
        self.activity.set_vec(rows);

        let lines: Vec<LogLine> = snapshot
            .map(|s| {
                s.recent_control_results
                    .iter()
                    .rev()
                    .map(crate::ui_model::completion_line)
                    .filter(|(receipt, _, _)| !self.cleared_receipts.contains(receipt))
                    .map(|(receipt, message, tone)| LogLine {
                        receipt: receipt.into(),
                        message: message.into(),
                        tone,
                    })
                    .collect()
            })
            .unwrap_or_default();
        self.log_lines.set_vec(lines);

        let preview = self.connection_preview.as_ref();
        self.window.set_preview(EnrollPreview {
            shown: preview.is_some(),
            gateway: preview
                .and_then(|info| info["gateway"].as_str())
                .unwrap_or("")
                .into(),
            expires: preview
                .and_then(|info| info["expires_at"].as_str())
                .unwrap_or("")
                .into(),
            ca_hash: preview
                .and_then(|info| info["ca_sha256"].as_str())
                .unwrap_or("없음")
                .into(),
        });
        self.window
            .set_notice(self.notice.clone().unwrap_or_default().into());
        self.window.set_quitting(self.quitting);
    }
}
