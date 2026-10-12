use crate::controls::{
    button, checkbox, constraint_editor, profile_picklist, scrollable, text, text_input,
};
use crate::ui_model::UiModel;
use iced::{
    widget::{column, container, row, text_editor},
    Element, Length, Task,
};
use racp_contract::local_ipc::{
    AvailabilityState, ControlCommand, ControlReply, ControlResult, Grant, PolicyChanges,
    StateChange,
};
use racp_contract::service_scope::ControllerRole;

fn keyboard_navigation(
    key: iced::keyboard::Key,
    modifiers: iced::keyboard::Modifiers,
) -> Option<Message> {
    use iced::keyboard::{key::Named, Key};
    match key {
        Key::Named(Named::Tab) if !modifiers.command() && !modifiers.alt() => {
            Some(Message::MoveFocus(modifiers.shift()))
        }
        Key::Character(value) if modifiers.command() && !modifiers.alt() => match value.as_str() {
            "1" => Some(Message::Navigate(Page::Status)),
            "2" => Some(Message::Navigate(Page::Permissions)),
            "3" => Some(Message::Navigate(Page::Approvals)),
            "4" => Some(Message::Navigate(Page::Settings)),
            "5" => Some(Message::Navigate(Page::Runtime)),
            _ => None,
        },
        _ => None,
    }
}
fn completion_summary(receipt: &racp_contract::local_ipc::ControlCompletion) -> (&str, String) {
    use racp_contract::local_ipc::ControlCompletion;
    match receipt {
        ControlCompletion::Cancellation { receipt_id, operation_id, cancel_requested, state, terminal, cleanup_status, error_code } => (
            receipt_id, format!("작업 {operation_id} · 취소 신호 {cancel_requested} · 관찰 상태 {state} · 종료 {terminal} · 정리 {cleanup_status}{}", error_code.as_ref().map(|code| format!(" · {code}")).unwrap_or_default()),
        ),
        ControlCompletion::Policy { receipt_id, revision, committed, retirement_state, failed_resource_ids } => (
            receipt_id, format!("정책 revision {revision} · 저장 {committed} · 회수 {retirement_state} · 실패 자원 {}", failed_resource_ids.join(", ")),
        ),
        ControlCompletion::Stop { receipt_id, cleanup_state, failed_resource_ids } => (
            receipt_id, format!("Agent 정리 {cleanup_state} · 실패 자원 {}", failed_resource_ids.join(", ")),
        ),
    }
}

pub struct App {
    model: UiModel,
    constraints: crate::constraints::Draft,
    page: Page,
    error: Option<String>,
    workspace_draft: String,
    named_workspaces: Vec<racp_core::WorkspaceSpec>,
    local: Option<crate::onboarding::LocalInfo>,
    connection_path: String,
    connection_hash: String,
    connection_preview: Option<serde_json::Value>,
    profile: String,
    runtime_manifest: String,
    runtime_hash: String,
    runtime_source: String,
    chromium_hash: String,
    cdb_hash: String,
    gdb_hash: String,
    analysis_hash: String,
    browser_origins: String,
    external_cdp: bool,
    local_cancel: Option<tokio_util::sync::CancellationToken>,
    desktop_cancel: Option<tokio_util::sync::CancellationToken>,
    desktop_cleanup_failed: bool,
    receipts: std::collections::BTreeMap<String, String>,
    #[cfg(windows)]
    shell: Option<std::sync::Arc<crate::native::Shell>>,
    can_hide: bool,
    quitting: bool,
    starting: bool,
    auto_start: bool,
    role: Option<ControllerRole>,
    accessibility_attempts: u8,
    window_ready: bool,
}
#[derive(Clone, Copy, Debug)]
pub enum Page {
    Status,
    Permissions,
    Runtime,
    Approvals,
    Settings,
}
#[derive(Clone, Debug)]
pub enum Message {
    Navigate(Page),
    EditGrant(String, Grant),
    Snapshot(racp_contract::local_ipc::StateSnapshot, ControllerRole),
    Event(racp_contract::local_ipc::StateEvent),
    Disconnected,
    WorkspaceDraft(String),
    AddWorkspace,
    RemoveWorkspace(usize),
    WorkspaceId(usize, String),
    WorkspacePath(usize, String),
    SaveSettings,
    ConfigureLogin(bool),
    ReconcileLogin,
    SavePolicy,
    EditConstraint(crate::constraints::Field, String),
    EditConstraintList(crate::constraints::Field, text_editor::Action),
    ReloadConstraints,
    Decide(String, bool),
    CancelOperation(String, String, String),
    Stop,
    Reply(Result<ControlReply, String>),
    CategoryChoice(String, bool),
    ConnectionPath(String),
    ConnectionHash(String),
    Profile(String),
    InspectConnection,
    Enroll,
    RefreshLocal,
    LocalInfo(Result<crate::onboarding::LocalInfo, String>),
    ConnectionInfo(Result<serde_json::Value, String>),
    LocalAction(crate::onboarding::Action, Result<serde_json::Value, String>),
    CancelLocal,
    RuntimeManifest(String),
    RuntimeHash(String),
    RuntimeSource(String),
    ChromiumHash(String),
    CdbHash(String),
    GdbHash(String),
    AnalysisHash(String),
    BrowserOrigins(String),
    ExternalCdp(bool),
    AdmitRuntime,
    ConfigureRuntime,
    #[cfg(windows)]
    Shell(crate::native::ShellEvent),
    Close(iced::window::Id),
    Quit,
    ExitCheck(Result<bool, String>),
    ExitAnyway,
    StartAgent,
    StartResult(Result<serde_json::Value, String>),
    ConnectDesktop,
    DisconnectDesktop,
    DesktopFinished(Result<(), String>),
    MoveFocus(bool),
    Accessible(crate::accessibility::Request),
    AttachAccessibility(Option<iced::window::Id>),
    AccessibilityAttached(iced::window::Id, Result<(), String>),
}
impl App {
    pub fn new(auto_start: bool) -> (Self, Task<Message>) {
        (
            Self {
                model: UiModel::new().expect("maintained catalog"),
                constraints: crate::constraints::Draft::new(),
                page: Page::Status,
                error: None,
                workspace_draft: String::new(),
                named_workspaces: vec![],
                local: None,
                connection_path: String::new(),
                connection_hash: String::new(),
                connection_preview: None,
                profile: "read_only".into(),
                runtime_manifest: String::new(),
                runtime_hash: String::new(),
                runtime_source: String::new(),
                chromium_hash: String::new(),
                cdb_hash: String::new(),
                gdb_hash: String::new(),
                analysis_hash: String::new(),
                browser_origins: String::new(),
                external_cdp: false,
                local_cancel: None,
                desktop_cancel: None,
                desktop_cleanup_failed: false,
                receipts: Default::default(),
                #[cfg(windows)]
                shell: None,
                can_hide: false,
                quitting: false,
                starting: false,
                auto_start,
                role: None,
                accessibility_attempts: 0,
                window_ready: !cfg!(windows),
            },
            Task::batch([
                crate::onboarding::refresh(),
                iced::window::get_oldest().map(Message::AttachAccessibility),
            ]),
        )
    }
    pub fn title(&self) -> String {
        format!(
            "RACP {} v{}",
            if crate::control_target::service() {
                "서비스 제어"
            } else {
                "Native Client"
            },
            racp_contract::VERSION
        )
    }
    fn can_control(&self) -> bool {
        !crate::control_target::service()
            || !self.model.needs_resync && self.role == Some(ControllerRole::Controller)
    }
    fn sync_constraints(&mut self) {
        let Some(snapshot) = &self.model.snapshot else {
            return;
        };
        let Some(policy) = &snapshot.policy else {
            return;
        };
        let same = self.constraints.parse().ok().is_some_and(|draft| {
            serde_json::to_value(draft).ok() == serde_json::to_value(&policy.constraints).ok()
        });
        if !self.constraints.dirty || same {
            self.constraints
                .replace(&policy.constraints, snapshot.policy_revision);
        }
    }
    #[cfg(windows)]
    pub fn attach_shell(&mut self, shell: std::sync::Arc<crate::native::Shell>) {
        shell.control(self.can_control() && !self.model.needs_resync);
        self.shell = Some(shell);
        self.can_hide = true;
    }
    pub fn update(&mut self, message: Message) -> Task<Message> {
        if crate::control_target::service()
            && matches!(
                &message,
                Message::SaveSettings
                    | Message::ConfigureLogin(_)
                    | Message::ReconcileLogin
                    | Message::StartAgent
                    | Message::Enroll
                    | Message::InspectConnection
                    | Message::AdmitRuntime
                    | Message::ConfigureRuntime
            )
        {
            self.error = Some("서비스 설정과 등록은 서비스 관리 절차에서 변경합니다.".into());
            return Task::none();
        }
        if !self.can_control()
            && matches!(
                &message,
                Message::SavePolicy
                    | Message::Decide(_, _)
                    | Message::CancelOperation(_, _, _)
                    | Message::Stop
                    | Message::EditGrant(_, _)
                    | Message::CategoryChoice(_, _)
                    | Message::EditConstraint(_, _)
                    | Message::EditConstraintList(_, _)
                    | Message::ConnectDesktop
            )
        {
            self.error = Some("현재 연결에는 조회 권한만 있습니다.".into());
            return Task::none();
        }
        match message {
            Message::Accessible(request) => match crate::accessibility::resolve(request) {
                Some(crate::accessibility::Effect::Message(message)) => {
                    return self.update(message)
                }
                Some(crate::accessibility::Effect::Focus(id)) => return crate::controls::focus(id),
                None => (),
            },
            Message::AttachAccessibility(Some(id)) => {
                #[cfg(windows)]
                return iced::window::run_with_handle(id, crate::accessibility::attach)
                    .map(move |result| Message::AccessibilityAttached(id, result));
                #[cfg(not(windows))]
                return iced::window::change_mode(id, iced::window::Mode::Windowed);
            }
            Message::AttachAccessibility(None) => {
                self.accessibility_attempts += 1;
                if self.accessibility_attempts < 5 {
                    return Task::perform(
                        async { tokio::time::sleep(std::time::Duration::from_millis(200)).await },
                        |()| (),
                    )
                    .then(|()| iced::window::get_oldest().map(Message::AttachAccessibility));
                }
                #[cfg(windows)]
                crate::native::startup_error("ACCESSIBILITY_WINDOW_UNAVAILABLE");
                return iced::exit();
            }
            Message::AccessibilityAttached(id, result) => {
                self.window_ready = true;
                if let Err(error) = result {
                    self.error = Some(format!("접근성 연결: {error}"));
                }
                return iced::window::change_mode(id, iced::window::Mode::Windowed);
            }
            Message::MoveFocus(backwards) => return crate::controls::move_focus(backwards),
            Message::ConnectDesktop => {
                if !crate::control_target::service()
                    || self.desktop_cancel.is_some()
                    || self.quitting
                    || self.desktop_cleanup_failed
                {
                    return Task::none();
                }
                #[cfg(windows)]
                {
                    let root = match crate::control_target::root() {
                        Ok(root) => root,
                        Err(error) => {
                            self.error = Some(error.to_string());
                            return Task::none();
                        }
                    };
                    let cancel = tokio_util::sync::CancellationToken::new();
                    self.desktop_cancel = Some(cancel.clone());
                    self.error = Some(
                        "현재 사용자 화면 연결 중 — 상태는 Agent의 세션 목록에서 확인합니다."
                            .into(),
                    );
                    return Task::perform(
                        async move {
                            racp_runtime::providers::desktop::run_service_session(&root, cancel)
                                .await
                                .map_err(|error| error.to_string())
                        },
                        Message::DesktopFinished,
                    );
                }
            }
            Message::DisconnectDesktop => {
                if let Some(cancel) = &self.desktop_cancel {
                    cancel.cancel();
                }
                self.error = Some("현재 사용자 화면 연결과 입력 정리를 기다립니다.".into());
            }
            Message::DesktopFinished(result) => {
                self.desktop_cancel = None;
                self.desktop_cleanup_failed = result.as_ref().is_err_and(|error| {
                    error.contains("CLEANUP_FAILED") || error.contains("CLEANUP_UNKNOWN")
                });
                self.error = Some(match result {
                    Ok(()) => "현재 사용자 화면 연결을 정리했습니다.".into(),
                    Err(error) => format!("현재 사용자 화면 연결: {error}"),
                });
                if self.quitting && !self.desktop_cleanup_failed {
                    return iced::exit();
                }
            }
            Message::ConfigureLogin(enabled) => return self.configure_login(enabled, false),
            Message::ReconcileLogin => return self.configure_login(false, true),
            Message::AddWorkspace => {
                if self.named_workspaces.len() < 15 {
                    self.named_workspaces.push(racp_core::WorkspaceSpec {
                        id: String::new(),
                        path: Default::default(),
                    });
                }
            }
            Message::RemoveWorkspace(index) => {
                if index < self.named_workspaces.len() {
                    self.named_workspaces.remove(index);
                }
            }
            Message::WorkspaceId(index, value) => {
                if let Some(row) = self.named_workspaces.get_mut(index) {
                    row.id = value;
                }
            }
            Message::WorkspacePath(index, value) => {
                if let Some(row) = self.named_workspaces.get_mut(index) {
                    row.path = value.into();
                }
            }
            Message::SaveSettings => {
                if !self.model.needs_resync || self.starting || self.local_cancel.is_some() {
                    self.error = Some("Agent를 완전히 종료한 뒤 설정을 저장하세요.".into());
                    return Task::none();
                }
                let Some(state) = self.local.as_ref().and_then(|info| info.state.as_ref()) else {
                    return Task::none();
                };
                let input = serde_json::json!({"expected_revision":state.settings_revision,"workspace":self.workspace_draft,
                    "allowed_workspaces":self.named_workspaces,"profile":self.profile});
                return self.local_action(crate::onboarding::Action::ConfigureSettings, input);
            }
            Message::EditConstraint(field, value) => self.constraints.edit(field, value),
            Message::EditConstraintList(field, action) => self.constraints.edit_list(field, action),
            Message::ReloadConstraints => {
                if let Some(snapshot) = &self.model.snapshot {
                    if let Some(policy) = &snapshot.policy {
                        self.constraints
                            .replace(&policy.constraints, snapshot.policy_revision);
                    }
                } else if let Some(policy) =
                    self.local.as_ref().and_then(|info| info.policy.as_ref())
                {
                    self.constraints
                        .replace(&policy.constraints, policy.revision);
                }
            }
            Message::StartAgent => {
                if !self.model.needs_resync
                    || self.starting
                    || self.local_cancel.is_some()
                    || self.local.as_ref().is_none_or(|info| info.state.is_none())
                {
                    return Task::none();
                }
                self.starting = true;
                self.error = Some("Agent 시작 요청 중".into());
                return crate::onboarding::start_agent();
            }
            Message::StartResult(result) => {
                self.starting = false;
                let spawned = result
                    .as_ref()
                    .is_ok_and(|result| result["spawned"] == true);
                self.error = Some(match result {
                    Ok(value) => format!(
                        "Agent 시작 PID {} — 인증 IPC 연결을 기다립니다.",
                        value["pid"].as_u64().unwrap_or(0)
                    ),
                    Err(code) => format!("Agent 시작: {code}"),
                });
                if self.quitting && spawned {
                    return command_task(ControlCommand::Stop);
                }
                if self.quitting {
                    return crate::onboarding::exit_check(true);
                }
            }
            #[cfg(windows)]
            Message::Shell(event) => match event {
                crate::native::ShellEvent::Show => return self.show_window(),
                crate::native::ShellEvent::StopAgent => return self.update(Message::Stop),
                crate::native::ShellEvent::Quit => return self.update(Message::Quit),
                crate::native::ShellEvent::Failed => {
                    self.can_hide = false;
                    self.error = Some("트레이를 복구하지 못했습니다. 창을 열어 둡니다.".into());
                    return self.show_window();
                }
            },
            Message::Close(id) => {
                if self.can_hide {
                    return iced::window::change_mode(id, iced::window::Mode::Hidden);
                }
                return self.update(Message::Quit);
            }
            Message::Quit => {
                if crate::control_target::service() {
                    self.quitting = true;
                    if self.desktop_cancel.is_some() {
                        return self.update(Message::DisconnectDesktop);
                    }
                    if self.desktop_cleanup_failed {
                        self.error=Some("화면 입력 정리를 확인하지 못했습니다. 정리 결과를 확인한 뒤 종료하세요.".into());
                        return self.show_window();
                    }
                    return iced::exit();
                }
                self.quitting = true;
                if self.starting {
                    self.error = Some("Agent 시작 결과를 확인한 뒤 종료합니다.".into());
                    return Task::none();
                }
                if let Some(cancel) = &self.local_cancel {
                    cancel.cancel();
                    self.error = Some("로컬 요청 정리 후 종료합니다.".into());
                    return Task::none();
                }
                if self.model.needs_resync {
                    return crate::onboarding::exit_check(false);
                }
                self.error = Some("Agent 종료와 자원 회수 결과를 기다립니다.".into());
                return command_task(ControlCommand::Stop);
            }
            Message::ExitCheck(result) => match result {
                Ok(true) => return iced::exit(),
                Ok(false) => {
                    self.error=Some("Agent 실행 또는 정리 결과가 확인되지 않았습니다. 종료 요청을 다시 확인하세요.".into());
                    return self.show_window();
                }
                Err(code) => {
                    self.error = Some(format!("종료 확인: {code}"));
                    return self.show_window();
                }
            },
            Message::ExitAnyway => return iced::exit(),
            Message::ConnectionPath(value) => {
                self.connection_path = value;
                self.connection_preview = None;
                self.connection_hash.clear();
            }
            Message::ConnectionHash(value) => self.connection_hash = value,
            Message::Profile(value) => self.profile = value,
            Message::RuntimeManifest(value) => self.runtime_manifest = value,
            Message::RuntimeHash(value) => self.runtime_hash = value,
            Message::RuntimeSource(value) => self.runtime_source = value,
            Message::ChromiumHash(value) => self.chromium_hash = value,
            Message::CdbHash(value) => self.cdb_hash = value,
            Message::GdbHash(value) => self.gdb_hash = value,
            Message::AnalysisHash(value) => self.analysis_hash = value,
            Message::BrowserOrigins(value) => self.browser_origins = value,
            Message::ExternalCdp(value) => self.external_cdp = value,
            Message::RefreshLocal => return crate::onboarding::refresh(),
            Message::InspectConnection => {
                return crate::onboarding::inspect(self.connection_path.clone())
            }
            Message::CancelLocal => {
                if let Some(cancel) = &self.local_cancel {
                    cancel.cancel();
                }
            }
            Message::LocalInfo(result) => match result {
                Ok(info) => {
                    if let Some(state) = &info.state {
                        self.workspace_draft = state.workspace.display().to_string();
                        self.named_workspaces = state.allowed_workspaces.clone();
                        self.profile = state.profile.clone();
                    }
                    if self.model.needs_resync {
                        if let Some(policy) = &info.policy {
                            self.model.grants.extend(policy.grants.clone());
                            self.model.disabled_categories = policy.disabled_categories.clone();
                            self.model.constraints = Some(policy.constraints.clone());
                            if !self.constraints.dirty {
                                self.constraints
                                    .replace(&policy.constraints, policy.revision);
                            }
                        }
                    }
                    self.chromium_hash = info.runtime.chromium_manifest.clone().unwrap_or_default();
                    self.cdb_hash = info.runtime.cdb_manifest.clone().unwrap_or_default();
                    self.gdb_hash = info.runtime.gdb_manifest.clone().unwrap_or_default();
                    self.analysis_hash = info.runtime.analysis_manifest.clone().unwrap_or_default();
                    self.browser_origins = info.runtime.browser_allow_origins.join("\n");
                    self.external_cdp = info.runtime.external_cdp;
                    self.local = Some(info);
                    if self.auto_start {
                        self.auto_start = false;
                        if self
                            .local
                            .as_ref()
                            .is_some_and(|info| info.state.is_some() && !info.registration_pending)
                        {
                            return self.update(Message::StartAgent);
                        }
                    }
                }
                Err(code) => self.error = Some(code),
            },
            Message::ConnectionInfo(result) => match result {
                Ok(info) => {
                    self.connection_hash = info["file_sha256"].as_str().unwrap_or("").into();
                    self.connection_preview = Some(info);
                    self.error = None;
                }
                Err(code) => {
                    self.connection_preview = None;
                    self.error = Some(code);
                }
            },
            Message::LocalAction(action, result) => {
                self.local_cancel = None;
                self.error = Some(match result {
                    Ok(value) => match action {
                        crate::onboarding::Action::Enroll => format!(
                            "새 등록 완료: {}",
                            value["device_id"].as_str().unwrap_or("")
                        ),
                        crate::onboarding::Action::AdmitRuntime => format!(
                            "런타임 승인 완료: {} — 실행 선택은 별도로 저장합니다.",
                            value["manifest_sha256"].as_str().unwrap_or("")
                        ),
                        crate::onboarding::Action::ConfigureRuntime => {
                            "런타임 설정 저장 완료. 다음 Agent 시작부터 적용됩니다.".into()
                        }
                        crate::onboarding::Action::ConfigureSettings => {
                            format!(
                                "설정 revision {} 저장 완료 — Agent를 다시 시작하세요.",
                                value["revision"]
                            )
                        }
                        crate::onboarding::Action::ConfigureLogin => format!(
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
                if self.quitting {
                    return crate::onboarding::exit_check(false);
                }
                return crate::onboarding::refresh();
            }
            Message::Enroll => {
                if self.local_cancel.is_some()
                    || self
                        .local
                        .as_ref()
                        .is_some_and(|info| info.state.is_some() || info.registration_pending)
                {
                    return Task::none();
                }
                if self.connection_preview.is_none() {
                    self.error = Some("연결 파일을 먼저 확인하세요.".into());
                    return Task::none();
                }
                let mut policy = racp_core::state_schema::NativePolicy::deny_all();
                policy.grants = self.model.grants.clone();
                policy.disabled_categories = self.model.disabled_categories.clone();
                if let Some(constraints) = &self.model.constraints {
                    policy.constraints = constraints.clone();
                }
                if self.constraints.revision.is_some() || self.constraints.dirty {
                    match self.constraints.parse() {
                        Ok(value) => policy.constraints = value,
                        Err(_) => {
                            self.error =
                                Some("제약 값과 실행 파일의 절대 경로를 확인하세요.".into());
                            return Task::none();
                        }
                    }
                }
                let input = serde_json::json!({"connection_file":self.connection_path,"connection_sha256":self.connection_hash,"workspace":self.workspace_draft,"allowed_workspaces":self.named_workspaces,"profile":self.profile,"policy":policy});
                return self.local_action(crate::onboarding::Action::Enroll, input);
            }
            Message::AdmitRuntime => {
                if !self.model.needs_resync {
                    self.error = Some("Agent를 완전히 종료한 뒤 런타임을 승인하세요.".into());
                    return Task::none();
                }
                return self.local_action(crate::onboarding::Action::AdmitRuntime,serde_json::json!({"manifest_file":self.runtime_manifest,"manifest_sha256":self.runtime_hash,"source_directory":self.runtime_source}));
            }
            Message::ConfigureRuntime => {
                if !self.model.needs_resync {
                    self.error = Some("Agent를 완전히 종료한 뒤 런타임 설정을 저장하세요.".into());
                    return Task::none();
                }
                let Some(info) = &self.local else {
                    return Task::none();
                };
                let mut config = info.runtime.clone();
                config.revision = match config.revision.checked_add(1) {
                    Some(value) => value,
                    None => return Task::none(),
                };
                config.chromium_manifest = if self.chromium_hash.trim().is_empty() {
                    None
                } else {
                    Some(self.chromium_hash.trim().into())
                };
                config.browser_allow_origins = self
                    .browser_origins
                    .split([',', '\n'])
                    .map(str::trim)
                    .filter(|v| !v.is_empty())
                    .map(str::to_owned)
                    .collect();
                config.external_cdp = self.external_cdp;
                let selected = |hash: &str| {
                    if hash.trim().is_empty() {
                        None
                    } else {
                        Some(hash.trim().to_owned())
                    }
                };
                config.cdb_manifest = selected(&self.cdb_hash);
                config.gdb_manifest = selected(&self.gdb_hash);
                config.analysis_manifest = selected(&self.analysis_hash);
                let input =
                    serde_json::json!({"expected_revision":info.runtime.revision,"config":config});
                return self.local_action(crate::onboarding::Action::ConfigureRuntime, input);
            }
            Message::Navigate(page) => {
                if crate::control_target::service()
                    && matches!(page, Page::Settings | Page::Runtime)
                {
                    return Task::none();
                }
                self.page = page;
            }
            Message::CategoryChoice(id, enabled) => {
                self.error = self
                    .model
                    .category_enabled(&id, enabled)
                    .err()
                    .map(|e| e.to_string())
            }
            Message::EditGrant(id, grant) => {
                self.error = self
                    .model
                    .edit_grant(&id, grant)
                    .err()
                    .map(|e| e.to_string())
            }
            Message::Snapshot(snapshot, role) => {
                self.role = Some(role);
                if self
                    .model
                    .snapshot
                    .as_ref()
                    .is_some_and(|old| old.boot_id != snapshot.boot_id)
                {
                    self.receipts.clear();
                }
                for receipt in &snapshot.recent_control_results {
                    let (id, summary) = completion_summary(receipt);
                    self.receipts.insert(id.to_owned(), summary);
                }
                while self.receipts.len() > 256 {
                    self.receipts.pop_first();
                }
                #[cfg(windows)]
                if let Some(shell) = &self.shell {
                    shell.status(
                        if snapshot.status == racp_contract::local_ipc::AgentStatus::Online {
                            1
                        } else {
                            0
                        },
                    );
                }
                self.model.replace_snapshot(snapshot);
                #[cfg(windows)]
                if let Some(shell) = &self.shell {
                    shell.control(self.can_control());
                }
                self.sync_constraints();
                if self.quitting && self.local_cancel.is_none() {
                    return command_task(ControlCommand::Stop);
                }
            }
            Message::Event(event) => {
                if !self.model.apply_event(event.clone()) {
                    self.role = None;
                    #[cfg(windows)]
                    if let Some(shell) = &self.shell {
                        shell.control(false);
                    }
                    return Task::none();
                }
                self.sync_constraints();
                #[cfg(windows)]
                if let (Some(shell), Some(snapshot)) = (&self.shell, &self.model.snapshot) {
                    shell.status(
                        if snapshot.status != racp_contract::local_ipc::AgentStatus::Online {
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
                        },
                    );
                }
                match &event.change {
                    StateChange::CancelFinished { .. } => {
                        if let Some(receipt) =
                            racp_contract::local_ipc::ControlCompletion::from_change(&event.change)
                        {
                            let (id, summary) = completion_summary(&receipt);
                            self.receipts.insert(id.to_owned(), summary.clone());
                            self.error = Some(summary);
                        }
                    }
                    StateChange::PolicyFinished {
                        receipt_id,
                        committed,
                        retirement_state,
                        failed_resource_ids,
                        ..
                    } => {
                        self.error = Some(format!(
                            "정책 저장: {committed}, 회수: {retirement_state}, 실패 자원: {}",
                            failed_resource_ids.join(", ")
                        ));
                        self.receipts
                            .insert(receipt_id.clone(), self.error.clone().unwrap());
                    }
                    StateChange::StopFinished {
                        receipt_id,
                        cleanup_state,
                        failed_resource_ids,
                        ..
                    } => {
                        self.error = Some(format!(
                            "Agent 정리: {cleanup_state}, 실패 자원: {}",
                            failed_resource_ids.join(", ")
                        ));
                        self.receipts
                            .insert(receipt_id.clone(), self.error.clone().unwrap());
                        if self.quitting
                            && cleanup_state == "complete"
                            && failed_resource_ids.is_empty()
                        {
                            return crate::onboarding::exit_check(true);
                        }
                    }
                    StateChange::ApprovalPending { .. } if self.can_control() => {
                        self.page = Page::Approvals;
                        return self.show_window();
                    }
                    _ => (),
                }
                if self.receipts.len() > 256 {
                    self.receipts.pop_first();
                }
            }
            Message::Disconnected => {
                self.role = None;
                self.model.needs_resync = true;
                #[cfg(windows)]
                if let Some(shell) = &self.shell {
                    shell.status(0);
                    shell.control(false);
                }
                if self.quitting && self.local_cancel.is_none() {
                    return crate::onboarding::exit_check(true);
                }
            }
            Message::WorkspaceDraft(value) => {
                self.workspace_draft = value;
            }
            Message::SavePolicy => {
                if self.model.needs_resync {
                    self.error = Some("Agent 연결 후 저장할 수 있습니다.".into());
                    return Task::none();
                }
                let revision = self.model.snapshot.as_ref().unwrap().policy_revision;
                if self.constraints.revision != Some(revision) {
                    self.error = Some(
                        "정책이 변경되었습니다. 제약 초안을 다시 불러온 뒤 저장하세요.".into(),
                    );
                    return Task::none();
                }
                let constraints = match self.constraints.parse() {
                    Ok(value) => value,
                    Err(_) => {
                        self.error = Some("제약 값과 실행 파일의 절대 경로를 확인하세요.".into());
                        return Task::none();
                    }
                };
                return command_task(ControlCommand::UpdatePolicy {
                    expected_revision: revision,
                    changes: PolicyChanges {
                        grants: self.model.grants.clone(),
                        disabled_categories: Some(
                            self.model.disabled_categories.iter().cloned().collect(),
                        ),
                        constraints: Some(constraints),
                    },
                });
            }
            Message::Decide(nonce, allow) => {
                return command_task(ControlCommand::DecideApproval { nonce, allow })
            }
            Message::CancelOperation(boot_id, operation_id, target_request_id) => {
                if self.model.needs_resync
                    || self.quitting
                    || !self.model.snapshot.as_ref().is_some_and(|snapshot| {
                        snapshot.boot_id == boot_id
                            && snapshot.activity.iter().any(|item| {
                                item.operation_id == operation_id
                                    && item.request_id == target_request_id
                                    && matches!(
                                        item.state.as_str(),
                                        "ACCEPTED" | "DISPATCHED" | "RUNNING" | "RECONCILING"
                                    )
                            })
                    })
                {
                    self.error = Some(
                        "작업 상태가 변경되었습니다. 현재 Agent 상태를 다시 확인하세요.".into(),
                    );
                    return Task::none();
                }
                return command_task(ControlCommand::CancelOperation {
                    boot_id,
                    operation_id,
                    target_request_id,
                });
            }
            Message::Stop => return command_task(ControlCommand::Stop),
            Message::Reply(reply) => {
                self.error = Some(match reply {
                    Ok(reply) => match reply.result {
                        ControlResult::Accepted { receipt_id } => self
                            .receipts
                            .get(&receipt_id)
                            .cloned()
                            .unwrap_or_else(|| format!("접수: {receipt_id} — 최종 결과 대기")),
                        ControlResult::Error { code } => format!("요청 거부: {code:?}"),
                        _ => "응답 수신".into(),
                    },
                    Err(error) => error,
                })
            }
        }
        Task::none()
    }
    pub fn subscription(&self) -> iced::Subscription<Message> {
        #[cfg(windows)]
        {
            let mut subscriptions = vec![
                iced::Subscription::run(accessibility_events),
                iced::keyboard::on_key_press(keyboard_navigation),
                iced::Subscription::run(ipc_events),
                iced::window::close_requests().map(Message::Close),
            ];
            if let Some(shell) = &self.shell {
                let receiver = shell.receiver.clone();
                subscriptions.push(iced::Subscription::run_with_id(
                    "native-windows-shell",
                    iced::stream::channel(32, move |mut output| async move {
                        use iced::futures::SinkExt;
                        let Some(mut receiver) = receiver.lock().await.take() else {
                            std::future::pending::<()>().await;
                            return;
                        };
                        while let Some(event) = receiver.recv().await {
                            if output.send(Message::Shell(event)).await.is_err() {
                                break;
                            }
                        }
                    }),
                ));
            }
            iced::Subscription::batch(subscriptions)
        }
        #[cfg(not(windows))]
        {
            iced::Subscription::none()
        }
    }
    fn configure_login(&mut self, enabled: bool, reconcile: bool) -> Task<Message> {
        if self.local_cancel.is_some() || self.starting {
            return Task::none();
        }
        let Some(login) = self.local.as_ref().and_then(|info| info.login.as_ref()) else {
            return Task::none();
        };
        let (Some(revision), Some(hash)) =
            (login["revision"].as_u64(), login["value_sha256"].as_str())
        else {
            return Task::none();
        };
        let input = serde_json::json!({"expected_revision":revision,"expected_value_sha256":hash,"enabled":enabled,"reconcile":reconcile});
        self.local_action(crate::onboarding::Action::ConfigureLogin, input)
    }
    fn local_action(
        &mut self,
        action: crate::onboarding::Action,
        input: serde_json::Value,
    ) -> Task<Message> {
        if self.local_cancel.is_some() {
            return Task::none();
        }
        let cancel = tokio_util::sync::CancellationToken::new();
        self.local_cancel = Some(cancel.clone());
        self.error = Some("로컬 요청 처리 중".into());
        crate::onboarding::action(action, input, cancel)
    }
    #[cfg(windows)]
    fn show_window(&self) -> Task<Message> {
        if self.window_ready {
            show_window()
        } else {
            Task::none()
        }
    }
    pub fn view(&self) -> Element<'_, Message> {
        crate::accessibility::begin(self.title());
        let navigation = row![
            button("상태").on_press(Message::Navigate(Page::Status)),
            button("권한").on_press(Message::Navigate(Page::Permissions)),
            button("설정").on_press_maybe(
                (!crate::control_target::service()).then_some(Message::Navigate(Page::Settings))
            ),
            button("선택 런타임").on_press_maybe(
                (!crate::control_target::service()).then_some(Message::Navigate(Page::Runtime))
            ),
            button(format!(
                "승인 ({})",
                self.model
                    .snapshot
                    .as_ref()
                    .map_or(0, |snapshot| snapshot.pending_approvals.len())
            ))
            .accessibility_label("승인 요청")
            .on_press(Message::Navigate(Page::Approvals)),
            button("종료").on_press(Message::Quit)
        ]
        .spacing(16);
        let header = column![text(self.title()).size(24), navigation].spacing(12);
        let body: Element<'_, Message> = match self.page {
            Page::Settings | Page::Runtime if crate::control_target::service() => text("서비스 설정은 서비스 관리 절차에서 변경합니다.").into(),
            Page::Permissions if !self.can_control() => {
                let mut list = column![text("권한 조회 — 관찰 전용").size(24)];
                if let Some(policy) = self.model.snapshot.as_ref().and_then(|s| s.policy.as_ref()) {
                    list = list.push(text(format!("정책 revision {} · 최대 실행 시간 {} ms · 최대 출력 {} bytes", policy.revision, policy.constraints.max_timeout_ms, policy.constraints.max_output_bytes)));
                    for item in self.model.catalog.iter().filter(|item| {
                        item.implementation == "rpc"
                            && self.model.availability(&item.id) != AvailabilityState::NotImplemented
                    }) {
                        list = list.push(text(format!("{} · {:?}{}", item.label, policy.grants.get(&item.id).unwrap_or(&Grant::Deny), if policy.disabled_categories.contains(&item.category) { " · 분류 꺼짐" } else { "" })));
                    }
                } else { list = list.push(text("서비스 연결 후 현재 권한을 조회할 수 있습니다.")); }
                scrollable(list.spacing(12)).height(Length::Fill).into()
            }
            Page::Settings => {
                let mut list=column![text("Agent 종료 후 적용하는 설정").size(24),
                    text("저장 후 Agent를 다시 시작하세요. 권한 변경은 권한 탭에서 별도로 저장합니다."),
                    text("기본 작업 폴더 (절대 경로)"),text_input("기본 작업 폴더",&self.workspace_draft).on_input(Message::WorkspaceDraft),
                    text("실행 프로필"),profile_picklist(&self.profile),
                    text("추가 작업 폴더: 이름과 절대 경로")].spacing(12);
                for (index, workspace) in self.named_workspaces.iter().enumerate() {
                    let path = workspace.path.to_string_lossy().into_owned();
                    list = list.push(
                        row![
                            text_input("작업 영역 이름", &workspace.id)
                                .on_input(move |value| Message::WorkspaceId(index, value)),
                            text_input("절대 경로", &path)
                                .on_input(move |value| Message::WorkspacePath(index, value)),
                            button("제거").on_press(Message::RemoveWorkspace(index))
                        ]
                        .spacing(8),
                    );
                }
                list = list.push(button("작업 폴더 추가").on_press_maybe(
                    (self.named_workspaces.len() < 15).then_some(Message::AddWorkspace),
                ));
                if let Some(state) = self.local.as_ref().and_then(|info| info.state.as_ref()) {
                    list=list.push(text(format!("저장한 설정 revision: {}",state.settings_revision)))
                        .push(text(format!("등록 Gateway: {} · Device: {}",state.gateway,state.device_id)))
                        .push(text("Gateway·Device·인증서는 새 등록의 신뢰 정보입니다. 이 화면에서 변경하거나 기존 credential을 옮기지 않습니다."))
                        .push(button("설정 저장").on_press_maybe((self.model.needs_resync&&!self.starting&&self.local_cancel.is_none()).then_some(Message::SaveSettings)));
                } else {
                    list = list.push(text(
                        "추가 작업 폴더와 프로필은 상태 탭의 새 등록에도 사용됩니다.",
                    ));
                }
                list = list
                    .push(button("현재 저장된 설정 다시 불러오기").on_press(Message::RefreshLocal));
                list = list.push(text("현재 사용자 로그인 시작").size(20));
                if let Some(login) = self.local.as_ref().and_then(|info| info.login.as_ref()) {
                    if let Some(error) = login["error"].as_str() {
                        list = list.push(text(format!(
                            "로그인 시작 상태를 확인하지 못했습니다: {error}"
                        )));
                    } else {
                        list = list.push(text(format!(
                            "등록값 존재: {} · 소유 확인: {} · 미확정 변경: {}",
                            login["enabled"], login["owned"], login["pending"]
                        )));
                        if login["pending"] == true {
                            list = list.push(
                                button("미확정 등록 결과 다시 확인")
                                    .on_press(Message::ReconcileLogin),
                            );
                        } else if login["owned"] == true {
                            list = list.push(
                                button(if login["enabled"] == true {
                                    "로그인 시작 해제"
                                } else {
                                    "로그인 시 Client와 Agent 시작"
                                })
                                .on_press(Message::ConfigureLogin(login["enabled"] != true)),
                            );
                            if login["enabled"] == true {
                                list = list.push(
                                    button("현재 Client로 로그인 등록 갱신")
                                        .on_press(Message::ConfigureLogin(true)),
                                );
                            }
                        }
                    }
                    list=list.push(text("설치/포터블 payload의 고정 Client만 등록합니다. 다른 로그인 항목을 덮어쓰거나 실행 중인 앱을 종료하지 않습니다."));
                } else {
                    list = list.push(text("새 등록 후 로그인 시작을 설정할 수 있습니다."));
                }
                scrollable(list).height(Length::Fill).into()
            }
            Page::Approvals => self.approval_view(),
            Page::Status => {
                let root = crate::control_target::root()
                    .map(|p| p.display().to_string())
                    .unwrap_or_else(|e| e.to_string());
                let status = if self.model.needs_resync {
                    if crate::control_target::service() {
                        "서비스 제어 연결 대기 — 관리자에게 서비스 등록 또는 시작을 요청하세요.".to_owned()
                    } else {
                        "Agent 연결 대기 — 새 등록 또는 Agent 시작이 필요합니다.".to_owned()
                    }
                } else {
                    format!(
                        "Agent 상태: {:?}",
                        self.model.snapshot.as_ref().map(|s| s.status)
                    )
                };
                let mut status_view = column![
                    text(status).size(24),
                    text(if crate::control_target::service() { "서비스 제어 연결" } else { "기존 Client 설정과 credential을 자동으로 가져오지 않습니다." }),
                    text(format!("{}: {root}", if crate::control_target::service() { "서비스 제어 경로" } else { "새 상태 경로" })),
                    button("로컬 상태 새로 확인").on_press_maybe((!crate::control_target::service()).then_some(Message::RefreshLocal))
                ]
                .spacing(16);
                if crate::control_target::service() {
                    status_view=status_view
                        .push(text("현재 로그인한 사용자의 화면을 연결합니다. 권한 정책과 요청별 승인은 그대로 적용됩니다."))
                        .push(button("현재 사용자 화면 연결").on_press_maybe(
                            (self.can_control() && self.desktop_cancel.is_none() && !self.quitting
                                && !self.desktop_cleanup_failed).then_some(Message::ConnectDesktop)))
                        .push(button("현재 사용자 화면 연결 해제").on_press_maybe(
                            self.desktop_cancel.as_ref().filter(|token|!token.is_cancelled())
                                .map(|_|Message::DisconnectDesktop)));
                }
                if let Some(snapshot) = &self.model.snapshot {
                    status_view = status_view.push(
                        text(if self.model.needs_resync {
                            "마지막으로 확인한 작업 상태 (연결 중단)"
                        } else {
                            "작업 상태"
                        })
                        .size(20),
                    );
                    for activity in snapshot.activity.iter().rev() {
                        let cancellable = self.can_control() && !self.model.needs_resync && !self.quitting
                            && !activity.operation_id.is_empty() && !activity.request_id.is_empty()
                            && !matches!(snapshot.status, racp_contract::local_ipc::AgentStatus::Draining | racp_contract::local_ipc::AgentStatus::Stopped | racp_contract::local_ipc::AgentStatus::Failed)
                            && matches!(activity.state.as_str(), "ACCEPTED" | "DISPATCHED" | "RUNNING" | "RECONCILING");
                        status_view = status_view.push(row![text(format!(
                            "{} · {} · {} · 요청 {}",
                            activity.operation_id,
                            activity.operation,
                            activity.state,
                            activity.request_id
                        )).width(Length::Fill), button("작업 취소").on_press_maybe(cancellable.then(|| Message::CancelOperation(
                            snapshot.boot_id.clone(), activity.operation_id.clone(), activity.request_id.clone(),
                        )))].spacing(12));
                    }
                    status_view = status_view.push(text("취소 접수와 작업 종료는 별개입니다. 작업 상태와 정리 결과를 각각 확인하세요."));
                    status_view = status_view.push(text("최근 제어 결과").size(20));
                    for receipt in snapshot.recent_control_results.iter().rev() {
                        let (id, summary) = completion_summary(receipt);
                        status_view = status_view.push(text(format!("{id}: {summary}")));
                    }
                    status_view = status_view.push(text("최근 최대 16개를 표시합니다. 연결 중단·Agent 재시작·기록 누락은 정리 성공을 뜻하지 않습니다."));
                }
                if crate::control_target::service() {
                    status_view = status_view.push(text(match self.role {
                        Some(ControllerRole::Controller) => "서비스 제어 권한",
                        Some(ControllerRole::Observer) => "서비스 관찰 권한 — 변경 요청 불가",
                        None => "서비스 연결과 권한 확인을 기다립니다.",
                    }));
                    if let Some(identity) = self.model.snapshot.as_ref().and_then(|s| s.identity.as_ref()) {
                        status_view = status_view.push(text(format!("Device: {} · Gateway: {}", identity.device_id, identity.gateway_origin)));
                    }
                    status_view = status_view.push(text("앱을 종료해도 공유 서비스는 계속 실행됩니다. 서비스 Agent 종료는 별도 제어 요청입니다."));
                } else if let Some(state) = self.local.as_ref().and_then(|info| info.state.as_ref()) {
                    status_view = status_view.push(text(format!(
                        "Device: {} · Gateway: {} · Workspace: {} · Profile: {}",
                        state.device_id,
                        state.gateway,
                        state.workspace.display(),
                        state.profile
                    )));
                    status_view = status_view.push(
                        button("Agent 시작").on_press_maybe(
                            (self.model.needs_resync
                                && !self.starting
                                && self.local_cancel.is_none())
                            .then_some(Message::StartAgent),
                        ),
                    );
                } else {
                    status_view=status_view.push(text_input("새 연결 파일 경로",&self.connection_path).on_input(Message::ConnectionPath)).push(button("연결 파일 확인").on_press(Message::InspectConnection)).push(text_input("확인한 연결 파일 SHA-256",&self.connection_hash).on_input(Message::ConnectionHash)).push(text_input("새 작업 폴더의 절대 경로",&self.workspace_draft).on_input(Message::WorkspaceDraft)).push(profile_picklist(&self.profile)).push(text("등록에는 권한 탭의 현재 선택을 사용합니다. 초기 선택은 모두 거부입니다."));
                    if let Some(info) = &self.connection_preview {
                        status_view = status_view.push(text(format!(
                            "Gateway: {} · 만료: {} · CA hash: {}",
                            info["gateway"].as_str().unwrap_or(""),
                            info["expires_at"].as_str().unwrap_or(""),
                            info["ca_sha256"].as_str().unwrap_or("없음")
                        )));
                    }
                    let pending = self
                        .local
                        .as_ref()
                        .is_some_and(|info| info.registration_pending);
                    if pending {
                        status_view=status_view.push(text("이전 등록 결과가 불확실합니다. 기존 pending 상태를 확인하기 전 새 등록을 재시도하지 않습니다."));
                    }
                    status_view =
                        status_view.push(button("확인한 연결 파일로 새 등록").on_press_maybe(
                            (!pending && self.local_cancel.is_none()).then_some(Message::Enroll),
                        ));
                }
                scrollable(status_view).height(Length::Fill).into()
            }
            Page::Runtime => scrollable(
                column![
                    text("선택 런타임 — Agent 종료 상태에서 저장").size(24),
                    text_input("신뢰한 manifest 파일 경로", &self.runtime_manifest)
                        .on_input(Message::RuntimeManifest),
                    text_input("manifest SHA-256", &self.runtime_hash)
                        .on_input(Message::RuntimeHash),
                    text_input(
                        "manifest에 대응하는 로컬 파일 묶음 폴더",
                        &self.runtime_source
                    )
                    .on_input(Message::RuntimeSource),
                    button("manifest와 모든 파일 검증 후 승인").on_press_maybe(
                        self.local_cancel.is_none().then_some(Message::AdmitRuntime)
                    ),
                    text_input(
                        "사용할 Chromium manifest hash — 비우면 기본 기능만",
                        &self.chromium_hash
                    )
                    .on_input(Message::ChromiumHash),
                    text_input("허용할 HTTP(S) origin — 쉼표로 구분", &self.browser_origins)
                        .on_input(Message::BrowserOrigins),
                    checkbox("외부 loopback CDP 연결을 허용", self.external_cdp)
                        .on_toggle(Message::ExternalCdp),
                    text("CDB manifest SHA-256 (선택하지 않으면 비움)"),
                    text_input("CDB SHA-256",&self.cdb_hash).on_input(Message::CdbHash),
                    text("GDB manifest SHA-256 (선택하지 않으면 비움)"),
                    text_input("GDB SHA-256",&self.gdb_hash).on_input(Message::GdbHash),
                    text("Native 분석 host manifest SHA-256 (선택하지 않으면 비움)"),
                    text_input("분석 host SHA-256",&self.analysis_hash).on_input(Message::AnalysisHash),
                    text("분석 host는 고정 native stdio protocol을 사용합니다. Java 실행 파일이나 기존 분석 설정을 가져오지 않습니다."),
                    button("런타임 실행 선택 저장").on_press_maybe(
                        self.local_cancel
                            .is_none()
                            .then_some(Message::ConfigureRuntime)
                    ),
                    text("기존 Chrome·프로필·분석 도구를 자동으로 검색하거나 가져오지 않습니다.")
                ]
                .spacing(16),
            )
            .height(Length::Fill)
            .into(),
            Page::Permissions => {
                let mut list = column![text(
                    "권한 선택 초안 — Agent에 저장되지 않은 로컬 편집입니다."
                ),
                    text("작업 제약").size(20),
                    text("허용 작업 영역 ID (한 줄에 하나, 비워 두면 작업 영역 ID 추가 제한 없음)"),
                    constraint_editor("허용 작업 영역 ID", &self.constraints.workspaces, crate::constraints::Field::Workspaces, 80.0),
                    text("최대 실행 시간 (밀리초)"),
                    text_input("밀리초", &self.constraints.timeout).on_input(|value|
                        Message::EditConstraint(crate::constraints::Field::Timeout, value)),
                    text("최대 출력 크기 (바이트)"),
                    text_input("바이트", &self.constraints.output).on_input(|value|
                        Message::EditConstraint(crate::constraints::Field::Output, value)),
                    text("실행 파일 허용 목록 (한 줄에 절대 경로 하나, 비워 두면 허용 목록 제한 없음)"),
                    constraint_editor("실행 파일 허용 목록", &self.constraints.allow, crate::constraints::Field::AllowExecutables, 100.0),
                    text("실행 파일 차단 목록 (한 줄에 절대 경로 하나)"),
                    constraint_editor("실행 파일 차단 목록", &self.constraints.deny, crate::constraints::Field::DenyExecutables, 100.0),
                    text("작업 제약과 별도로 각 기능의 사용 권한을 선택하세요."),
                    text("실행 파일 목록은 시작 실행 파일을 제한합니다. 셸·터미널에 허용한 명령 전체를 개별 OS 권한으로 분리하거나 격리하지 않습니다."),
                    button("제약 초안을 현재 정책으로 되돌리기").on_press(Message::ReloadConstraints),
                ]
                .spacing(12);
                let mut categories = std::collections::BTreeSet::new();
                for item in &self.model.catalog {
                    if item.implementation != "rpc"
                        || self.model.availability(&item.id) == AvailabilityState::NotImplemented
                    {
                        continue;
                    }
                    if categories.insert(item.category.clone()) {
                        let enabled = !self.model.disabled_categories.contains(&item.category);
                        list = list.push(
                            row![
                                text(&item.category_label).size(20),
                                button(if enabled {
                                    "카테고리 끄기"
                                } else {
                                    "카테고리 켜기"
                                })
                                .accessibility_label(format!("{} · 카테고리 {}", item.category_label, if enabled { "끄기" } else { "켜기" }))
                                .on_press(Message::CategoryChoice(item.category.clone(), !enabled))
                            ]
                            .spacing(12),
                        );
                    }
                    let availability = match self.model.availability(&item.id) {
                        AvailabilityState::NotImplemented
                        | AvailabilityState::Supported
                        | AvailabilityState::Unverified => None,
                        AvailabilityState::NeedsRuntime => Some("추가 도구 설정 필요"),
                        AvailabilityState::NeedsPermission => Some("OS 권한 필요"),
                        AvailabilityState::UnsupportedOs => Some("이 운영체제에서 사용할 수 없음"),
                        AvailabilityState::UnsupportedProtectedTarget => Some("보호된 대상에 사용할 수 없음"),
                    };
                    let mut controls =
                        row![button("거부")
                            .accessibility_label(format!("{} · {} · 거부", item.label, item.id))
                            .on_press(Message::EditGrant(item.id.clone(), Grant::Deny))]
                        .spacing(8);
                    if item.implementation == "rpc" {
                        controls = controls
                            .push(
                                button("허용")
                                    .accessibility_label(format!("{} · {} · 허용", item.label, item.id))
                                    .on_press(Message::EditGrant(item.id.clone(), Grant::Allow)),
                            )
                            .push(button("승인 필요").accessibility_label(format!("{} · {} · 승인 필요", item.label, item.id)).on_press(Message::EditGrant(
                                item.id.clone(),
                                Grant::RequireApproval,
                            )));
                    }
                    list = list.push(
                        column![
                            text(format!(
                                "{} · {}{}",
                                item.category_label, item.label,
                                availability.map(|label| format!(" [{label}]")).unwrap_or_default()
                            )),
                            text(format!("{} · {:?}", item.id, self.model.grants[&item.id]))
                                .size(14),
                            controls
                        ]
                        .spacing(6),
                    );
                }
                scrollable(list).height(Length::Fill).into()
            }
        };
        let mut content = column![header, body].spacing(24);
        if !self.model.needs_resync && self.can_control() {
            content = content.push(
                row![
                    button("권한 저장").on_press(Message::SavePolicy),
                    button("Agent 완전 종료").on_press(Message::Stop)
                ]
                .spacing(12),
            );
        }
        if let Some(error) = &self.error {
            content = content.push(text(error));
        }
        if self.local_cancel.is_some() {
            content = content.push(button("로컬 요청 취소").on_press(Message::CancelLocal));
        }
        if self.quitting && self.local_cancel.is_none() {
            content =
                content.push(button("정리 요청 없이 앱만 종료").on_press(Message::ExitAnyway));
        }
        crate::controls::surface(
            container(content)
                .padding(24)
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
        )
    }
    fn approval_view(&self) -> Element<'_, Message> {
        let mut list = column![text("이번 요청의 대상과 범위를 확인하세요.").size(24)].spacing(16);
        if let Some(snapshot) = &self.model.snapshot {
            for prompt in &snapshot.pending_approvals {
                list = list.push(
                    column![
                        text(format!("{} · {}", prompt.operation, prompt.request_id)),
                        text(&prompt.target_summary),
                        text(format!(
                            "대상 hash: {} · 만료: {}",
                            prompt.target_digest, prompt.expires_at
                        ))
                        .size(12),
                        row![
                            button("이번 요청 허용")
                                .accessibility_label(format!(
                                    "{} · 요청 {} · 이번 요청 허용",
                                    prompt.operation, prompt.request_id
                                ))
                                .on_press_maybe(
                                    (self.can_control() && !self.model.needs_resync)
                                        .then(|| Message::Decide(prompt.nonce.clone(), true))
                                ),
                            button("거부")
                                .accessibility_label(format!(
                                    "{} · 요청 {} · 거부",
                                    prompt.operation, prompt.request_id
                                ))
                                .on_press_maybe(
                                    (self.can_control() && !self.model.needs_resync)
                                        .then(|| Message::Decide(prompt.nonce.clone(), false))
                                )
                        ]
                        .spacing(12)
                    ]
                    .spacing(8),
                );
            }
            if snapshot.pending_approvals.is_empty() {
                list = list.push(text("대기 중인 승인 요청이 없습니다."));
            }
        }
        scrollable(list).height(Length::Fill).into()
    }
}
fn show_window() -> Task<Message> {
    iced::window::get_latest().and_then(|id| {
        iced::window::change_mode(id, iced::window::Mode::Windowed)
            .chain(iced::window::gain_focus(id))
    })
}
fn command_task(command: ControlCommand) -> Task<Message> {
    #[cfg(windows)]
    {
        Task::perform(
            async move {
                let endpoint = tokio::task::spawn_blocking(crate::control_target::endpoint)
                    .await
                    .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?
                    .map_err(|e| e.to_string())?;
                let mut session = racp_runtime::local_ipc::connect(&endpoint)
                    .await
                    .map_err(|e| e.to_string())?;
                session
                    .request(&racp_contract::local_ipc::ControlRequest {
                        protocol: 1,
                        request_id: racp_contract::new_id("ui"),
                        deadline_ms: 5000,
                        command,
                    })
                    .await
                    .map_err(|e| e.to_string())
            },
            Message::Reply,
        )
    }
    #[cfg(not(windows))]
    {
        let _ = command;
        Task::done(Message::Reply(Err("LOCAL_IPC_NOT_IMPLEMENTED".into())))
    }
}

#[cfg(windows)]
fn accessibility_events() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(32, crate::accessibility::events)
}
fn ipc_events() -> impl iced::futures::Stream<Item = Message> {
    use iced::futures::SinkExt;
    use racp_contract::local_ipc::*;
    iced::stream::channel(32, |mut output| async move {
        let mut delay = std::time::Duration::from_millis(500);
        loop {
            let result: Result<(), racp_contract::RacpError> = async {
                // Filesystem/OS identity queries run away from the UI event loop.
                let endpoint = tokio::task::spawn_blocking(crate::control_target::endpoint)
                    .await
                    .map_err(|_| racp_contract::RacpError::new("LOCAL_STATE_FAILED"))??;
                let mut session = racp_runtime::local_ipc::connect(&endpoint).await?;
                let reply = session
                    .request(&ControlRequest {
                        protocol: PROTOCOL,
                        request_id: racp_contract::new_id("ui"),
                        deadline_ms: 5_000,
                        command: ControlCommand::Subscribe {
                            boot_id: "resync".into(),
                            after_sequence: 0,
                        },
                    })
                    .await?;
                let ControlResult::Subscribed { snapshot, role } = reply.result else {
                    return Err(racp_contract::RacpError::new("RUNTIME_RESPONSE_INVALID"));
                };
                if crate::control_target::service()
                    && !snapshot.identity.as_ref().is_some_and(|identity| {
                        matches!(identity.execution_scope, ExecutionScope::Service)
                    })
                {
                    return Err(racp_contract::RacpError::new("SERVICE_SCOPE_INVALID"));
                }
                let mut boot_id = snapshot.boot_id.clone();
                let mut sequence = snapshot.sequence;
                output
                    .send(Message::Snapshot(snapshot, role))
                    .await
                    .map_err(|_| racp_contract::RacpError::new("CANCELLED"))?;
                loop {
                    let event = session.next_event().await?;
                    if event.boot_id != boot_id || sequence.checked_add(1) != Some(event.sequence) {
                        return Err(racp_contract::RacpError::new("RESYNC_REQUIRED"));
                    }
                    boot_id.clone_from(&event.boot_id);
                    sequence = event.sequence;
                    output
                        .send(Message::Event(event))
                        .await
                        .map_err(|_| racp_contract::RacpError::new("CANCELLED"))?;
                }
            }
            .await;
            if result.is_err() && output.send(Message::Disconnected).await.is_err() {
                break;
            }
            // Only disconnected sessions retry. Healthy sessions wait for events without polling.
            tokio::time::sleep(delay).await;
            delay = (delay * 2).min(std::time::Duration::from_secs(10));
        }
    })
}
