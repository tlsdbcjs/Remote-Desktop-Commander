//! Modern Dark Slate theme system for RACP enterprise native client.

pub mod styles;

use iced::Color;

// Enterprise Dark Palette (Slate-950 base)
pub const BG_APP: Color = Color::from_rgb(0.043, 0.059, 0.098); // #0b0f19
pub const BG_SIDEBAR: Color = Color::from_rgb(0.059, 0.090, 0.149); // #0f1726
pub const BG_CARD: Color = Color::from_rgb(0.067, 0.094, 0.153); // #111827
pub const BG_CARD_HOVER: Color = Color::from_rgb(0.106, 0.145, 0.224); // #1b2539
pub const BG_ELEVATED: Color = Color::from_rgb(0.020, 0.035, 0.063); // #050910
pub const BG_TERMINAL: Color = Color::from_rgb(0.012, 0.016, 0.027); // #030407

// Border Colors
pub const BORDER_SUBTLE: Color = Color::from_rgb(0.118, 0.161, 0.231); // #1e293b
pub const BORDER_MUTED: Color = Color::from_rgb(0.078, 0.114, 0.176); // #141d2d
pub const BORDER_ACTIVE: Color = Color::from_rgb(0.200, 0.255, 0.333); // #334155
pub const BORDER_FOCUS: Color = Color::from_rgb(0.388, 0.400, 0.945); // #6366f1

// Text Colors
pub const TEXT_PRIMARY: Color = Color::from_rgb(0.973, 0.980, 0.988); // #f8fafc
pub const TEXT_SECONDARY: Color = Color::from_rgb(0.804, 0.835, 0.878); // #cbd5e1
pub const TEXT_MUTED: Color = Color::from_rgb(0.580, 0.639, 0.722); // #94a3b8
pub const TEXT_DIM: Color = Color::from_rgb(0.392, 0.455, 0.545); // #64748b

// Semantic Accent Colors
pub const ACCENT_INDIGO: Color = Color::from_rgb(0.388, 0.400, 0.945); // #6366f1
pub const ACCENT_INDIGO_HOVER: Color = Color::from_rgb(0.310, 0.322, 0.886); // #4f52e2
pub const ACCENT_ROSE: Color = Color::from_rgb(0.882, 0.114, 0.282); // #e11d48
pub const ACCENT_ROSE_BG: Color = Color::from_rgba(0.882, 0.114, 0.282, 0.15);
pub const ACCENT_AMBER: Color = Color::from_rgb(0.851, 0.467, 0.024); // #d97706
pub const ACCENT_AMBER_BG: Color = Color::from_rgba(0.851, 0.467, 0.024, 0.15);
pub const ACCENT_EMERALD: Color = Color::from_rgb(0.020, 0.588, 0.412); // #059669
pub const ACCENT_EMERALD_BG: Color = Color::from_rgba(0.020, 0.588, 0.412, 0.15);
pub const ACCENT_BLUE: Color = Color::from_rgb(0.235, 0.510, 0.965); // #3b82f6
