//! Centralized SVG vector icon rendering with compile-time embedded assets.

use iced::{
    widget::svg,
    Length,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IconType {
    Deny,
    Prompt,
    Allow,
    Monitor,
    Folder,
    FileEdit,
    Terminal,
    Search,
    Refresh,
    Copy,
    Trash,
}

/// Renders a compile-time embedded SVG vector icon at the requested size (width = height = size).
pub fn render_icon(icon: IconType, size: f32) -> svg::Svg<'static> {
    let bytes: &'static [u8] = match icon {
        IconType::Deny => include_bytes!("../../assets/icons/deny.svg"),
        IconType::Prompt => include_bytes!("../../assets/icons/prompt.svg"),
        IconType::Allow => include_bytes!("../../assets/icons/allow.svg"),
        IconType::Monitor => include_bytes!("../../assets/icons/monitor.svg"),
        IconType::Folder => include_bytes!("../../assets/icons/folder.svg"),
        IconType::FileEdit => include_bytes!("../../assets/icons/file_edit.svg"),
        IconType::Terminal => include_bytes!("../../assets/icons/terminal.svg"),
        IconType::Search => include_bytes!("../../assets/icons/search.svg"),
        IconType::Refresh => include_bytes!("../../assets/icons/refresh.svg"),
        IconType::Copy => include_bytes!("../../assets/icons/copy.svg"),
        IconType::Trash => include_bytes!("../../assets/icons/trash.svg"),
    };
    svg(svg::Handle::from_memory(bytes))
        .width(Length::Fixed(size))
        .height(Length::Fixed(size))
}
