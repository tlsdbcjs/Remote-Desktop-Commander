//! Category Accordion Tree component with batch grant controls and leaf rows.

use crate::{
    app::Message,
    components::{
        grant_selector::grant_selector,
        icons::{render_icon, IconType},
    },
    controls::{button, custom_button, text},
    i18n::{tr, Language, TextKey},
    theme::{
        self,
        styles::{card_container, secondary_button},
    },
    ui_model::CatalogItem,
};
use iced::{
    widget::{column, container, row},
    Alignment, Element, Length, Padding,
};
use racp_contract::local_ipc::Grant;

/// Resolves the appropriate vector icon for a given category identifier.
pub fn category_icon(category: &str) -> IconType {
    match category {
        "system" => IconType::Monitor,
        "files_read" => IconType::Folder,
        "files_write" => IconType::FileEdit,
        "terminal" => IconType::Terminal,
        "analysis" => IconType::Search,
        _ => IconType::Monitor,
    }
}

/// Resolves the localized title for a category.
pub fn category_title<'a>(category: &str, default_label: &'a str, lang: Language) -> std::borrow::Cow<'a, str> {
    match category {
        "system" => std::borrow::Cow::Borrowed(tr(lang, TextKey::CatSystemTitle)),
        "files_read" => std::borrow::Cow::Borrowed(tr(lang, TextKey::CatFilesReadTitle)),
        "files_write" => std::borrow::Cow::Borrowed(tr(lang, TextKey::CatFilesWriteTitle)),
        "terminal" => std::borrow::Cow::Borrowed(tr(lang, TextKey::CatTerminalTitle)),
        "analysis" => std::borrow::Cow::Borrowed(tr(lang, TextKey::CatAnalysisTitle)),
        "browser" => std::borrow::Cow::Borrowed(tr(lang, TextKey::CatBrowserTitle)),
        _ => std::borrow::Cow::Borrowed(default_label),
    }
}

/// Resolves the localized description for a category.
pub fn category_description(category: &str, lang: Language) -> &'static str {
    match category {
        "system" => tr(lang, TextKey::CatSystemDesc),
        "files_read" => tr(lang, TextKey::CatFilesReadDesc),
        "files_write" => tr(lang, TextKey::CatFilesWriteDesc),
        "terminal" => tr(lang, TextKey::CatTerminalDesc),
        "analysis" => tr(lang, TextKey::CatAnalysisDesc),
        "browser" => tr(lang, TextKey::CatBrowserDesc),
        _ => "",
    }
}

/// Renders a category node with header, batch controls, and leaf permission items.
pub fn category_node<'a>(
    category: &str,
    category_label: &str,
    enabled: bool,
    items: &[&'a CatalogItem],
    grants: &std::collections::BTreeMap<String, Grant>,
    lang: Language,
    can_control: bool,
) -> Element<'a, Message> {
    let icon_type = category_icon(category);
    let title_text = category_title(category, category_label, lang);
    let desc_text = category_description(category, lang);

    // Category Header Left: Icon + Title + Description
    let icon_elem = container(render_icon(icon_type, 20.0))
        .padding(Padding::from(6))
        .style(|_theme| container::Style {
            background: Some(iced::Background::Color(iced::Color::from_rgba(0.388, 0.400, 0.945, 0.12))),
            border: iced::Border {
                color: iced::Color::from_rgba(0.388, 0.400, 0.945, 0.25),
                width: 1.0,
                radius: 8.0.into(),
            },
            text_color: Some(theme::ACCENT_INDIGO),
            shadow: iced::Shadow::default(),
        });

    let title_column = column![
        row![
            text(title_text).size(16),
            text(format!("({})", category)).size(12),
        ]
        .spacing(8)
        .align_y(Alignment::Center),
        text(desc_text).size(11),
    ]
    .spacing(3);

    let header_left = row![icon_elem, title_column]
        .spacing(12)
        .align_y(Alignment::Center);

    // Category Header Right: Enable/Disable toggle + Batch buttons
    let toggle_btn = button(if enabled { "카테고리 끄기" } else { "카테고리 켜기" })
        .style(secondary_button)
        .on_press_maybe(can_control.then(|| Message::CategoryChoice(category.to_string(), !enabled)));

    let batch_deny_btn = custom_button(
        row![render_icon(IconType::Deny, 14.0), text(tr(lang, TextKey::BtnBatchDeny)).size(11)].spacing(4),
        format!("{} - {}", category, tr(lang, TextKey::BtnBatchDeny)),
    )
    .padding(Padding::from([4, 8]))
    .style(secondary_button)
    .on_press_maybe(can_control.then(|| Message::BatchCategoryGrant(category.to_string(), Grant::Deny)));

    let batch_prompt_btn = custom_button(
        row![render_icon(IconType::Prompt, 14.0), text(tr(lang, TextKey::BtnBatchPrompt)).size(11)].spacing(4),
        format!("{} - {}", category, tr(lang, TextKey::BtnBatchPrompt)),
    )
    .padding(Padding::from([4, 8]))
    .style(secondary_button)
    .on_press_maybe(can_control.then(|| Message::BatchCategoryGrant(category.to_string(), Grant::RequireApproval)));

    let batch_allow_btn = custom_button(
        row![render_icon(IconType::Allow, 14.0), text(tr(lang, TextKey::BtnBatchAllow)).size(11)].spacing(4),
        format!("{} - {}", category, tr(lang, TextKey::BtnBatchAllow)),
    )
    .padding(Padding::from([4, 8]))
    .style(secondary_button)
    .on_press_maybe(can_control.then(|| Message::BatchCategoryGrant(category.to_string(), Grant::Allow)));

    let header_right = row![
        text(tr(lang, TextKey::CategoryBatchLabel)).size(11),
        batch_deny_btn,
        batch_prompt_btn,
        batch_allow_btn,
        toggle_btn,
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    let header_row = row![header_left, header_right]
        .spacing(12)
        .align_y(Alignment::Center)
        .width(Length::Fill);

    // Leaf rows
    let mut leaves_col = column![].spacing(6).width(Length::Fill);
    for item in items {
        let current_grant = grants.get(&item.id).copied();
        let leaf_row = row![
            column![
                text(&item.label).size(13),
                text(&item.id).size(11),
            ]
            .spacing(2)
            .width(Length::Fill),
            grant_selector(&item.id, current_grant, lang, can_control && enabled),
        ]
        .spacing(12)
        .align_y(Alignment::Center)
        .width(Length::Fill);

        leaves_col = leaves_col.push(
            container(leaf_row)
                .padding(Padding::from([6, 12]))
                .style(|_theme| container::Style {
                    background: Some(iced::Background::Color(iced::Color::from_rgba(0.0, 0.0, 0.0, 0.15))),
                    border: iced::Border {
                        color: theme::BORDER_MUTED,
                        width: 1.0,
                        radius: 6.0.into(),
                    },
                    text_color: Some(theme::TEXT_PRIMARY),
                    shadow: iced::Shadow::default(),
                }),
        );
    }

    container(column![header_row, leaves_col].spacing(14).width(Length::Fill))
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill)
        .into()
}
