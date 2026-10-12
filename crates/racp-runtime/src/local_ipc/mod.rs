//! GUI control endpoint is separate from privileged Desktop Broker IPC; no TCP fallback.
pub mod approval;
mod cancellation;
pub mod events;
pub mod policy;
#[cfg(windows)]
mod service_identity;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub(crate) use service_identity::allow_queries as allow_service_identity_queries;
#[cfg(windows)]
pub use windows::{connect, serve, serve_endpoint, ControlSession, LocalEndpoint};

#[cfg(not(windows))]
pub async fn connect(_: &std::path::Path) -> Result<(), racp_contract::RacpError> {
    // A plaintext/unauthenticated fallback must never masquerade as native OS IPC.
    Err(racp_contract::RacpError::new("LOCAL_IPC_NOT_IMPLEMENTED"))
}
