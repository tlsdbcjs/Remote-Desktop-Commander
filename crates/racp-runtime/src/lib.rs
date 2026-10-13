mod network;
pub use network::http_client;

mod identity;
mod peer;
mod transport;
pub use transport::Agent;

mod dispatch;
mod health;
pub mod platform;
pub mod providers;

pub mod artifacts;
mod streams;

pub mod local_agent;
pub mod local_ipc;
pub mod native_client;
pub mod native_login;
#[cfg(windows)]
pub mod native_maintenance;
pub mod native_portable;
pub mod resources;
pub mod runtime_catalog;
#[cfg(windows)]
pub mod service_bootstrap;
#[cfg(windows)]
pub mod service_maintenance;
/// Native host primitives; analysis engines and vendor bridges are not part of this library.
pub mod tool_support {
    pub use crate::identity::ProtectedProcess;
    pub use crate::providers::containment::{environment, CommandSpec, OwnedProcess};
    pub use crate::providers::reversing::{file_hash, plugin::decode};
}
