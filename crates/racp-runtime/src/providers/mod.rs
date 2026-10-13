use futures_util::future::BoxFuture;
use racp_contract::RacpError;
use serde_json::Value;
use tokio_util::sync::CancellationToken;

/// Providers must finish resource cleanup before returning after cancellation.
pub trait Provider: Send + Sync {
    #[cfg(windows)]
    fn remove_desktop(
        &self,
        _: desktop::PeerIdentity,
        _: String,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) })
    }
    #[cfg(windows)]
    fn prepare_desktop(
        &self,
        _: desktop::PeerIdentity,
    ) -> BoxFuture<'_, Result<racp_contract::local_ipc::DesktopSeed, RacpError>> {
        Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) })
    }
    #[cfg(windows)]
    fn register_desktop(
        &self,
        _: desktop::PeerIdentity,
        _: String,
        _: u32,
        _: f64,
        _: u32,
        _: f64,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) })
    }
    fn frame_request(&self, _: &Value) -> Result<Value, RacpError> {
        Err(RacpError::new("CAPABILITY_UNAVAILABLE"))
    }
    fn retire_handle(&self, _: &Value) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) })
    }
    fn policy_changed(
        &self,
        _: racp_core::policy::PolicySnapshot,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async { Ok(()) })
    }
    fn connection(&self, _: u64, _: std::time::Duration) {}
    fn native_frames(&self) -> Vec<Value> {
        vec![]
    }
    fn native_message(&self, _: Value) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) })
    }
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
    /// Retire expired-lease resources while preserving registration infrastructure.
    fn expire_lease(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        self.cleanup()
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
mod diagnostics;
mod jobs;
pub use diagnostics::run_diagnostic_worker;
mod filesystem;
mod native_carrier;
#[cfg(windows)]
pub(crate) use native_carrier::validate_cdb;
mod os_observation;
pub mod recipes;
pub use filesystem::run_preview_worker;
#[cfg(windows)]
pub use filesystem::run_trash_worker;
pub use filesystem::Filesystem;
mod cursor;
mod encoding;
mod shell;
pub(crate) mod worker;
pub use shell::Shell;
pub(crate) mod containment;
mod process_memory;
mod processes;
pub use processes::Processes;
pub mod browser;
pub mod desktop;
pub mod hardware;
mod terminal;
use std::sync::Arc;
pub use terminal::{Terminal, TerminalBuffer};
pub struct NativeProviders {
    providers: Vec<Arc<dyn Provider>>,
}
#[derive(Clone, Default)]
pub struct ProviderRuntimes {
    pub cdb: Option<Arc<racp_core::runtime_admission::PinnedRuntime>>,
    pub gdb: Option<Arc<racp_core::runtime_admission::PinnedRuntime>>,
    pub analysis: Option<Arc<racp_core::runtime_admission::PinnedRuntime>>,
}
impl NativeProviders {
    pub(crate) fn with_jobs(
        mut self,
        journal: racp_core::Journal,
        tasks: jobs::Executions,
    ) -> Self {
        self.providers
            .push(Arc::new(jobs::Jobs::new(journal, tasks)));
        self
    }
    pub fn new(settings: &racp_core::AgentSettings, boot: &str) -> Result<Self, RacpError> {
        Self::with_browser(settings, boot, browser::BrowserConfig::default())
    }
    pub fn with_browser(
        settings: &racp_core::AgentSettings,
        boot: &str,
        config: browser::BrowserConfig,
    ) -> Result<Self, RacpError> {
        Self::with_runtimes(settings, boot, config, ProviderRuntimes::default())
    }
    pub fn with_runtimes(
        settings: &racp_core::AgentSettings,
        boot: &str,
        config: browser::BrowserConfig,
        runtimes: ProviderRuntimes,
    ) -> Result<Self, RacpError> {
        let providers: Vec<Arc<dyn Provider>> = vec![
            Arc::new(Filesystem::new(settings)?),
            Arc::new(diagnostics::Diagnostics::new(settings, boot)),
            Arc::new(os_observation::OSObservation::new(settings, boot)),
            Arc::new(recipes::Recipes::new(settings, boot)?),
            Arc::new(native_carrier::NativeCarrier::with_runtimes(
                settings,
                boot,
                runtimes.clone(),
            )?),
            Arc::new(Shell::new(settings)?),
            Arc::new(Processes::new(settings, boot)?),
            Arc::new(Terminal::new(settings, boot)?),
            Arc::new(browser::Browser::new(settings, boot, config)?),
            Arc::new(reversing::Reversing::with_runtimes(
                settings, boot, runtimes,
            )?),
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
    #[cfg(windows)]
    fn remove_desktop(
        &self,
        peer: desktop::PeerIdentity,
        pair: String,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        match self.providers.iter().find(|provider| {
            provider
                .capabilities()
                .iter()
                .any(|cap| cap["name"] == "desktop")
        }) {
            Some(provider) => provider.remove_desktop(peer, pair),
            None => Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) }),
        }
    }
    #[cfg(windows)]
    fn prepare_desktop(
        &self,
        peer: desktop::PeerIdentity,
    ) -> BoxFuture<'_, Result<racp_contract::local_ipc::DesktopSeed, RacpError>> {
        match self.providers.iter().find(|provider| {
            provider
                .capabilities()
                .iter()
                .any(|cap| cap["name"] == "desktop")
        }) {
            Some(provider) => provider.prepare_desktop(peer),
            None => Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) }),
        }
    }
    #[cfg(windows)]
    fn register_desktop(
        &self,
        peer: desktop::PeerIdentity,
        pair: String,
        broker: u32,
        created: f64,
        guardian: u32,
        birth: f64,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        match self.providers.iter().find(|provider| {
            provider
                .capabilities()
                .iter()
                .any(|cap| cap["name"] == "desktop")
        }) {
            Some(provider) => {
                provider.register_desktop(peer, pair, broker, created, guardian, birth)
            }
            None => Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) }),
        }
    }
    fn frame_request(&self, frame: &Value) -> Result<Value, RacpError> {
        self.providers
            .iter()
            .find(|p| p.capabilities().iter().any(|c| c["name"] == "native"))
            .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?
            .frame_request(frame)
    }
    fn retire_handle(&self, handle: &Value) -> BoxFuture<'_, Result<(), RacpError>> {
        let id = &handle["id"];
        match self
            .providers
            .iter()
            .find(|provider| provider.inventory().iter().any(|h| &h["id"] == id))
        {
            Some(provider) => provider.retire_handle(handle),
            None => Box::pin(async { Err(RacpError::new("HANDLE_NOT_FOUND")) }),
        }
    }
    fn policy_changed(
        &self,
        permissions: racp_core::policy::PolicySnapshot,
    ) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            let mut error = None;
            for provider in &self.providers {
                if let Err(e) = provider.policy_changed(permissions.clone()).await {
                    error = Some(e);
                }
            }
            error.map_or(Ok(()), Err)
        })
    }
    fn connection(&self, epoch: u64, ttl: std::time::Duration) {
        for p in &self.providers {
            p.connection(epoch, ttl);
        }
    }
    fn native_frames(&self) -> Vec<Value> {
        self.providers
            .iter()
            .flat_map(|p| p.native_frames())
            .collect()
    }
    fn native_message(&self, message: Value) -> BoxFuture<'_, Result<(), RacpError>> {
        match self
            .providers
            .iter()
            .find(|p| p.capabilities().iter().any(|c| c["name"] == "native"))
        {
            Some(p) => p.native_message(message),
            None => Box::pin(async { Err(RacpError::new("CAPABILITY_UNAVAILABLE")) }),
        }
    }
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
        wire_capabilities(self.providers.iter().flat_map(|p| p.capabilities()))
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
    fn expire_lease(&self) -> BoxFuture<'_, Result<(), RacpError>> {
        Box::pin(async move {
            for p in &self.providers {
                p.expire_lease().await?;
            }
            Ok(())
        })
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

/// Provider implementation names are not wire capability names. Advertise only
/// operations from the registry, combining available members without enabling
/// an unavailable member (for example administrator-only network capture).
fn wire_capabilities(source: impl IntoIterator<Item = Value>) -> Vec<Value> {
    use std::collections::{BTreeMap, BTreeSet};
    let mut groups: BTreeMap<String, Vec<(Value, String)>> = BTreeMap::new();
    for capability in source {
        for operation in capability["operations"].as_array().into_iter().flatten() {
            let Some(operation) = operation.as_str() else {
                continue;
            };
            let Some(name) = racp_contract::registry()[operation]["capability"].as_str() else {
                continue;
            };
            groups
                .entry(name.into())
                .or_default()
                .push((capability.clone(), operation.into()));
        }
    }
    groups
        .into_iter()
        .map(|(name, members)| {
            let available = |cap: &Value| cap["enabled"] == true && cap["healthy"] == true;
            let active = members.iter().any(|(cap, _)| available(cap));
            let operations: BTreeSet<_> = members
                .iter()
                .filter(|(cap, _)| !active || available(cap))
                .map(|(_, operation)| operation.clone())
                .collect();
            let mut result = members[0].0.clone();
            result["name"] = serde_json::json!(name);
            result["operations"] = serde_json::json!(operations);
            for field in ["installed", "supported", "enabled", "healthy"] {
                result[field] =
                    serde_json::json!(members.iter().any(|(cap, _)| cap[field] == true));
            }
            result["unavailable_reason"] = if active {
                Value::Null
            } else {
                members
                    .iter()
                    .find_map(|(cap, _)| {
                        cap.get("unavailable_reason")
                            .filter(|v| !v.is_null())
                            .cloned()
                    })
                    .unwrap_or(Value::Null)
            };
            result
        })
        .collect()
}

#[cfg(test)]
mod capability_tests {
    use super::wire_capabilities;
    use serde_json::json;

    #[test]
    fn registry_groups_preserve_operations_and_unavailable_members() {
        let capability = |name, operations, enabled| json!({"name":name,"version":"1.0.0","operations":operations,"installed":true,"supported":true,"enabled":enabled,"healthy":enabled,"unavailable_reason":if enabled {None}else{Some("needs_administrator")},"attributes":{}});
        let values = wire_capabilities([
            capability(
                "os_observation",
                vec!["system.info", "network.interfaces", "services.list"],
                true,
            ),
            capability("network_capture", vec!["network.capture"], false),
            capability("process_dump", vec!["process.dump"], true),
            capability("process", vec!["process.list"], true),
        ]);
        let network = values.iter().find(|cap| cap["name"] == "network").unwrap();
        assert_eq!(network["operations"], json!(["network.interfaces"]));
        let process = values.iter().find(|cap| cap["name"] == "process").unwrap();
        assert_eq!(
            process["operations"],
            json!(["process.dump", "process.list"])
        );
        for cap in values {
            for operation in cap["operations"].as_array().unwrap() {
                assert_eq!(
                    racp_contract::registry()[operation.as_str().unwrap()]["capability"],
                    cap["name"]
                );
            }
        }
    }
}

pub mod reversing;
