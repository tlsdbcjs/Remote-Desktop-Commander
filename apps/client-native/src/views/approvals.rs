//! Zero-Trust Approvals Queue View.

use crate::{
    app::{App, Message},
    components::status_badge::{badge, BadgeStyle},
    controls::{button, scrollable, text},
    i18n::{tr, TextKey},
    theme::styles::{card_container, danger_button, elevated_box, success_button},
};
use iced::{
    widget::{column, container, row},
    Alignment, Element, Length, Padding,
};

pub fn view<'a>(app: &'a App) -> Element<'a, Message> {
    let lang = app.lang;
    let can_control = app.can_control() && !app.model.needs_resync;

    // Header
    let title_sec = column![
        text(tr(lang, TextKey::ApprovalsTitle)).size(20),
        text(tr(lang, TextKey::ApprovalsDesc)).size(12),
    ]
    .spacing(4)
    .width(Length::Fill);

    let header_card = container(title_sec)
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill);

    let mut list = column![header_card].spacing(12).width(Length::Fill);

    let pending = app
        .model
        .snapshot
        .as_ref()
        .map(|s| s.pending_approvals.as_slice())
        .unwrap_or_default();

    if pending.is_empty() {
        let empty_box = container(
            text(tr(lang, TextKey::NoPendingApprovals)).size(13),
        )
        .style(elevated_box)
        .padding(Padding::from(24))
        .width(Length::Fill);

        list = list.push(empty_box);
    } else {
        for prompt in pending {
            let is_high_risk = prompt.operation.contains("write")
                || prompt.operation.contains("terminal")
                || prompt.operation.contains("exec");

            let risk_badge = if is_high_risk {
                badge(tr(lang, TextKey::RiskHigh), BadgeStyle::Danger)
            } else {
                badge(tr(lang, TextKey::RiskMedium), BadgeStyle::Warning)
            };

            let card_header = row![
                row![
                    risk_badge,
                    text(format!("{} · {}", prompt.operation, prompt.request_id)).size(14),
                ]
                .spacing(10)
                .align_y(Alignment::Center)
                .width(Length::Fill),
                badge(format!("{} {}", tr(lang, TextKey::TtlRemaining), prompt.expires_at), BadgeStyle::Neutral),
            ]
            .spacing(12)
            .align_y(Alignment::Center)
            .width(Length::Fill);

            let target_box = container(
                column![
                    text(format!("{}: {}", tr(lang, TextKey::TargetSummary), prompt.target_summary)).size(12),
                    text(format!("{}: {}", tr(lang, TextKey::TargetDigest), prompt.target_digest)).size(11),
                ]
                .spacing(4)
                .width(Length::Fill),
            )
            .style(elevated_box)
            .padding(Padding::from(10))
            .width(Length::Fill);

            let approve_btn = button(tr(lang, TextKey::BtnApproveOnce))
                .style(success_button)
                .accessibility_label(format!("{} 요청 승인", prompt.operation))
                .on_press_maybe(can_control.then(|| Message::Decide(prompt.nonce.clone(), true)));

            let deny_btn = button(tr(lang, TextKey::BtnDenyRequest))
                .style(danger_button)
                .accessibility_label(format!("{} 요청 거절", prompt.operation))
                .on_press_maybe(can_control.then(|| Message::Decide(prompt.nonce.clone(), false)));

            let action_row = row![deny_btn, approve_btn]
                .spacing(10)
                .align_y(Alignment::Center);

            let card_content = column![
                card_header,
                target_box,
                row![container(iced::widget::Space::with_width(Length::Fill)), action_row]
                    .width(Length::Fill)
                    .align_y(Alignment::Center),
            ]
            .spacing(12)
            .width(Length::Fill);

            let prompt_card = container(card_content)
                .style(card_container)
                .padding(Padding::from(16))
                .width(Length::Fill);

            list = list.push(prompt_card);
        }
    }

    scrollable(list)
        .height(Length::Fill)
        .width(Length::Fill)
        .into()
}
