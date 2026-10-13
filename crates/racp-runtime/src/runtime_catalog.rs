//! One startup snapshot of explicitly selected, immutable optional runtime bundles.
use crate::providers::ProviderRuntimes;
use racp_contract::{runtime_manifest::RuntimeId, RacpError};
use racp_core::runtime_admission::*;
use std::{
    collections::BTreeMap,
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
pub struct Prepared {
    pub config: NativeRuntimeConfig,
    pub chromium: Option<Arc<PinnedRuntime>>,
    pub providers: ProviderRuntimes,
    pub errors: BTreeMap<&'static str, &'static str>,
}
pub async fn prepare(root: &Path, shutdown: &CancellationToken) -> Result<Prepared, RacpError> {
    let root = root.to_owned();
    let cancelled = Arc::new(AtomicBool::new(false));
    let signal = cancelled.clone();
    let mut work = tokio::task::spawn_blocking(move || {
        let config = NativeRuntimeConfig::load(&root)?;
        let budget = AdmissionBudget {
            deadline: Instant::now() + Duration::from_secs(120),
            cancelled,
        };
        let mut result = Prepared {
            config: config.clone(),
            chromium: None,
            providers: ProviderRuntimes::default(),
            errors: BTreeMap::new(),
        };
        result.providers.hardware_discovery_enabled = config.hardware_discovery_enabled;
        for (name, id, selected) in [
            ("chromium", RuntimeId::Chromium, &config.chromium_manifest),
            ("cdb", RuntimeId::Cdb, &config.cdb_manifest),
            ("gdb", RuntimeId::Gdb, &config.gdb_manifest),
            ("analysis", RuntimeId::Ghidra, &config.analysis_manifest),
        ] {
            let Some(hash) = selected else {
                continue;
            };
            let pinned = pin(&root, hash, &budget).and_then(|runtime| {
                if runtime.manifest.runtime.id != id {
                    return Err(RacpError::new("RUNTIME_MANIFEST_INVALID"));
                }
                validate_selection(&runtime.manifest)?;
                let runtime = Arc::new(runtime);
                #[cfg(windows)]
                if id == RuntimeId::Cdb {
                    crate::providers::validate_cdb(runtime.clone())?;
                }
                Ok(runtime)
            });
            match pinned {
                Ok(runtime) => match id {
                    RuntimeId::Chromium => result.chromium = Some(runtime),
                    RuntimeId::Cdb => result.providers.cdb = Some(runtime),
                    RuntimeId::Gdb => result.providers.gdb = Some(runtime),
                    RuntimeId::Ghidra => result.providers.analysis = Some(runtime),
                    _ => (),
                },
                Err(error) if error.code.0 == "CANCELLED" => return Err(error),
                Err(error) => {
                    result.errors.insert(name, error.code.0);
                }
            }
        }
        Ok(result)
    });
    tokio::select! {result=&mut work=>result.map_err(|_|RacpError::new("EXECUTION_UNKNOWN"))?,_=shutdown.cancelled()=>{signal.store(true,Ordering::SeqCst);work.await.map_err(|_|RacpError::new("EXECUTION_UNKNOWN"))?}}
}
