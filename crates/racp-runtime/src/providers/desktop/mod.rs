//! Session-explicit desktop authority; native observations are supplied only by the paired Broker.
mod state;
pub use state::{DesktopState, Scope, Snapshot};
mod watch;
pub use watch::{HeldInputs, InterruptionCounter, ReleaseInput};
mod pairing;
pub use pairing::{PairConfig, PeerIdentity};
mod wire;
pub use wire::{decode_pipe_message, encode_pipe_message, MAX_PIPE_MESSAGE};
#[cfg(windows)]
mod broker;
#[cfg(windows)]
mod native;
#[cfg(windows)]
pub use broker::run_broker;
#[cfg(windows)]
mod provider;
#[cfg(windows)]
pub use provider::Desktop;
#[cfg(windows)]
pub mod native_identity;
#[cfg(windows)]
pub mod pipe;

#[cfg(windows)]
mod hooks;

#[cfg(windows)]
mod guardian;
#[cfg(windows)]
pub use guardian::run_guardian;

#[cfg(windows)]
mod input;

#[cfg(windows)]
mod automation;
#[cfg(windows)]
mod clipboard;

#[cfg(windows)]
mod access;
#[cfg(windows)]
mod login;
#[cfg(windows)]
pub use login::{configure_login, run_login_broker, verify_config_identity};
