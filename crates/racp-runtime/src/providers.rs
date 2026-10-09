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
