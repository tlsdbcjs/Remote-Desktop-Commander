//! Chromium is controlled directly over CDP; no language runtime is launched.
pub mod cdp;
mod events;
mod operations;
pub use events::BrowserOutbox;
mod session;
pub use session::Browser;
use std::path::PathBuf;
use url::Url;

#[derive(Clone, Default)]
pub struct BrowserConfig {
    pub executable: Option<PathBuf>,
    pub allow_origins: Vec<String>,
    pub cdp_enabled: bool,
}
impl BrowserConfig {
    pub fn allowed(&self, value: &str) -> bool {
        let Ok(url) = Url::parse(value) else {
            return false;
        };
        if !matches!(url.scheme(), "http" | "https")
            || url.host().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
        {
            return false;
        }
        self.allow_origins.is_empty()
            || self.allow_origins.iter().any(|value| {
                Url::parse(value).is_ok_and(|allowed| allowed.origin() == url.origin())
            })
    }
}
