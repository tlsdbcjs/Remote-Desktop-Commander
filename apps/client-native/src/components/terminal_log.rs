//! Terminal-style real-time status and audit log viewer.

use crate::{
    app::Message,
    components::icons::{render_icon, IconType},
    controls::{custom_button, scrollable, text},
    i18n::{tr, Language, TextKey},
    theme::styles::{secondary_button, terminal_container},
};
use iced::{
    widget::{column, container, row},
    Alignment, Element, Length, Padding,
};
use racp_contract::local_ipc::ControlCompletion;

fn completion_summary(receipt: &ControlCompletion) -> (&str, String) {
    match receipt {
        ControlCompletion::Cancellation {
            receipt_id,
            operation_id,
            cancel_requested,
            state,
            terminal,
            cleanup_status,
            error_code,
        } => (
            receipt_id,
            format!(
                "작업 {operation_id} · 취소 신호 {cancel_requested} · 관찰 상태 {state} · 종료 {terminal} · 정리 {cleanup_status}{}",
                error_code.as_ref().map(|code| format!(" · {code}")).unwrap_or_default()
            ),
        ),
        ControlCompletion::Policy {
            receipt_id,
            revision,
            committed,
            retirement_state,
            failed_resource_ids,
        } => (
            receipt_id,
            format!(
                "정책 revision {revision} · 저장 {committed} · 회수 {retirement_state} · 실패 자원 {}",
                failed_resource_ids.join(", ")
            ),
        ),
        ControlCompletion::Stop {
            receipt_id,
            cleanup_state,
            failed_resource_ids,
        } => (
            receipt_id,
            format!(
                "Agent 정리 {cleanup_state} · 실패 자원 {}",
                failed_resource_ids.join(", ")
            ),
        ),
    }
}

/// Renders the terminal log stream and recent activity audit cards.
pub fn terminal_log<'a>(
    recent_results: &'a [ControlCompletion],
    lang: Language,
    _can_control: bool,
) -> Element<'a, Message> {
    // Header Toolbar
    let header_title = row![
        render_icon(IconType::Terminal, 16.0),
        text(tr(lang, TextKey::StatusLogTitle)).size(14),
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    let clear_btn = custom_button(
        row![render_icon(IconType::Trash, 14.0), text(tr(lang, TextKey::BtnClearLog)).size(11)].spacing(4),
        tr(lang, TextKey::BtnClearLog),
    )
    .padding(Padding::from([3, 8]))
    .style(secondary_button);

    let copy_btn = custom_button(
        row![render_icon(IconType::Copy, 14.0), text(tr(lang, TextKey::BtnCopyLog)).size(11)].spacing(4),
        tr(lang, TextKey::BtnCopyLog),
    )
    .padding(Padding::from([3, 8]))
    .style(secondary_button);

    let toolbar = row![
        header_title,
        row![clear_btn, copy_btn].spacing(6).align_y(Alignment::Center),
    ]
    .spacing(12)
    .align_y(Alignment::Center)
    .width(Length::Fill);

    // Recent results entries
    let mut log_lines = column![].spacing(6).width(Length::Fill);

    if recent_results.is_empty() {
        log_lines = log_lines.push(
            text(format!(
                "[{}] {}",
                chrono::Local::now().format("%H:%M:%S"),
                tr(lang, TextKey::RecentActivityEmpty)
            ))
            .size(11),
        );
    } else {
        for receipt in recent_results.iter().rev() {
            let (id, summary) = completion_summary(receipt);
            let entry_row = row![
                text(format!("[{}]", chrono::Local::now().format("%H:%M:%S"))).size(11),
                text(id).size(11),
                text(summary).size(11).width(Length::Fill),
            ]
            .spacing(8)
            .align_y(Alignment::Center);

            log_lines = log_lines.push(entry_row);
        }
    }

    let scrollable_log = scrollable(log_lines)
        .height(Length::Fixed(160.0))
        .width(Length::Fill);

    let term_box = container(scrollable_log)
        .style(terminal_container)
        .padding(Padding::from(10))
        .width(Length::Fill);

    column![toolbar, term_box].spacing(8).width(Length::Fill).into()
}
