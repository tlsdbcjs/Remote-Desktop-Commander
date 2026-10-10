use futures_util::future::BoxFuture;
use racp_contract::RacpError;
use serde_json::Value;
use tokio_util::sync::CancellationToken;

/// Providers must finish resource cleanup before returning after cancellation.
pub trait Provider: Send + Sync {
    fn capabilities(&self) -> Vec<Value>;
    fn execute(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>>;
    fn owns_process(&self, _: &Value) -> bool {
        false
    }
    fn inventory(&self) -> Vec<Value> {
        vec![]
    }
    fn events_after(&self, _: u64) -> Vec<Value> {
        vec![]
    }
    fn event_cursor(&self) -> String {
        "0".into()
    }
    fn event_sent(&self, _instance: &str, _sequence: &str) -> Result<(), RacpError> {
        Err(RacpError::new("INVALID_ARGUMENT"))
    }
    fn event_ack(&self, _instance: &str, _sequence: &str) -> Result<(), RacpError> {
        Err(RacpError::new("INVALID_ARGUMENT"))
    }
    fn stream_read(&self, _: &Value) -> Result<Value, Value> {
        Err(racp_core::error_value(
            "CAPABILITY_UNAVAILABLE",
            "terminal stream unavailable",
            "not_started",
        ))
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async { Ok(()) })
    }
}
pub struct EmptyProvider;
impl Provider for EmptyProvider {
    fn capabilities(&self) -> Vec<Value> {
        vec![]
    }
    fn execute(&self, _: Value, _: CancellationToken) -> BoxFuture<'_, Result<Value, RacpError>> {
        Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) })
    }
}
mod filesystem;
mod os_observation;
pub use filesystem::Filesystem;
mod cursor;
mod encoding;
mod shell;
pub use shell::Shell;
mod containment;
mod process_memory;
mod processes;
pub use processes::Processes;
pub mod browser;
pub mod desktop;
mod terminal;
use std::sync::Arc;
pub use terminal::{Terminal, TerminalBuffer};
pub struct NativeProviders {
    providers: Vec<Arc<dyn Provider>>,
}
impl NativeProviders {
    pub fn new(settings: &racp_core::AgentSettings, boot: &str) -> Result<Self, RacpError> {
        Self::with_browser(settings, boot, browser::BrowserConfig::bundled())
    }
    pub fn with_browser(
        settings: &racp_core::AgentSettings,
        boot: &str,
        config: browser::BrowserConfig,
    ) -> Result<Self, RacpError> {
        let providers: Vec<Arc<dyn Provider>> = vec![
            Arc::new(Filesystem::new(settings)?),
            Arc::new(os_observation::OSObservation::new(settings, boot)),
            Arc::new(Shell::new(settings)?),
            Arc::new(Processes::new(settings, boot)?),
            Arc::new(Terminal::new(settings, boot)?),
            Arc::new(browser::Browser::new(settings, boot, config)?),
            Arc::new(reversing::Reversing::new(settings, boot)?),
        ];
        #[cfg(windows)]
        let providers = {
            let mut providers = providers;
            providers.push(Arc::new(desktop::Desktop::new(settings)?));
            providers
        };
        Ok(Self { providers })
    }
}
impl Provider for NativeProviders {
    fn owns_process(&self, r: &Value) -> bool {
        self.providers.iter().any(|p| p.owns_process(r))
    }
    fn events_after(&self, after: u64) -> Vec<Value> {
        self.providers
            .iter()
            .flat_map(|p| p.events_after(after))
            .collect()
    }
    fn event_cursor(&self) -> String {
        self.providers
            .iter()
            .map(|p| p.event_cursor())
            .find(|s| s != "0")
            .unwrap_or_else(|| "0".into())
    }
    fn event_sent(&self, instance: &str, sequence: &str) -> Result<(), RacpError> {
        self.providers
            .iter()
            .find(|p| p.capabilities().iter().any(|c| c["name"] == "browser"))
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
            .event_sent(instance, sequence)
    }
    fn event_ack(&self, instance: &str, sequence: &str) -> Result<(), RacpError> {
        self.providers
            .iter()
            .find(|p| p.capabilities().iter().any(|c| c["name"] == "browser"))
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
            .event_ack(instance, sequence)
    }
    fn stream_read(&self, request: &Value) -> Result<Value, Value> {
        match self
            .providers
            .iter()
            .find(|p| p.capabilities().iter().any(|c| c["name"] == "terminal"))
        {
            Some(p) => p.stream_read(request),
            None => Err(racp_core::error_value(
                "CAPABILITY_UNAVAILABLE",
                "terminal stream unavailable",
                "not_started",
            )),
        }
    }
    fn capabilities(&self) -> Vec<Value> {
        self.providers
            .iter()
            .flat_map(|p| p.capabilities())
            .collect()
    }
    fn inventory(&self) -> Vec<Value> {
        self.providers.iter().flat_map(|p| p.inventory()).collect()
    }
    fn execute(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        let operation = request["operation"].as_str().unwrap_or("");
        match self.providers.iter().find(|p| {
            p.capabilities().iter().any(|c| {
                c["operations"]
                    .as_array()
                    .is_some_and(|ops| ops.iter().any(|op| op == operation))
            })
        }) {
            Some(provider) => provider.execute(request, cancel),
            None => Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) }),
        }
    }
    fn cleanup(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            for p in &self.providers {
                p.cleanup().await?;
            }
            Ok(())
        })
    }
}

pub mod reversing;
