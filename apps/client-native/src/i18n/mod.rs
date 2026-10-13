//! Independent i18n (Internationalization) system for RACP native client.
//! Decoupled from view widgets, with compile-time checked keys.

pub mod en;
pub mod ko;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum Language {
    #[default]
    Ko,
    En,
}

impl Language {
    pub const ALL: &'static [Language] = &[Language::Ko, Language::En];

    pub fn code(&self) -> &'static str {
        match self {
            Self::Ko => "ko",
            Self::En => "en",
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            Self::Ko => "한국어 (Korean, ko-KR)",
            Self::En => "English (en-US)",
        }
    }

    pub fn from_code(code: &str) -> Option<Self> {
        match code {
            "ko" | "ko-KR" => Some(Self::Ko),
            "en" | "en-US" => Some(Self::En),
            _ => None,
        }
    }
}

impl std::fmt::Display for Language {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.label())
    }
}

/// Detects the OS default language with cross-platform fallback.
pub fn detect_os_language() -> Language {
    #[cfg(windows)]
    {
        // 0x0412 is Korean (ko-KR) LCID
        let lcid = unsafe { windows_sys::Win32::Globalization::GetUserDefaultUILanguage() };
        if lcid == 0x0412 {
            Language::Ko
        } else {
            Language::En
        }
    }
    #[cfg(not(windows))]
    {
        let lang = std::env::var("LANG")
            .or_else(|_| std::env::var("LC_ALL"))
            .unwrap_or_default()
            .to_lowercase();
        if lang.starts_with("ko") {
            Language::Ko
        } else {
            Language::En
        }
    }
}

/// Enumeration of all translatable text keys in the client application.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum TextKey {
    // Navigation & Header
    AppTitle,
    AppBadge,
    NavStatus,
    NavPermissions,
    NavApprovals,
    NavRuntime,
    NavSettings,
    NavQuit,
    DeviceIdentity,
    DpapiVaultEnforced,
    StatusOnline,
    RoleController,
    RoleObserver,
    BtnEmergencyStop,

    // Common Actions
    Save,
    Cancel,
    Refresh,
    Clear,
    Copy,
    Search,
    Close,
    Success,
    Error,

    // Status & Enrollment View
    AgentPending,
    AgentStopped,
    AgentRunning,
    EnrollBannerTitle,
    EnrollBannerDesc,
    LocalStatePath,
    BtnRefreshLocal,
    EnrollFormTitle,
    EnrollFormDesc,
    LblConnPath,
    BtnInspectConn,
    ConnPreviewTitle,
    ConnGateway,
    ConnExpires,
    ConnCaHash,
    LblConnHash,
    LblDefaultWorkspace,
    LblExecProfile,
    BtnEnrollSubmit,
    BtnStartAgent,
    BtnStopAgent,
    BtnConnectDesktop,
    StatusLogTitle,
    BtnClearLog,
    BtnCopyLog,
    RecentActivityTitle,
    RecentActivityEmpty,
    BtnSafeCancel,

    // Permissions View
    PermissionsTitle,
    PermissionsSubtitle,
    UnsavedChanges,
    BtnCommitCas,
    GlobalPresetsLabel,
    PresetAllDeny,
    PresetAllPrompt,
    PresetAllAllow,
    ExecutionConstraintsTitle,
    AllowExecutablesLabel,
    DenyExecutablesLabel,
    BtnReloadConstraints,
    ConstraintsHint,
    CategoryBatchLabel,
    BtnBatchDeny,
    BtnBatchPrompt,
    BtnBatchAllow,
    GrantDeny,
    GrantPrompt,
    GrantAllow,
    TooltipDeny,
    TooltipPrompt,
    TooltipAllow,

    // Category Titles & Descs
    CatSystemTitle,
    CatSystemDesc,
    CatFilesReadTitle,
    CatFilesReadDesc,
    CatFilesWriteTitle,
    CatFilesWriteDesc,
    CatTerminalTitle,
    CatTerminalDesc,
    CatAnalysisTitle,
    CatAnalysisDesc,
    CatBrowserTitle,
    CatBrowserDesc,
    CatUnknownTitle,

    // Approvals View
    ApprovalsTitle,
    ApprovalsDesc,
    NoPendingApprovals,
    RiskHigh,
    RiskMedium,
    RiskLow,
    TtlRemaining,
    BtnApproveOnce,
    BtnDenyRequest,
    TargetDigest,
    TargetSummary,

    // Runtime View
    RuntimeTitle,
    RuntimeDesc,
    RuntimeManifestPath,
    RuntimeHash,
    RuntimeSource,
    ChromiumHash,
    CdbHash,
    GdbHash,
    AnalysisHash,
    BrowserOrigins,
    ExternalCdp,
    BtnAdmitRuntime,
    BtnConfigureRuntime,
    AdmittedRuntimesTitle,
    NoAdmittedRuntimes,

    // Settings View
    SettingsTitle,
    SettingsDesc,
    LangSettingTitle,
    LangSettingDesc,
    InterfaceLanguage,
    NamedWorkspacesTitle,
    NamedWorkspacesDesc,
    BtnAddWorkspace,
    BtnSaveSettings,
    BtnReloadSettings,
    WindowsStartupTitle,
    WindowsStartupDesc,
    BtnEnableStartup,
    BtnDisableStartup,
    BtnUpdateStartup,
    BtnReconcileStartup,
    ServiceSettingNotice,
    NoNamedWorkspaces,
}

/// Translates a key for a given language.
pub fn tr(lang: Language, key: TextKey) -> &'static str {
    match lang {
        Language::Ko => ko::translate(key),
        Language::En => en::translate(key),
    }
}

/// Helper macro for concise translation in view widgets.
#[macro_export]
macro_rules! tr {
    ($lang:expr, $key:ident) => {
        $crate::i18n::tr($lang, $crate::i18n::TextKey::$key)
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_language_codes() {
        assert_eq!(Language::Ko.code(), "ko");
        assert_eq!(Language::En.code(), "en");
        assert_eq!(Language::from_code("ko"), Some(Language::Ko));
        assert_eq!(Language::from_code("en"), Some(Language::En));
        assert_eq!(Language::from_code("fr"), None);
    }

    #[test]
    fn test_translations_non_empty() {
        let keys = [
            TextKey::AppTitle,
            TextKey::NavStatus,
            TextKey::NavPermissions,
            TextKey::NavApprovals,
            TextKey::NavRuntime,
            TextKey::NavSettings,
            TextKey::NavQuit,
            TextKey::GrantDeny,
            TextKey::GrantPrompt,
            TextKey::GrantAllow,
            TextKey::CatSystemTitle,
            TextKey::CatFilesReadTitle,
            TextKey::CatFilesWriteTitle,
            TextKey::CatTerminalTitle,
            TextKey::CatAnalysisTitle,
            TextKey::CatBrowserTitle,
        ];
        for lang in [Language::Ko, Language::En] {
            for key in keys {
                let text = tr(lang, key);
                assert!(!text.is_empty(), "Translation for {:?} in {:?} was empty", key, lang);
            }
        }
    }
}

