use crate::controls::{button, custom_button, text};
use crate::i18n::{tr, TextKey};
use crate::ui_model::UiModel;
use iced::{
    widget::{column, container, row, text_editor},
    Alignment, Element, Length, Padding, Task,
};
use racp_contract::local_ipc::{
    ControlCommand, ControlReply, ControlResult, Grant, PolicyChanges, StateChange,
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
    pub(crate) model: UiModel,
    pub(crate) constraints: crate::constraints::Draft,
    pub(crate) page: Page,
    pub(crate) error: Option<String>,
    pub(crate) workspace_draft: String,
    pub(crate) named_workspaces: Vec<racp_core::WorkspaceSpec>,
    pub(crate) local: Option<crate::onboarding::LocalInfo>,
    pub(crate) connection_path: String,
    pub(crate) connection_hash: String,
    pub(crate) connection_preview: Option<serde_json::Value>,
    pub(crate) profile: String,
    pub(crate) runtime_manifest: String,
    pub(crate) runtime_hash: String,
    pub(crate) runtime_source: String,
    pub(crate) chromium_hash: String,
    pub(crate) cdb_hash: String,
    pub(crate) gdb_hash: String,
    pub(crate) analysis_hash: String,
    pub(crate) browser_origins: String,
    pub(crate) external_cdp: bool,
    pub(crate) local_cancel: Option<tokio_util::sync::CancellationToken>,
    pub(crate) desktop_cancel: Option<tokio_util::sync::CancellationToken>,
    pub(crate) desktop_cleanup_failed: bool,
    pub(crate) receipts: std::collections::BTreeMap<String, String>,
    #[cfg(windows)]
    pub(crate) shell: Option<std::sync::Arc<crate::native::Shell>>,
    pub(crate) can_hide: bool,
    pub(crate) quitting: bool,
    pub(crate) starting: bool,
    pub(crate) auto_start: bool,
    pub(crate) role: Option<ControllerRole>,
    pub(crate) accessibility_attempts: u8,
    pub(crate) window_ready: bool,
    pub lang: crate::i18n::Language,
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
    BatchCategoryGrant(String, Grant),
    SetLanguage(crate::i18n::Language),
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
                lang: crate::i18n::detect_os_language(),
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
    pub(crate) fn can_control(&self) -> bool {
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
                    | Message::BatchCategoryGrant(_, _)
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
            Message::BatchCategoryGrant(category, grant) => {
                let ids: Vec<String> = self
                    .model
                    .catalog
                    .iter()
                    .filter(|item| (category == "*" || item.category == category) && item.implementation == "rpc")
                    .map(|item| item.id.clone())
                    .collect();
                for id in ids {
                    if let Err(e) = self.model.edit_grant(&id, grant) {
                        self.error = Some(e.to_string());
                        break;
                    }
                }
            }
            Message::SetLanguage(lang) => {
                self.lang = lang;
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

        let lang = self.lang;
        let is_status = matches!(self.page, Page::Status);
        let is_permissions = matches!(self.page, Page::Permissions);
        let is_settings = matches!(self.page, Page::Settings);
        let is_runtime = matches!(self.page, Page::Runtime);
        let is_approvals = matches!(self.page, Page::Approvals);
        let is_service = crate::control_target::service();

        let pending_count = self
            .model
            .snapshot
            .as_ref()
            .map_or(0, |s| s.pending_approvals.len());

        // --- 1. TOP ENTERPRISE HEADER BAR ---
        let logo_icon = container(crate::components::icons::render_icon(crate::components::icons::IconType::Shield, 16.0))
            .padding(Padding::from(6))
            .style(|_theme| container::Style {
                background: Some(iced::Background::Color(crate::theme::ACCENT_INDIGO)),
                border: iced::Border {
                    color: iced::Color::TRANSPARENT,
                    width: 0.0,
                    radius: 8.0.into(),
                },
                text_color: Some(crate::theme::TEXT_PRIMARY),
                shadow: iced::Shadow::default(),
            });

        let brand_title = text(self.title()).size(14);
        let brand_badge = container(text(tr(lang, TextKey::AppBadge)).size(10))
            .padding(Padding::from([2, 6]))
            .style(|_theme| container::Style {
                background: Some(iced::Background::Color(iced::Color::from_rgba(0.388, 0.400, 0.945, 0.15))),
                border: iced::Border {
                    color: iced::Color::from_rgba(0.388, 0.400, 0.945, 0.3),
                    width: 1.0,
                    radius: 4.0.into(),
                },
                text_color: Some(crate::theme::ACCENT_INDIGO),
                shadow: iced::Shadow::default(),
            });
        let version_tag = text(format!("v{}", racp_contract::VERSION)).size(11);

        let header_left = row![logo_icon, brand_title, brand_badge, version_tag]
            .spacing(8)
            .align_y(Alignment::Center);

        // Center Status Capsule
        let (status_text_color, status_text_str) = if is_service {
            (crate::theme::ACCENT_INDIGO, "Service Mode")
        } else if self.model.needs_resync {
            (crate::theme::ACCENT_AMBER, tr(lang, TextKey::AgentStopped))
        } else if let Some(snapshot) = &self.model.snapshot {
            match snapshot.status {
                racp_contract::local_ipc::AgentStatus::Online => (crate::theme::ACCENT_EMERALD, tr(lang, TextKey::StatusOnline)),
                _ => (crate::theme::ACCENT_AMBER, tr(lang, TextKey::AgentStopped)),
            }
        } else {
            (crate::theme::ACCENT_AMBER, tr(lang, TextKey::AgentStopped))
        };

        let status_dot = container(text("●").size(10))
            .style(move |_theme| container::Style {
                text_color: Some(status_text_color),
                ..Default::default()
            });

        let role_label = match self.role {
            Some(ControllerRole::Controller) => tr(lang, TextKey::RoleController),
            Some(ControllerRole::Observer) => tr(lang, TextKey::RoleObserver),
            None => "Standby",
        };

        let capsule_content = row![
            status_dot,
            text(status_text_str).size(11),
            text("·").size(11),
            text(format!("Role: {role_label}")).size(11),
        ]
        .spacing(8)
        .align_y(Alignment::Center);

        let header_center = container(capsule_content)
            .padding(Padding::from([4, 12]))
            .style(crate::theme::styles::status_capsule_container);

        // Right controls
        let emergency_stop_btn = custom_button(
            row![
                crate::components::icons::render_icon(crate::components::icons::IconType::Alert, 14.0),
                text(tr(lang, TextKey::BtnEmergencyStop)).size(11),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
            tr(lang, TextKey::BtnEmergencyStop),
        )
        .padding(Padding::from([5, 10]))
        .style(crate::theme::styles::emergency_button)
        .on_press(Message::Stop);

        let quit_btn = custom_button(
            text(tr(lang, TextKey::NavQuit)).size(11),
            tr(lang, TextKey::NavQuit),
        )
        .padding(Padding::from([5, 10]))
        .style(crate::theme::styles::secondary_button)
        .on_press(Message::Quit);

        let header_right = row![emergency_stop_btn, quit_btn]
            .spacing(8)
            .align_y(Alignment::Center);

        let header_bar = container(
            row![
                header_left,
                row![header_center].width(Length::Fill).align_y(Alignment::Center),
                header_right,
            ]
            .spacing(12)
            .align_y(Alignment::Center)
            .width(Length::Fill),
        )
        .style(crate::theme::styles::header_container)
        .padding(Padding::from([8, 16]))
        .width(Length::Fill);

        // --- 2. LEFT SIDEBAR NAVIGATION ---
        // Tab 1: Status
        let tab_status = custom_button(
            row![
                row![
                    crate::components::icons::render_icon(crate::components::icons::IconType::Dashboard, 15.0),
                    text(tr(lang, TextKey::NavStatus)).size(12),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                container(text("●").size(8)).style(move |_| container::Style {
                    text_color: Some(status_text_color),
                    ..Default::default()
                }),
            ]
            .width(Length::Fill)
            .align_y(Alignment::Center),
            tr(lang, TextKey::NavStatus),
        )
        .width(Length::Fill)
        .padding(Padding::from([8, 10]))
        .style(crate::theme::styles::sidebar_nav_button(is_status))
        .on_press(Message::Navigate(Page::Status));

        // Tab 2: Permissions
        let tree_badge = container(text("Tree").size(9))
            .padding(Padding::from([2, 5]))
            .style(|_| container::Style {
                background: Some(iced::Background::Color(iced::Color::from_rgba(0.388, 0.400, 0.945, 0.2))),
                border: iced::Border {
                    color: iced::Color::from_rgba(0.388, 0.400, 0.945, 0.3),
                    width: 1.0,
                    radius: 4.0.into(),
                },
                text_color: Some(crate::theme::ACCENT_INDIGO),
                shadow: iced::Shadow::default(),
            });

        let tab_permissions = custom_button(
            row![
                row![
                    crate::components::icons::render_icon(crate::components::icons::IconType::Shield, 15.0),
                    text(tr(lang, TextKey::NavPermissions)).size(12),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                tree_badge,
            ]
            .width(Length::Fill)
            .align_y(Alignment::Center),
            tr(lang, TextKey::NavPermissions),
        )
        .width(Length::Fill)
        .padding(Padding::from([8, 10]))
        .style(crate::theme::styles::sidebar_nav_button(is_permissions))
        .on_press(Message::Navigate(Page::Permissions));

        // Tab 3: Approvals
        let approvals_badge_elem = if pending_count > 0 {
            container(text(pending_count.to_string()).size(10))
                .padding(Padding::from([1, 6]))
                .style(|_| container::Style {
                    background: Some(iced::Background::Color(crate::theme::ACCENT_ROSE)),
                    border: iced::Border {
                        color: iced::Color::TRANSPARENT,
                        width: 0.0,
                        radius: 10.0.into(),
                    },
                    text_color: Some(crate::theme::TEXT_PRIMARY),
                    shadow: iced::Shadow::default(),
                })
        } else {
            container(text("").size(0))
        };

        let tab_approvals = custom_button(
            row![
                row![
                    crate::components::icons::render_icon(crate::components::icons::IconType::Bell, 15.0),
                    text(tr(lang, TextKey::NavApprovals)).size(12),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                approvals_badge_elem,
            ]
            .width(Length::Fill)
            .align_y(Alignment::Center),
            tr(lang, TextKey::NavApprovals),
        )
        .width(Length::Fill)
        .padding(Padding::from([8, 10]))
        .style(crate::theme::styles::sidebar_nav_button(is_approvals))
        .on_press(Message::Navigate(Page::Approvals));

        // Tab 4: Runtime
        let tab_runtime = custom_button(
            row![
                row![
                    crate::components::icons::render_icon(crate::components::icons::IconType::Package, 15.0),
                    text(tr(lang, TextKey::NavRuntime)).size(12),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                text("3 OK").size(10),
            ]
            .width(Length::Fill)
            .align_y(Alignment::Center),
            tr(lang, TextKey::NavRuntime),
        )
        .width(Length::Fill)
        .padding(Padding::from([8, 10]))
        .style(crate::theme::styles::sidebar_nav_button(is_runtime))
        .on_press_maybe((!is_service).then_some(Message::Navigate(Page::Runtime)));

        // Tab 5: Settings
        let tab_settings = custom_button(
            row![
                crate::components::icons::render_icon(crate::components::icons::IconType::Settings, 15.0),
                text(tr(lang, TextKey::NavSettings)).size(12),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            tr(lang, TextKey::NavSettings),
        )
        .width(Length::Fill)
        .padding(Padding::from([8, 10]))
        .style(crate::theme::styles::sidebar_nav_button(is_settings))
        .on_press_maybe((!is_service).then_some(Message::Navigate(Page::Settings)));

        let mut sidebar_nav = column![tab_status, tab_permissions, tab_approvals].spacing(4);
        if !is_service {
            sidebar_nav = sidebar_nav.push(tab_runtime).push(tab_settings);
        }

        // Sidebar Footer: Host Identity Card
        let device_id_str = if let Some(identity) = self.model.snapshot.as_ref().and_then(|s| s.identity.as_ref()) {
            identity.device_id.chars().take(16).collect::<String>() + "..."
        } else if let Some(state) = self.local.as_ref().and_then(|i| i.state.as_ref()) {
            state.device_id.chars().take(16).collect::<String>() + "..."
        } else {
            "dev_unregistered".to_string()
        };

        let device_card = container(
            column![
                text(tr(lang, TextKey::DeviceIdentity)).size(10),
                text(device_id_str).size(10),
                row![
                    text("DPAPI Vault").size(10),
                    row![
                        crate::components::icons::render_icon(crate::components::icons::IconType::Lock, 10.0),
                        text("Enforced").size(10),
                    ]
                    .spacing(4)
                    .align_y(Alignment::Center),
                ]
                .spacing(6)
                .align_y(Alignment::Center),
            ]
            .spacing(4),
        )
        .style(crate::theme::styles::elevated_box)
        .padding(Padding::from(10))
        .width(Length::Fill);

        let sidebar = container(
            column![
                sidebar_nav,
                column![].height(Length::Fill),
                device_card,
            ]
            .spacing(8)
            .height(Length::Fill),
        )
        .style(crate::theme::styles::sidebar_container)
        .padding(Padding::from(10))
        .width(Length::Fixed(210.0))
        .height(Length::Fill);

        // --- 3. MAIN CONTENT BODY ---
        let body: Element<'_, Message> = match self.page {
            Page::Status => crate::views::status::view(self),
            Page::Permissions => crate::views::permissions::view(self),
            Page::Approvals => crate::views::approvals::view(self),
            Page::Runtime => crate::views::runtime::view(self),
            Page::Settings => crate::views::settings::view(self),
        };

        let mut main_col = column![body].spacing(14).width(Length::Fill);

        // Bottom action toolbar (if active & can control)
        if !self.model.needs_resync && self.can_control() {
            main_col = main_col.push(
                row![
                    button(tr(lang, TextKey::BtnCommitCas)).style(crate::theme::styles::primary_button).on_press(Message::SavePolicy),
                    button("Agent 완전 종료").style(crate::theme::styles::danger_button).on_press(Message::Stop)
                ]
                .spacing(12),
            );
        }

        // Error message banner
        if let Some(error) = &self.error {
            let err_box = container(text(error).size(12))
                .padding(Padding::from([8, 12]))
                .style(|_theme| container::Style {
                    background: Some(iced::Background::Color(iced::Color::from_rgba(0.937, 0.267, 0.267, 0.15))),
                    border: iced::Border {
                        color: iced::Color::from_rgba(0.937, 0.267, 0.267, 0.4),
                        width: 1.0,
                        radius: 6.0.into(),
                    },
                    text_color: Some(crate::theme::ACCENT_ROSE),
                    shadow: iced::Shadow::default(),
                });
            main_col = main_col.push(err_box);
        }

        if self.local_cancel.is_some() {
            main_col = main_col.push(
                button("로컬 요청 취소")
                    .style(crate::theme::styles::secondary_button)
                    .on_press(Message::CancelLocal),
            );
        }

        if self.quitting && self.local_cancel.is_none() {
            main_col = main_col.push(
                button("정리 요청 없이 앱만 종료")
                    .style(crate::theme::styles::danger_button)
                    .on_press(Message::ExitAnyway),
            );
        }

        let main_area = container(main_col)
            .padding(16)
            .width(Length::Fill)
            .height(Length::Fill);

        let layout = column![
            header_bar,
            row![sidebar, main_area].width(Length::Fill).height(Length::Fill),
        ]
        .width(Length::Fill)
        .height(Length::Fill);

        crate::controls::surface(layout.into())
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
