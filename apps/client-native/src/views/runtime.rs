//! Isolated Runtimes and Engine Hub View.

use crate::{
    app::{App, Message},
    controls::{button, checkbox, scrollable, text, text_input},
    i18n::{tr, TextKey},
    theme::styles::{card_container, elevated_box, primary_button, secondary_button},
};
use iced::{
    widget::{column, container, row},
    Element, Length, Padding,
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
        text(tr(lang, TextKey::RuntimeTitle)).size(20),
        text(tr(lang, TextKey::RuntimeDesc)).size(12),
    ]
    .spacing(4)
    .width(Length::Fill);

    let header_card = container(title_sec)
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill);

    // Form inputs card
    let form_content = column![
        text(tr(lang, TextKey::RuntimeManifestPath)).size(12),
        text_input(tr(lang, TextKey::RuntimeManifestPath), &app.runtime_manifest)
            .on_input(Message::RuntimeManifest),
        text(tr(lang, TextKey::RuntimeHash)).size(12),
        text_input(tr(lang, TextKey::RuntimeHash), &app.runtime_hash)
            .on_input(Message::RuntimeHash),
        text(tr(lang, TextKey::RuntimeSource)).size(12),
        text_input(tr(lang, TextKey::RuntimeSource), &app.runtime_source)
            .on_input(Message::RuntimeSource),
        text(tr(lang, TextKey::ChromiumHash)).size(12),
        text_input(tr(lang, TextKey::ChromiumHash), &app.chromium_hash)
            .on_input(Message::ChromiumHash),
        text(tr(lang, TextKey::CdbHash)).size(12),
        text_input(tr(lang, TextKey::CdbHash), &app.cdb_hash)
            .on_input(Message::CdbHash),
        text(tr(lang, TextKey::GdbHash)).size(12),
        text_input(tr(lang, TextKey::GdbHash), &app.gdb_hash)
            .on_input(Message::GdbHash),
        text(tr(lang, TextKey::AnalysisHash)).size(12),
        text_input(tr(lang, TextKey::AnalysisHash), &app.analysis_hash)
            .on_input(Message::AnalysisHash),
        text(tr(lang, TextKey::BrowserOrigins)).size(12),
        text_input(tr(lang, TextKey::BrowserOrigins), &app.browser_origins)
            .on_input(Message::BrowserOrigins),
        checkbox(tr(lang, TextKey::ExternalCdp), app.external_cdp)
            .on_toggle(Message::ExternalCdp),
        row![
            button(tr(lang, TextKey::BtnAdmitRuntime))
                .style(primary_button)
                .on_press(Message::AdmitRuntime),
            button(tr(lang, TextKey::BtnConfigureRuntime))
                .style(secondary_button)
                .on_press(Message::ConfigureRuntime),
        ]
        .spacing(12),
    ]
    .spacing(8)
    .width(Length::Fill);

    let form_card = container(form_content)
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill);

    // Admitted runtimes summary card
    let admitted_content = column![
        text(tr(lang, TextKey::AdmittedRuntimesTitle)).size(16),
        container(
            text(tr(lang, TextKey::NoAdmittedRuntimes)).size(12),
        )
        .style(elevated_box)
        .padding(Padding::from(16))
        .width(Length::Fill),
    ]
    .spacing(10)
    .width(Length::Fill);

    let admitted_card = container(admitted_content)
        .style(card_container)
        .padding(Padding::from(16))
        .width(Length::Fill);

    let content = column![header_card, form_card, admitted_card]
        .spacing(16)
        .width(Length::Fill);

    scrollable(content)
        .height(Length::Fill)
        .width(Length::Fill)
        .into()
}
