mod journal;
pub mod journal_schema;
pub use journal::{error_value, Acceptance, Journal, JournalHealth, Record};
pub mod approval;
mod connection;
pub mod distribution;
pub mod hardware;
#[cfg(windows)]
pub mod machine_service;
mod native_directory;
pub mod native_lifecycle;
pub mod native_release;
pub mod native_service;
#[cfg(windows)]
pub mod service_config;
#[cfg(windows)]
pub mod service_directory;
pub mod service_provision;
pub mod service_start;
pub use native_directory::{
    create as create_private_native_directory, verify as verify_private_native_directory,
};
mod paths;
pub mod policy;
pub mod runtime_admission;
mod secrets;
mod settings;
pub mod state_schema;
pub use connection::{inspect_connection, load_connection, validate_ca, ConnectionFile};
pub use paths::{atomic_write, private_dir, read_bounded, validate_local_path, InstanceLock};
pub use secrets::SecretStore;
pub use settings::{gateway_origin, AgentSettings, WorkspaceSpec};
mod outputs;
pub use outputs::OutputSpool;

pub use paths::{secure_append_file, secure_create_file, secure_read_file};
mod workspaces;
pub use workspaces::{path_within, Directory, FileInfo, Workspaces};

pub use outputs::browser_temporary_usage;

mod permissions;
pub use permissions::{
    authorize, authorize_with_approval, desktop_enabled, permission_allows_approved_field,
    permission_allows_field, validate_permissions,
};
