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
    fn inventory(&self) -> Vec<Value> {
        vec![]
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
pub use filesystem::Filesystem;
mod cursor;
mod encoding;
mod shell;
pub use shell::Shell;
mod containment;
mod processes;
pub use processes::Processes;
use std::sync::Arc;
pub struct NativeProviders {
    providers: Vec<Arc<dyn Provider>>,
}
impl NativeProviders {
    pub fn new(settings: &racp_core::AgentSettings, boot: &str) -> Result<Self, RacpError> {
        Ok(Self {
            providers: vec![
                Arc::new(Filesystem::new(settings)?),
                Arc::new(Shell::new(settings)?),
                Arc::new(Processes::new(settings, boot)?),
            ],
        })
    }
}
impl Provider for NativeProviders {
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
