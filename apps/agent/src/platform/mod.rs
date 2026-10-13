//! Windows is the only implemented service/setup adapter.
//! macOS launchd and Linux systemd/device adapters remain unimplemented.
#[cfg(windows)]
pub mod windows;
