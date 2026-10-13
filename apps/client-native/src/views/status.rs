//! Status, Endpoint Onboarding, and Real-Time Terminal Log View.

use crate::{
    app::{App, Message},
    components::{
        icons::{render_icon, IconType},
        status_badge::{badge, BadgeStyle},
        terminal_log::terminal_log,
    },
    controls::{button, custom_button, profile_picklist, scrollable, text, text_input},
    i18n::{tr, TextKey},
    theme::styles::{card_container, danger_button, elevated_box, primary_button, secondary_button, success_button},
};
use iced::{
    widget::{column, container, row},
    Alignment, Element, Length, Padding,
};
use racp_contract::{
    local_ipc::AgentStatus,
    service_scope::ControllerRole,
};

pub fn view<'a>(app: &'a App) -> Element<'a, Message> {
    let lang = app.lang;
    let can_control = app.can_control() && !app.model.needs_resync;
    let is_service = crate::control_target::service();

    let root = crate::control_target::root()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|e| e.to_string());

    let mut content = column![].spacing(16).width(Length::Fill);

    // 1. Agent Status & Endpoint Identity Card
    let (status_badge, status_summary) = if is_service {
        let role_str = match app.role {
            Some(ControllerRole::Controller) => "서비스 제어 권한 (Controller)",
            Some(ControllerRole::Observer) => "서비스 관찰 권한 (Observer — 변경 불가)",
            None => "서비스 연결 대기 (Connecting...)",
        };
        (badge("Service Mode", BadgeStyle::Info), role_str.to_string())
    } else if app.model.needs_resync {
        (
            badge(tr(lang, TextKey::AgentStopped), BadgeStyle::Warning),
            tr(lang, TextKey::AgentPending).to_string(),
        )
    } else if let Some(snapshot) = &app.model.snapshot {
        match snapshot.status {
            AgentStatus::Online => (badge(tr(lang, TextKey::AgentRunning), BadgeStyle::Success), format!("Agent 상태: {:?}", snapshot.status)),
            AgentStatus::Connecting => (badge("Connecting", BadgeStyle::Info), format!("Agent 상태: {:?}", snapshot.status)),
            AgentStatus::Offline => (badge("Offline", BadgeStyle::Neutral), format!("Agent 상태: {:?}", snapshot.status)),
            AgentStatus::Unregistered => (badge("Unregistered", BadgeStyle::Neutral), format!("Agent 상태: {:?}", snapshot.status)),
            AgentStatus::Draining => (badge("Draining", BadgeStyle::Warning), format!("Agent 상태: {:?}", snapshot.status)),
            AgentStatus::Stopped => (badge("Stopped", BadgeStyle::Neutral), format!("Agent 상태: {:?}", snapshot.status)),
            AgentStatus::Failed => (badge("Failed", BadgeStyle::Danger), format!("Agent 상태: {:?}", snapshot.status)),
        }
    } else {
        (
            badge(tr(lang, TextKey::AgentStopped), BadgeStyle::Neutral),
            "Agent 연결 대기 — 새 등록 또는 Agent 시작이 필요합니다.".to_string(),
        )
    };

    let title_row = row![
        text("엔드포인트 제어 상태").size(20),
        status_badge,
    ]
    .spacing(10)
    .align_y(Alignment::Center);

    let mut header_card_col = column![
        title_row,
        text(status_summary).size(13),
        row![
            text(format!("{}: {root}", if is_service { "서비스 제어 경로" } else { "상태 저장 경로" })).size(12),
        ].spacing(8).align_y(Alignment::Center),
    ]
    .spacing(10)
    .width(Length::Fill);

    // Refresh local button & quick actions
    let mut actions_row = row![].spacing(10).align_y(Alignment::Center);

    if !is_service {
        actions_row = actions_row.push(
            custom_button(
                row![render_icon(IconType::Refresh, 14.0), text(tr(lang, TextKey::BtnRefreshLocal)).size(12)].spacing(6),
                tr(lang, TextKey::BtnRefreshLocal),
            )
            .padding(Padding::from([6, 12]))
            .style(secondary_button)
            .on_press(Message::RefreshLocal)
        );
    }

    if is_service {
        let can_connect_desktop = app.can_control()
            && app.desktop_cancel.is_none()
            && !app.quitting
            && !app.desktop_cleanup_failed;

        let is_desktop_connected = app.desktop_cancel.as_ref().filter(|t| !t.is_cancelled()).is_some();

        actions_row = actions_row
            .push(
                custom_button(
                    row![render_icon(IconType::Monitor, 14.0), text(tr(lang, TextKey::BtnConnectDesktop)).size(12)].spacing(6),
                    tr(lang, TextKey::BtnConnectDesktop),
                )
                .padding(Padding::from([6, 12]))
                .style(primary_button)
                .on_press_maybe(can_connect_desktop.then_some(Message::ConnectDesktop))
            )
            .push(
                button("현재 화면 연결 해제")
                    .style(danger_button)
                    .on_press_maybe(is_desktop_connected.then_some(Message::DisconnectDesktop))
            );
    }

    header_card_col = header_card_col.push(actions_row);

    // Identity Metadata in Header
    if is_service {
        if let Some(identity) = app.model.snapshot.as_ref().and_then(|s| s.identity.as_ref()) {
            let id_box = container(
                row![
                    text(format!("Device: {}", identity.device_id)).size(11),
                    text("·").size(11),
                    text(format!("Gateway: {}", identity.gateway_origin)).size(11),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
            )
            .style(elevated_box)
            .padding(Padding::from([6, 10]));
            header_card_col = header_card_col.push(id_box);
        }
        header_card_col = header_card_col.push(
            text("현재 로그인한 사용자의 화면을 연결합니다. 권한 정책과 요청별 승인은 그대로 적용되며, 앱을 종료해도 공유 서비스는 계속 실행됩니다.").size(11)
        );
    } else if let Some(state) = app.local.as_ref().and_then(|info| info.state.as_ref()) {
        let id_box = container(
            row![
                text(format!("Device: {}", state.device_id)).size(11),
                text("·").size(11),
                text(format!("Gateway: {}", state.gateway)).size(11),
                text("·").size(11),
                text(format!("Workspace: {}", state.workspace.display())).size(11),
                text("·").size(11),
                text(format!("Profile: {}", state.profile)).size(11),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        )
        .style(elevated_box)
        .padding(Padding::from([6, 10]));
        header_card_col = header_card_col.push(id_box);

        let can_start_agent = app.model.needs_resync && !app.starting && app.local_cancel.is_none();
        header_card_col = header_card_col.push(
            button(tr(lang, TextKey::BtnStartAgent))
                .style(primary_button)
                .on_press_maybe(can_start_agent.then_some(Message::StartAgent))
        );
    }

    let header_card = container(header_card_col)
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill);

    content = content.push(header_card);

    // 4 Core Metric Cards Grid (From Demo)
    if let Some(snapshot) = &app.model.snapshot {
        let active_tasks_count = snapshot.activity.iter().filter(|a| matches!(a.state.as_str(), "RUNNING" | "DISPATCHED" | "ACCEPTED")).count();
        let pending_count = snapshot.pending_approvals.len();

        let card_pid = container(
            column![
                row![
                    text("Agent 프로세스").size(11),
                    container(text(if snapshot.status == AgentStatus::Online { "정상 가동" } else { "대기" }).size(9))
                        .padding(Padding::from([1, 5]))
                        .style(|_| container::Style {
                            background: Some(iced::Background::Color(iced::Color::from_rgba(0.020, 0.588, 0.412, 0.15))),
                            border: iced::Border {
                                color: iced::Color::from_rgba(0.020, 0.588, 0.412, 0.3),
                                width: 1.0,
                                radius: 4.0.into(),
                            },
                            text_color: Some(crate::theme::ACCENT_EMERALD),
                            shadow: iced::Shadow::default(),
                        })
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                text(format!("Seq #{}", snapshot.sequence)).size(16),
                text("Pipe IPC Connected").size(10),
            ]
            .spacing(4)
        )
        .style(card_container)
        .padding(Padding::from(12))
        .width(Length::FillPortion(1));

        let card_tasks = container(
            column![
                row![
                    text("활성 제어 작업").size(11),
                    container(text(format!("{active_tasks_count} ACTIVE")).size(9))
                        .padding(Padding::from([1, 5]))
                        .style(|_| container::Style {
                            background: Some(iced::Background::Color(iced::Color::from_rgba(0.388, 0.400, 0.945, 0.15))),
                            border: iced::Border {
                                color: iced::Color::from_rgba(0.388, 0.400, 0.945, 0.3),
                                width: 1.0,
                                radius: 4.0.into(),
                            },
                            text_color: Some(crate::theme::ACCENT_INDIGO),
                            shadow: iced::Shadow::default(),
                        })
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                text(format!("{active_tasks_count} / 32 Slots")).size(16),
                text("안전 취소 핸들러 무장됨").size(10),
            ]
            .spacing(4)
        )
        .style(card_container)
        .padding(Padding::from(12))
        .width(Length::FillPortion(1));

        let card_approvals = container(
            column![
                row![
                    text("대기 중 승인 요청").size(11),
                    container(text(format!("{pending_count} PENDING")).size(9))
                        .padding(Padding::from([1, 5]))
                        .style(|_| container::Style {
                            background: Some(iced::Background::Color(iced::Color::from_rgba(0.882, 0.114, 0.282, 0.15))),
                            border: iced::Border {
                                color: iced::Color::from_rgba(0.882, 0.114, 0.282, 0.3),
                                width: 1.0,
                                radius: 4.0.into(),
                            },
                            text_color: Some(crate::theme::ACCENT_ROSE),
                            shadow: iced::Shadow::default(),
                        })
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                text(format!("{pending_count} 건")).size(16),
                text("Zero-Trust Prompt").size(10),
            ]
            .spacing(4)
        )
        .style(card_container)
        .padding(Padding::from(12))
        .width(Length::FillPortion(1));

        let card_guardian = container(
            column![
                row![
                    text("화면/입력 가디언").size(11),
                    container(text("Standby").size(9))
                        .padding(Padding::from([1, 5]))
                        .style(|_| container::Style {
                            background: Some(iced::Background::Color(iced::Color::from_rgba(0.851, 0.467, 0.024, 0.15))),
                            border: iced::Border {
                                color: iced::Color::from_rgba(0.851, 0.467, 0.024, 0.3),
                                width: 1.0,
                                radius: 4.0.into(),
                            },
                            text_color: Some(crate::theme::ACCENT_AMBER),
                            shadow: iced::Shadow::default(),
                        })
                ]
                .spacing(8)
                .align_y(Alignment::Center),
                text("Guardian Ready").size(16),
                text("물리 입력 감지 시 원격 차단").size(10),
            ]
            .spacing(4)
        )
        .style(card_container)
        .padding(Padding::from(12))
        .width(Length::FillPortion(1));

        let metrics_row = row![card_pid, card_tasks, card_approvals, card_guardian]
            .spacing(12)
            .width(Length::Fill);

        content = content.push(metrics_row);
    }

    // 2. Onboarding / Enrollment Section (Shown when local state is absent and not service)
    let needs_enrollment = !is_service && app.local.as_ref().and_then(|info| info.state.as_ref()).is_none();
    if needs_enrollment {
        let is_pending = app.local.as_ref().is_some_and(|info| info.registration_pending);

        let mut enroll_form = column![
            row![
                render_icon(IconType::Folder, 18.0),
                text(tr(lang, TextKey::EnrollFormTitle)).size(16),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
            text(tr(lang, TextKey::EnrollFormDesc)).size(12),
            text("기존 Client 설정과 credential을 자동으로 가져오지 않습니다. 배포된 연결 파일(.racp)을 검증한 후 안전하게 엔드포인트를 등록하세요.").size(11),
            
            // Connection File Path + Inspect Button
            text(tr(lang, TextKey::LblConnPath)).size(12),
            row![
                text_input(tr(lang, TextKey::LblConnPath), &app.connection_path)
                    .on_input(Message::ConnectionPath),
                custom_button(
                    row![render_icon(IconType::Search, 14.0), text(tr(lang, TextKey::BtnInspectConn)).size(12)].spacing(6),
                    tr(lang, TextKey::BtnInspectConn),
                )
                .padding(Padding::from([6, 14]))
                .style(primary_button)
                .on_press(Message::InspectConnection),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        ]
        .spacing(10)
        .width(Length::Fill);

        // Preview Box if connection inspected
        if let Some(info) = &app.connection_preview {
            let preview_col = column![
                row![
                    render_icon(IconType::Prompt, 14.0),
                    text(tr(lang, TextKey::ConnPreviewTitle)).size(13),
                ]
                .spacing(6)
                .align_y(Alignment::Center),
                row![
                    text(format!("{} {}", tr(lang, TextKey::ConnGateway), info["gateway"].as_str().unwrap_or(""))).size(12),
                    text("·").size(12),
                    text(format!("{} {}", tr(lang, TextKey::ConnExpires), info["expires_at"].as_str().unwrap_or(""))).size(12),
                ]
                .spacing(8),
                text(format!("{} {}", tr(lang, TextKey::ConnCaHash), info["ca_sha256"].as_str().unwrap_or("없음"))).size(11),
            ]
            .spacing(6);

            let preview_card = container(preview_col)
                .style(elevated_box)
                .padding(Padding::from(12))
                .width(Length::Fill);

            enroll_form = enroll_form.push(preview_card);
        }

        // Connection Hash Input & Workspace Draft
        enroll_form = enroll_form
            .push(text(tr(lang, TextKey::LblConnHash)).size(12))
            .push(
                text_input(tr(lang, TextKey::LblConnHash), &app.connection_hash)
                    .on_input(Message::ConnectionHash)
            )
            .push(text(tr(lang, TextKey::LblDefaultWorkspace)).size(12))
            .push(
                text_input(tr(lang, TextKey::LblDefaultWorkspace), &app.workspace_draft)
                    .on_input(Message::WorkspaceDraft)
            )
            .push(text(tr(lang, TextKey::LblExecProfile)).size(12))
            .push(profile_picklist(&app.profile))
            .push(text("등록에는 권한 탭의 현재 선택을 사용합니다. 초기 선택은 모두 거부입니다.").size(11));

        if is_pending {
            let pending_alert = container(
                text("이전 등록 결과가 불확실합니다. 기존 pending 상태를 확인하기 전 새 등록을 재시도하지 않습니다.").size(12)
            )
            .style(elevated_box)
            .padding(Padding::from(10));
            enroll_form = enroll_form.push(pending_alert);
        }

        let can_enroll = !is_pending && app.local_cancel.is_none();
        enroll_form = enroll_form.push(
            button(tr(lang, TextKey::BtnEnrollSubmit))
                .style(success_button)
                .on_press_maybe(can_enroll.then_some(Message::Enroll))
        );

        let enroll_card = container(enroll_form)
            .style(card_container)
            .padding(Padding::from(16))
            .width(Length::Fill);

        content = content.push(enroll_card);
    }

    // 3. Active Operations & Activity List Card
    if let Some(snapshot) = &app.model.snapshot {
        if !snapshot.activity.is_empty() {
            let mut activity_col = column![
                text(if app.model.needs_resync {
                    "마지막으로 확인한 작업 상태 (연결 중단)"
                } else {
                    "실행 중인 작업 상태"
                })
                .size(16),
            ]
            .spacing(8)
            .width(Length::Fill);

            for activity in snapshot.activity.iter().rev() {
                let cancellable = can_control
                    && !app.quitting
                    && !activity.operation_id.is_empty()
                    && !activity.request_id.is_empty()
                    && !matches!(snapshot.status, AgentStatus::Draining | AgentStatus::Stopped | AgentStatus::Failed)
                    && matches!(activity.state.as_str(), "ACCEPTED" | "DISPATCHED" | "RUNNING" | "RECONCILING");

                let item_row = row![
                    column![
                        text(format!("{} · {}", activity.operation_id, activity.operation)).size(13),
                        text(format!("상태: {} · 요청: {}", activity.state, activity.request_id)).size(11),
                    ]
                    .spacing(2)
                    .width(Length::Fill),
                    button("작업 취소")
                        .style(danger_button)
                        .on_press_maybe(cancellable.then(|| Message::CancelOperation(
                            snapshot.boot_id.clone(),
                            activity.operation_id.clone(),
                            activity.request_id.clone(),
                        ))),
                ]
                .spacing(12)
                .align_y(Alignment::Center);

                let item_box = container(item_row)
                    .style(elevated_box)
                    .padding(Padding::from(10))
                    .width(Length::Fill);

                activity_col = activity_col.push(item_box);
            }

            activity_col = activity_col.push(
                text("취소 접수와 작업 종료는 별개입니다. 작업 상태와 정리 결과를 각각 확인하세요.").size(11)
            );

            let activity_card = container(activity_col)
                .style(card_container)
                .padding(Padding::from(16))
                .width(Length::Fill);

            content = content.push(activity_card);
        }
    }

    // 4. Terminal Log & Control Completions Section
    let recent_results = app
        .model
        .snapshot
        .as_ref()
        .map(|s| s.recent_control_results.as_slice())
        .unwrap_or_default();

    let terminal_view = terminal_log(recent_results, lang, can_control);
    content = content.push(terminal_view);

    content = content.push(
        text("최근 최대 16개를 표시합니다. 연결 중단·Agent 재시작·기록 누락은 정리 성공을 뜻하지 않습니다.").size(11)
    );

    scrollable(content).height(Length::Fill).into()
}
