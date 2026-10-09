//! Chromium is controlled directly over CDP; no language runtime is launched.
mod attach;
pub mod cdp;
mod events;
mod files;
mod keys;
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
    pub fn bundled() -> Self {
        #[cfg(windows)]
        let binary = "chrome-win64/chrome.exe";
        #[cfg(not(windows))]
        let binary = "chrome-linux64/chrome";
        let mut candidates = vec![];
        if let Ok(executable) = std::env::current_exe() {
            if let Some(parent) = executable.parent() {
                candidates.push(parent.join("chromium").join(if cfg!(windows) {
                    "chrome.exe"
                } else {
                    "chrome"
                }));
                if let Some(root) = parent.parent() {
                    candidates.push(root.join("chromium").join(if cfg!(windows) {
                        "chrome.exe"
                    } else {
                        "chrome"
                    }));
                }
            }
        }
        if cfg!(debug_assertions) {
            candidates.push(
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../../.tools/playwright/chromium-1243")
                    .join(binary),
            );
        }
        Self {
            executable: candidates.into_iter().find(|p| p.is_file()),
            ..Default::default()
        }
    }
    pub(super) fn script(&self) -> String {
        let origins = self
            .allow_origins
            .iter()
            .filter_map(|origin| {
                Url::parse(origin)
                    .ok()
                    .map(|url| url.origin().ascii_serialization())
            })
            .collect::<Vec<_>>();
        let origins = serde_json::to_string(&origins).expect("origin strings");
        format!(
            r#"(()=>{{
            const allowed=new Set({origins});
            const check=(value)=>{{const url=new URL(value,globalThis.location?.href);if(url.username||url.password)throw new DOMException('network policy','SecurityError');if(url.protocol==='http:')url.protocol='ws:';if(url.protocol==='https:')url.protocol='wss:';if(!['ws:','wss:'].includes(url.protocol))throw new DOMException('network policy','SecurityError');url.protocol=url.protocol==='wss:'?'https:':'http:';if(allowed.size&&!allowed.has(url.origin))throw new DOMException('network policy','SecurityError');}};
            for(const name of ['WebSocket','WebSocketStream']){{
                const Native=globalThis[name];if(typeof Native!=='function')continue;
                const descriptor=Object.getOwnPropertyDescriptor(globalThis,name);if(descriptor?.configurable===false)continue;
                const Guard=new Proxy(Native,{{construct(target,args,newTarget){{check(args[0]);return Reflect.construct(target,args,newTarget);}}}});
                if(Native.prototype)Object.defineProperty(Native.prototype,'constructor',{{value:Guard,writable:false,configurable:false}});
                Object.defineProperty(globalThis,name,{{value:Guard,writable:false,configurable:false}});
            }}
            if(globalThis.ServiceWorkerContainer){{const proto=ServiceWorkerContainer.prototype;const descriptor=Object.getOwnPropertyDescriptor(proto,'register');if(descriptor?.configurable!==false)Object.defineProperty(proto,'register',{{value:()=>Promise.reject(new DOMException('service workers disabled','SecurityError')),writable:false,configurable:false}});}}
        }})()"#
        )
    }
    pub fn allowed_socket(&self, value: &str) -> bool {
        let Ok(mut url) = Url::parse(value) else {
            return false;
        };
        let scheme = match url.scheme() {
            "ws" => "http",
            "wss" => "https",
            _ => return false,
        };
        if url.set_scheme(scheme).is_err() {
            return false;
        }
        self.allowed(url.as_str())
    }
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
