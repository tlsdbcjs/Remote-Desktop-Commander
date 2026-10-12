//! Style definitions for containers, buttons, and custom widgets.

use super::*;
use iced::{
    widget::{button, container},
    Background, Border, Color, Shadow, Vector,
};
use racp_contract::local_ipc::Grant;

/// Base container for cards / sections
pub fn card_container(_theme: &iced::Theme) -> container::Style {
    container::Style {
        background: Some(Background::Color(BG_CARD)),
        border: Border {
            color: BORDER_SUBTLE,
            width: 1.0,
            radius: 12.0.into(),
        },
        text_color: Some(TEXT_PRIMARY),
        shadow: Shadow {
            color: Color::from_rgba(0.0, 0.0, 0.0, 0.2),
            offset: Vector::new(0.0, 2.0),
            blur_radius: 6.0,
        },
    }
}

/// Border-accented container for warnings / alerts
pub fn alert_container(color: Color) -> impl Fn(&iced::Theme) -> container::Style {
    move |_theme| container::Style {
        background: Some(Background::Color(BG_CARD)),
        border: Border {
            color: Color::from_rgba(color.r, color.g, color.b, 0.5),
            width: 1.0,
            radius: 12.0.into(),
        },
        text_color: Some(TEXT_PRIMARY),
        shadow: Shadow {
            color: Color::from_rgba(color.r, color.g, color.b, 0.1),
            offset: Vector::new(0.0, 2.0),
            blur_radius: 8.0,
        },
    }
}

/// Elevated box (used for preview panels, toolbars)
pub fn elevated_box(_theme: &iced::Theme) -> container::Style {
    container::Style {
        background: Some(Background::Color(BG_ELEVATED)),
        border: Border {
            color: BORDER_SUBTLE,
            width: 1.0,
            radius: 8.0.into(),
        },
        text_color: Some(TEXT_SECONDARY),
        shadow: Shadow::default(),
    }
}

/// Container for terminal-style logs
pub fn terminal_container(_theme: &iced::Theme) -> container::Style {
    container::Style {
        background: Some(Background::Color(BG_TERMINAL)),
        border: Border {
            color: BORDER_SUBTLE,
            width: 1.0,
            radius: 8.0.into(),
        },
        text_color: Some(TEXT_SECONDARY),
        shadow: Shadow::default(),
    }
}

/// Container for 3-way segment control
pub fn segment_group_container(_theme: &iced::Theme) -> container::Style {
    container::Style {
        background: Some(Background::Color(BG_ELEVATED)),
        border: Border {
            color: BORDER_MUTED,
            width: 1.0,
            radius: 8.0.into(),
        },
        text_color: None,
        shadow: Shadow::default(),
    }
}

/// Primary action button (e.g., Save, Commit, Start Agent)
pub fn primary_button(_theme: &iced::Theme, status: button::Status) -> button::Style {
    let base = button::Style {
        background: Some(Background::Color(ACCENT_INDIGO)),
        text_color: TEXT_PRIMARY,
        border: Border {
            color: Color::TRANSPARENT,
            width: 0.0,
            radius: 8.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(ACCENT_INDIGO.r, ACCENT_INDIGO.g, ACCENT_INDIGO.b, 0.3),
            offset: Vector::new(0.0, 2.0),
            blur_radius: 4.0,
        },
    };
    match status {
        button::Status::Active => base,
        button::Status::Hovered => button::Style {
            background: Some(Background::Color(ACCENT_INDIGO_HOVER)),
            shadow: Shadow {
                color: Color::from_rgba(ACCENT_INDIGO.r, ACCENT_INDIGO.g, ACCENT_INDIGO.b, 0.5),
                offset: Vector::new(0.0, 2.0),
                blur_radius: 8.0,
            },
            ..base
        },
        button::Status::Pressed => button::Style {
            background: Some(Background::Color(ACCENT_INDIGO_HOVER)),
            shadow: Shadow::default(),
            ..base
        },
        button::Status::Disabled => button::Style {
            background: Some(Background::Color(Color::from_rgba(0.2, 0.25, 0.35, 0.3))),
            text_color: TEXT_DIM,
            shadow: Shadow::default(),
            ..base
        },
    }
}

/// Secondary action button (e.g., Reload, Cancel, Refresh)
pub fn secondary_button(_theme: &iced::Theme, status: button::Status) -> button::Style {
    let base = button::Style {
        background: Some(Background::Color(BG_CARD)),
        text_color: TEXT_SECONDARY,
        border: Border {
            color: BORDER_SUBTLE,
            width: 1.0,
            radius: 8.0.into(),
        },
        shadow: Shadow::default(),
    };
    match status {
        button::Status::Active => base,
        button::Status::Hovered => button::Style {
            background: Some(Background::Color(BG_CARD_HOVER)),
            text_color: TEXT_PRIMARY,
            border: Border {
                color: BORDER_ACTIVE,
                width: 1.0,
                radius: 8.0.into(),
            },
            ..base
        },
        button::Status::Pressed => button::Style {
            background: Some(Background::Color(BG_ELEVATED)),
            ..base
        },
        button::Status::Disabled => button::Style {
            text_color: TEXT_DIM,
            ..base
        },
    }
}

/// Danger action button (e.g., Stop, Emergency Stop)
pub fn danger_button(_theme: &iced::Theme, status: button::Status) -> button::Style {
    let base = button::Style {
        background: Some(Background::Color(ACCENT_ROSE)),
        text_color: TEXT_PRIMARY,
        border: Border {
            color: Color::TRANSPARENT,
            width: 0.0,
            radius: 8.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(ACCENT_ROSE.r, ACCENT_ROSE.g, ACCENT_ROSE.b, 0.3),
            offset: Vector::new(0.0, 2.0),
            blur_radius: 4.0,
        },
    };
    match status {
        button::Status::Active => base,
        button::Status::Hovered => button::Style {
            background: Some(Background::Color(Color::from_rgb(0.95, 0.2, 0.35))),
            ..base
        },
        button::Status::Pressed => button::Style {
            background: Some(Background::Color(Color::from_rgb(0.8, 0.1, 0.25))),
            ..base
        },
        button::Status::Disabled => button::Style {
            background: Some(Background::Color(Color::from_rgba(0.2, 0.25, 0.35, 0.3))),
            text_color: TEXT_DIM,
            ..base
        },
    }
}

/// Success action button (e.g., Approve)
pub fn success_button(_theme: &iced::Theme, status: button::Status) -> button::Style {
    let base = button::Style {
        background: Some(Background::Color(ACCENT_EMERALD)),
        text_color: TEXT_PRIMARY,
        border: Border {
            color: Color::TRANSPARENT,
            width: 0.0,
            radius: 8.0.into(),
        },
        shadow: Shadow {
            color: Color::from_rgba(ACCENT_EMERALD.r, ACCENT_EMERALD.g, ACCENT_EMERALD.b, 0.3),
            offset: Vector::new(0.0, 2.0),
            blur_radius: 4.0,
        },
    };
    match status {
        button::Status::Active => base,
        button::Status::Hovered => button::Style {
            background: Some(Background::Color(Color::from_rgb(0.05, 0.7, 0.5))),
            ..base
        },
        button::Status::Pressed => button::Style {
            background: Some(Background::Color(Color::from_rgb(0.02, 0.5, 0.35))),
            ..base
        },
        button::Status::Disabled => button::Style {
            background: Some(Background::Color(Color::from_rgba(0.2, 0.25, 0.35, 0.3))),
            text_color: TEXT_DIM,
            ..base
        },
    }
}

/// Navigation tab button
pub fn nav_tab_button(is_active: bool) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    move |_theme, status| {
        if is_active {
            button::Style {
                background: Some(Background::Color(Color::from_rgba(ACCENT_INDIGO.r, ACCENT_INDIGO.g, ACCENT_INDIGO.b, 0.15))),
                text_color: ACCENT_INDIGO,
                border: Border {
                    color: Color::from_rgba(ACCENT_INDIGO.r, ACCENT_INDIGO.g, ACCENT_INDIGO.b, 0.3),
                    width: 1.0,
                    radius: 8.0.into(),
                },
                shadow: Shadow::default(),
            }
        } else {
            let base = button::Style {
                background: Some(Background::Color(Color::TRANSPARENT)),
                text_color: TEXT_MUTED,
                border: Border::default(),
                shadow: Shadow::default(),
            };
            match status {
                button::Status::Hovered => button::Style {
                    background: Some(Background::Color(Color::from_rgba(1.0, 1.0, 1.0, 0.05))),
                    text_color: TEXT_PRIMARY,
                    border: Border {
                        color: BORDER_SUBTLE,
                        width: 1.0,
                        radius: 8.0.into(),
                    },
                    ..base
                },
                button::Status::Pressed => button::Style {
                    background: Some(Background::Color(Color::from_rgba(1.0, 1.0, 1.0, 0.08))),
                    ..base
                },
                _ => base,
            }
        }
    }
}

/// 3-Way SVG grant segment button style
pub fn grant_segment_button(
    current: Option<Grant>,
    target: Grant,
) -> impl Fn(&iced::Theme, button::Status) -> button::Style {
    let is_selected = current == Some(target);
    move |_theme, status| {
        if is_selected {
            let (bg, shadow_color) = match target {
                Grant::Deny => (ACCENT_ROSE, ACCENT_ROSE),
                Grant::RequireApproval => (ACCENT_AMBER, ACCENT_AMBER),
                Grant::Allow => (ACCENT_EMERALD, ACCENT_EMERALD),
            };
            button::Style {
                background: Some(Background::Color(bg)),
                text_color: TEXT_PRIMARY,
                border: Border {
                    color: Color::TRANSPARENT,
                    width: 0.0,
                    radius: 6.0.into(),
                },
                shadow: Shadow {
                    color: Color::from_rgba(shadow_color.r, shadow_color.g, shadow_color.b, 0.4),
                    offset: Vector::new(0.0, 1.0),
                    blur_radius: 4.0,
                },
            }
        } else {
            let hover_color = match target {
                Grant::Deny => Color::from_rgba(ACCENT_ROSE.r, ACCENT_ROSE.g, ACCENT_ROSE.b, 0.2),
                Grant::RequireApproval => Color::from_rgba(ACCENT_AMBER.r, ACCENT_AMBER.g, ACCENT_AMBER.b, 0.2),
                Grant::Allow => Color::from_rgba(ACCENT_EMERALD.r, ACCENT_EMERALD.g, ACCENT_EMERALD.b, 0.2),
            };
            let base = button::Style {
                background: Some(Background::Color(Color::TRANSPARENT)),
                text_color: TEXT_DIM,
                border: Border::default(),
                shadow: Shadow::default(),
            };
            match status {
                button::Status::Hovered => button::Style {
                    background: Some(Background::Color(hover_color)),
                    text_color: TEXT_SECONDARY,
                    border: Border {
                        color: Color::TRANSPARENT,
                        width: 0.0,
                        radius: 6.0.into(),
                    },
                    ..base
                },
                button::Status::Pressed => button::Style {
                    background: Some(Background::Color(hover_color)),
                    ..base
                },
                _ => base,
            }
        }
    }
}
