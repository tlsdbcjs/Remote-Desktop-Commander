//! Permissions, Constraints, and Category Tree View.

use crate::{
    app::{App, Message},
    components::{
        category_accordion::category_node,
        icons::{render_icon, IconType},
    },
    controls::{button, constraint_editor, custom_button, scrollable, text},
    i18n::{tr, TextKey},
    theme::{
        self,
        styles::{card_container, elevated_box, primary_button, secondary_button},
    },
};
use iced::{
    widget::{column, container, row},
    Alignment, Element, Length, Padding,
};
use racp_contract::local_ipc::{AvailabilityState, Grant};
use std::collections::BTreeMap;

pub fn view<'a>(app: &'a App) -> Element<'a, Message> {
    let lang = app.lang;
    let can_control = app.can_control() && !app.model.needs_resync;

    // Header with Title and CAS Commit Button
    let title_sec = column![
        row![
            text(tr(lang, TextKey::PermissionsTitle)).size(20),
            container(text("Zero-Trust Matrix").size(10))
                .padding(Padding::from([2, 6]))
                .style(|_theme| container::Style {
                    background: Some(iced::Background::Color(iced::Color::from_rgba(0.388, 0.400, 0.945, 0.15))),
                    border: iced::Border {
                        color: iced::Color::from_rgba(0.388, 0.400, 0.945, 0.3),
                        width: 1.0,
                        radius: 4.0.into(),
                    },
                    text_color: Some(theme::ACCENT_INDIGO),
                    shadow: iced::Shadow::default(),
                }),
        ]
        .spacing(8)
        .align_y(Alignment::Center),
        text(tr(lang, TextKey::PermissionsSubtitle)).size(12),
    ]
    .spacing(4)
    .width(Length::Fill);

    let commit_btn = button(tr(lang, TextKey::BtnCommitCas))
        .style(primary_button)
        .on_press_maybe(can_control.then_some(Message::SavePolicy));

    let header_card = container(
        row![title_sec, commit_btn]
            .spacing(16)
            .align_y(Alignment::Center)
            .width(Length::Fill),
    )
    .style(card_container)
    .padding(Padding::from(16))
    .width(Length::Fill);

    // Global Fast Presets Toolbar
    let preset_label = row![
        render_icon(IconType::Search, 14.0),
        text(tr(lang, TextKey::GlobalPresetsLabel)).size(12),
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    let all_deny_btn = custom_button(
        row![render_icon(IconType::Deny, 14.0), text(tr(lang, TextKey::PresetAllDeny)).size(11)].spacing(4),
        tr(lang, TextKey::PresetAllDeny),
    )
    .padding(Padding::from([4, 8]))
    .style(secondary_button)
    .on_press_maybe(can_control.then(|| Message::BatchCategoryGrant("*".into(), Grant::Deny)));

    let all_prompt_btn = custom_button(
        row![render_icon(IconType::Prompt, 14.0), text(tr(lang, TextKey::PresetAllPrompt)).size(11)].spacing(4),
        tr(lang, TextKey::PresetAllPrompt),
    )
    .padding(Padding::from([4, 8]))
    .style(secondary_button)
    .on_press_maybe(can_control.then(|| Message::BatchCategoryGrant("*".into(), Grant::RequireApproval)));

    let all_allow_btn = custom_button(
        row![render_icon(IconType::Allow, 14.0), text(tr(lang, TextKey::PresetAllAllow)).size(11)].spacing(4),
        tr(lang, TextKey::PresetAllAllow),
    )
    .padding(Padding::from([4, 8]))
    .style(secondary_button)
    .on_press_maybe(can_control.then(|| Message::BatchCategoryGrant("*".into(), Grant::Allow)));

    let preset_toolbar = container(
        row![
            preset_label,
            row![all_deny_btn, all_prompt_btn, all_allow_btn].spacing(8),
        ]
        .spacing(16)
        .align_y(Alignment::Center)
        .width(Length::Fill),
    )
    .style(elevated_box)
    .padding(Padding::from([10, 14]))
    .width(Length::Fill);

    // Execution Constraints Editor
    let constraints_card = container(
        column![
            text(tr(lang, TextKey::ExecutionConstraintsTitle)).size(16),
            text(tr(lang, TextKey::AllowExecutablesLabel)).size(12),
            constraint_editor(
                "실행 파일 허용 목록",
                &app.constraints.allow,
                crate::constraints::Field::AllowExecutables,
                90.0
            ),
            text(tr(lang, TextKey::DenyExecutablesLabel)).size(12),
            constraint_editor(
                "실행 파일 차단 목록",
                &app.constraints.deny,
                crate::constraints::Field::DenyExecutables,
                90.0
            ),
            row![
                text(tr(lang, TextKey::ConstraintsHint)).size(11).width(Length::Fill),
                button(tr(lang, TextKey::BtnReloadConstraints))
                    .style(secondary_button)
                    .on_press(Message::ReloadConstraints),
            ]
            .spacing(12)
            .align_y(Alignment::Center),
        ]
        .spacing(10)
        .width(Length::Fill),
    )
    .style(card_container)
    .padding(Padding::from(16))
    .width(Length::Fill);

    // Group Catalog Items by Category
    let mut category_map: BTreeMap<&str, (&str, Vec<&'a crate::ui_model::CatalogItem>)> = BTreeMap::new();

    for item in &app.model.catalog {
        if item.implementation != "rpc"
            || app.model.availability(&item.id) == AvailabilityState::NotImplemented
        {
            continue;
        }
        let entry = category_map
            .entry(item.category.as_str())
            .or_insert_with(|| (item.category_label.as_str(), Vec::new()));
        entry.1.push(item);
    }

    let mut category_nodes = column![].spacing(12).width(Length::Fill);

    for (cat_id, (cat_label, items)) in &category_map {
        let enabled = !app.model.disabled_categories.contains(*cat_id);
        category_nodes = category_nodes.push(category_node(
            cat_id,
            cat_label,
            enabled,
            items,
            &app.model.grants,
            lang,
            can_control,
        ));
    }

    let content = column![
        header_card,
        preset_toolbar,
        constraints_card,
        category_nodes,
    ]
    .spacing(16)
    .width(Length::Fill);

    scrollable(content)
        .height(Length::Fill)
        .width(Length::Fill)
        .into()
}
