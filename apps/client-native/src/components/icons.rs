//! Centralized SVG vector icon rendering with static cached handles.

use iced::{
    widget::svg,
    Length,
};
use std::sync::LazyLock;

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
    Dashboard,
    Bell,
    Package,
    Settings,
    Shield,
    Alert,
    Lock,
}

static HANDLE_DENY: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/deny.svg"))
});
static HANDLE_PROMPT: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/prompt.svg"))
});
static HANDLE_ALLOW: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/allow.svg"))
});
static HANDLE_MONITOR: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/monitor.svg"))
});
static HANDLE_FOLDER: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/folder.svg"))
});
static HANDLE_FILE_EDIT: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/file_edit.svg"))
});
static HANDLE_TERMINAL: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/terminal.svg"))
});
static HANDLE_SEARCH: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/search.svg"))
});
static HANDLE_REFRESH: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/refresh.svg"))
});
static HANDLE_COPY: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/copy.svg"))
});
static HANDLE_TRASH: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/trash.svg"))
});
static HANDLE_DASHBOARD: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/dashboard.svg"))
});
static HANDLE_BELL: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/bell.svg"))
});
static HANDLE_PACKAGE: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/package.svg"))
});
static HANDLE_SETTINGS: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/settings.svg"))
});
static HANDLE_SHIELD: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/shield.svg"))
});
static HANDLE_ALERT: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/alert.svg"))
});
static HANDLE_LOCK: LazyLock<svg::Handle> = LazyLock::new(|| {
    svg::Handle::from_memory(include_bytes!("../../assets/icons/lock.svg"))
});

/// Renders a statically cached SVG vector icon at the requested size (width = height = size).
pub fn render_icon(icon: IconType, size: f32) -> svg::Svg<'static> {
    let handle = match icon {
        IconType::Deny => (*HANDLE_DENY).clone(),
        IconType::Prompt => (*HANDLE_PROMPT).clone(),
        IconType::Allow => (*HANDLE_ALLOW).clone(),
        IconType::Monitor => (*HANDLE_MONITOR).clone(),
        IconType::Folder => (*HANDLE_FOLDER).clone(),
        IconType::FileEdit => (*HANDLE_FILE_EDIT).clone(),
        IconType::Terminal => (*HANDLE_TERMINAL).clone(),
        IconType::Search => (*HANDLE_SEARCH).clone(),
        IconType::Refresh => (*HANDLE_REFRESH).clone(),
        IconType::Copy => (*HANDLE_COPY).clone(),
        IconType::Trash => (*HANDLE_TRASH).clone(),
        IconType::Dashboard => (*HANDLE_DASHBOARD).clone(),
        IconType::Bell => (*HANDLE_BELL).clone(),
        IconType::Package => (*HANDLE_PACKAGE).clone(),
        IconType::Settings => (*HANDLE_SETTINGS).clone(),
        IconType::Shield => (*HANDLE_SHIELD).clone(),
        IconType::Alert => (*HANDLE_ALERT).clone(),
        IconType::Lock => (*HANDLE_LOCK).clone(),
    };
    svg(handle)
        .width(Length::Fixed(size))
        .height(Length::Fixed(size))
}
