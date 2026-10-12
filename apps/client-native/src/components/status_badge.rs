//! Status and Risk Badges for consistent enterprise security tags.

use crate::{app::Message, controls::text, theme};
use iced::{
    widget::container,
    Color, Element, Padding,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BadgeStyle {
    Success,
    Warning,
    Danger,
    Info,
    Neutral,
}

pub fn badge<'a>(label: impl ToString, style: BadgeStyle) -> Element<'a, Message> {
    let (text_color, bg_color, border_color) = match style {
        BadgeStyle::Success => (
            theme::ACCENT_EMERALD,
            Color::from_rgba(theme::ACCENT_EMERALD.r, theme::ACCENT_EMERALD.g, theme::ACCENT_EMERALD.b, 0.12),
            Color::from_rgba(theme::ACCENT_EMERALD.r, theme::ACCENT_EMERALD.g, theme::ACCENT_EMERALD.b, 0.3),
        ),
        BadgeStyle::Warning => (
            theme::ACCENT_AMBER,
            Color::from_rgba(theme::ACCENT_AMBER.r, theme::ACCENT_AMBER.g, theme::ACCENT_AMBER.b, 0.12),
            Color::from_rgba(theme::ACCENT_AMBER.r, theme::ACCENT_AMBER.g, theme::ACCENT_AMBER.b, 0.3),
        ),
        BadgeStyle::Danger => (
            theme::ACCENT_ROSE,
            Color::from_rgba(theme::ACCENT_ROSE.r, theme::ACCENT_ROSE.g, theme::ACCENT_ROSE.b, 0.12),
            Color::from_rgba(theme::ACCENT_ROSE.r, theme::ACCENT_ROSE.g, theme::ACCENT_ROSE.b, 0.3),
        ),
        BadgeStyle::Info => (
            theme::ACCENT_INDIGO,
            Color::from_rgba(theme::ACCENT_INDIGO.r, theme::ACCENT_INDIGO.g, theme::ACCENT_INDIGO.b, 0.12),
            Color::from_rgba(theme::ACCENT_INDIGO.r, theme::ACCENT_INDIGO.g, theme::ACCENT_INDIGO.b, 0.3),
        ),
        BadgeStyle::Neutral => (
            theme::TEXT_MUTED,
            Color::from_rgba(1.0, 1.0, 1.0, 0.05),
            theme::BORDER_SUBTLE,
        ),
    };

    container(text(label.to_string()).size(10))
        .padding(Padding::from([2, 6]))
        .style(move |_theme| container::Style {
            background: Some(iced::Background::Color(bg_color)),
            border: iced::Border {
                color: border_color,
                width: 1.0,
                radius: 4.0.into(),
            },
            text_color: Some(text_color),
            shadow: iced::Shadow::default(),
        })
        .into()
}
