//! Settings, Language Pack, Workspaces and Login Startup View.

use crate::{
    app::{App, Message},
    components::icons::{render_icon, IconType},
    controls::{button, custom_button, scrollable, text, text_input},
    i18n::{tr, Language, TextKey},
    theme::{
        self,
        styles::{card_container, elevated_box, primary_button, secondary_button},
    },
};
use iced::{
    widget::{column, container, row},
    Alignment, Element, Length, Padding,
};

pub fn view<'a>(app: &'a App) -> Element<'a, Message> {
    let lang = app.lang;

    if crate::control_target::service() {
        return container(text(tr(lang, TextKey::ServiceSettingNotice)).size(14))
            .style(card_container)
            .padding(Padding::from(24))
            .width(Length::Fill)
            .into();
    }

    // Header
    let title_sec = column![
        text(tr(lang, TextKey::SettingsTitle)).size(20),
        text(tr(lang, TextKey::SettingsDesc)).size(12),
    ]
    .spacing(4)
    .width(Length::Fill);

    let header_card = container(title_sec)
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill);

    // Section 1: Independent Language Pack Selection (In Settings!)
    let ko_active = lang == Language::Ko;
    let en_active = lang == Language::En;

    let btn_ko = custom_button(
        text("한국어 (Korean, ko-KR)").size(12),
        "한국어 (ko-KR)",
    )
    .padding(Padding::from([6, 14]))
    .style(theme::styles::nav_tab_button(ko_active))
    .on_press(Message::SetLanguage(Language::Ko));

    let btn_en = custom_button(
        text("English (en-US)").size(12),
        "English (en-US)",
    )
    .padding(Padding::from([6, 14]))
    .style(theme::styles::nav_tab_button(en_active))
    .on_press(Message::SetLanguage(Language::En));

    let lang_card = container(
        column![
            text(tr(lang, TextKey::LangSettingTitle)).size(16),
            text(tr(lang, TextKey::LangSettingDesc)).size(12),
            row![
                text(tr(lang, TextKey::InterfaceLanguage)).size(12),
                row![btn_ko, btn_en].spacing(8),
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

    // Section 2: Named Workspaces Isolation
    let mut ws_column = column![
        text(tr(lang, TextKey::NamedWorkspacesTitle)).size(16),
        text(tr(lang, TextKey::NamedWorkspacesDesc)).size(12),
        text(tr(lang, TextKey::LblDefaultWorkspace)).size(12),
        text_input(tr(lang, TextKey::LblDefaultWorkspace), &app.workspace_draft)
            .on_input(Message::WorkspaceDraft),
    ]
    .spacing(8)
    .width(Length::Fill);

    if app.named_workspaces.is_empty() {
        ws_column = ws_column.push(
            container(text(tr(lang, TextKey::NoNamedWorkspaces)).size(12))
                .style(elevated_box)
                .padding(Padding::from(10))
                .width(Length::Fill),
        );
    } else {
        for (index, spec) in app.named_workspaces.iter().enumerate() {
            let path = spec.path.to_string_lossy().into_owned();
            let row_item = row![
                text_input("식별자", &spec.id)
                    .on_input(move |v| Message::WorkspaceId(index, v)),
                text_input("절대 경로", &path)
                    .on_input(move |v| Message::WorkspacePath(index, v)),
                custom_button(
                    render_icon(IconType::Trash, 14.0),
                    format!("작업 폴더 {} 삭제", spec.id),
                )
                .padding(Padding::from([4, 8]))
                .style(theme::styles::danger_button)
                .on_press(Message::RemoveWorkspace(index)),
            ]
            .spacing(8)
            .align_y(Alignment::Center);

            ws_column = ws_column.push(row_item);
        }
    }

    let add_ws_btn = button(tr(lang, TextKey::BtnAddWorkspace))
        .style(secondary_button)
        .on_press_maybe((app.named_workspaces.len() < 15).then_some(Message::AddWorkspace));

    let save_settings_btn = button(tr(lang, TextKey::BtnSaveSettings))
        .style(primary_button)
        .on_press(Message::SaveSettings);

    let reload_settings_btn = button(tr(lang, TextKey::BtnReloadSettings))
        .style(secondary_button)
        .on_press(Message::RefreshLocal);

    ws_column = ws_column.push(
        row![add_ws_btn, save_settings_btn, reload_settings_btn]
            .spacing(10)
            .align_y(Alignment::Center),
    );

    let ws_card = container(ws_column)
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill);

    // Section 3: Windows Startup Configuration
    let mut login_column = column![
        text(tr(lang, TextKey::WindowsStartupTitle)).size(16),
        text(tr(lang, TextKey::WindowsStartupDesc)).size(12),
    ]
    .spacing(8)
    .width(Length::Fill);

    if let Some(local) = &app.local {
        if let Some(login) = &local.login {
            let status_text = format!(
                "등록값 존재: {} · 소유 확인: {} · 미확정 변경: {}",
                login["enabled"], login["owned"], login["pending"]
            );
            login_column = login_column.push(text(status_text).size(12));

            if login["pending"] == true {
                login_column = login_column.push(
                    button(tr(lang, TextKey::BtnReconcileStartup))
                        .style(secondary_button)
                        .on_press(Message::ReconcileLogin),
                );
            } else if login["owned"] == true {
                let is_enabled = login["enabled"] == true;
                let toggle_label = if is_enabled {
                    tr(lang, TextKey::BtnDisableStartup)
                } else {
                    tr(lang, TextKey::BtnEnableStartup)
                };

                let mut startup_actions = row![
                    button(toggle_label)
                        .style(if is_enabled { secondary_button } else { primary_button })
                        .on_press(Message::ConfigureLogin(!is_enabled)),
                ]
                .spacing(8);

                if is_enabled {
                    startup_actions = startup_actions.push(
                        button(tr(lang, TextKey::BtnUpdateStartup))
                            .style(secondary_button)
                            .on_press(Message::ConfigureLogin(true)),
                    );
                }

                login_column = login_column.push(startup_actions);
            }
        }
    }

    let login_card = container(login_column)
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill);

    let content = column![header_card, lang_card, ws_card, login_card]
        .spacing(16)
        .width(Length::Fill);

    scrollable(content)
        .height(Length::Fill)
        .width(Length::Fill)
        .into()
}
