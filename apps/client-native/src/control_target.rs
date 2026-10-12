//! One explicit control target per GUI process. Service mode never opens user state.
use racp_contract::RacpError;
use std::{path::PathBuf, sync::OnceLock};
static SERVICE_DIRECTORY: OnceLock<PathBuf> = OnceLock::new();
pub fn select_service(directory: PathBuf) -> Result<(), RacpError> {
    let directory = racp_core::validate_local_path(&directory)?;
    SERVICE_DIRECTORY
        .set(directory)
        .map_err(|_| RacpError::new("REQUEST_INVALID"))
}
pub fn service() -> bool {
    SERVICE_DIRECTORY.get().is_some()
}
pub fn root() -> Result<PathBuf, RacpError> {
    SERVICE_DIRECTORY
        .get()
        .cloned()
        .map(Ok)
        .unwrap_or_else(racp_core::state_schema::default_user_root)
}
#[cfg(windows)]
pub fn endpoint() -> Result<racp_runtime::local_ipc::LocalEndpoint, RacpError> {
    let root = root()?;
    if service() {
        racp_runtime::local_ipc::LocalEndpoint::load_service(&root)
    } else {
        racp_runtime::local_ipc::LocalEndpoint::load(&root)
    }
}
