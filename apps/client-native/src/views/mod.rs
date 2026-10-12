//! Independent tab view modules for client application.

pub mod approvals;
pub mod permissions;
pub mod runtime;
pub mod settings;
pub mod status;

#[cfg(test)]
mod tests {
    use crate::app::{App, Page};
    use crate::components::icons::{render_icon, IconType};
    use crate::components::status_badge::{badge, BadgeStyle};
    use crate::i18n::Language;
    use racp_contract::local_ipc::{AgentStatus, ApprovalPrompt, ControlCompletion, StateSnapshot};

    #[test]
    fn test_all_icon_renders() {
        let icons = [
            IconType::Deny,
            IconType::Prompt,
            IconType::Allow,
            IconType::Monitor,
            IconType::Folder,
            IconType::FileEdit,
            IconType::Terminal,
            IconType::Search,
            IconType::Refresh,
            IconType::Copy,
            IconType::Trash,
        ];
        for icon in icons {
            let _elem = render_icon(icon, 16.0);
        }
    }

    #[test]
    fn test_all_badge_styles() {
        let styles = [
            BadgeStyle::Success,
            BadgeStyle::Warning,
            BadgeStyle::Danger,
            BadgeStyle::Info,
            BadgeStyle::Neutral,
        ];
        for style in styles {
            let _elem = badge("TEST_BADGE", style);
        }
    }

    #[test]
    fn test_views_render_without_panic() {
        let (mut app, _task) = App::new(false);

        for lang in [Language::Ko, Language::En] {
            app.lang = lang;

            for page in [
                Page::Status,
                Page::Permissions,
                Page::Approvals,
                Page::Runtime,
                Page::Settings,
            ] {
                app.page = page;
                let _view = app.view();
            }

            // Test specific view functions directly
            let _status_view = super::status::view(&app);
            let _perm_view = super::permissions::view(&app);
            let _appr_view = super::approvals::view(&app);
            let _runt_view = super::runtime::view(&app);
            let _sett_view = super::settings::view(&app);
        }
    }

    #[test]
    fn test_approvals_view_with_pending_items() {
        let (mut app, _task) = App::new(false);
        let prompt = ApprovalPrompt {
            request_id: "req-123".into(),
            operation: "test.terminal.exec".into(),
            target_summary: "Execute /bin/sh".into(),
            target_digest: "sha256-abc123456789".into(),
            expires_at: "2026-12-31T23:59:59Z".into(),
            nonce: "nonce-xyz".into(),
        };

        let snapshot = StateSnapshot {
            boot_id: "boot-test".into(),
            sequence: 1,
            policy_revision: 1,
            status: AgentStatus::Online,
            activity: vec![],
            availability: std::collections::BTreeMap::new(),
            policy: None,
            pending_approvals: vec![prompt],
            recent_control_results: vec![
                ControlCompletion::Stop {
                    receipt_id: "rcpt-1".into(),
                    cleanup_state: "clean".into(),
                    failed_resource_ids: vec![],
                },
            ],
            identity: None,
        };

        app.model.snapshot = Some(snapshot);

        for lang in [Language::Ko, Language::En] {
            app.lang = lang;
            let _appr_view = super::approvals::view(&app);
            let _status_view = super::status::view(&app);
        }
    }
}
