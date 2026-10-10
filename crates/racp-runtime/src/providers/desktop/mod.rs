//! Session-explicit desktop authority; native observations are supplied only by the paired Broker.
mod state;
pub use state::{DesktopState, Scope, Snapshot};
mod watch;
pub use watch::{HeldInputs, InterruptionCounter, ReleaseInput};
mod pairing;
pub use pairing::{PairConfig, PeerIdentity};
