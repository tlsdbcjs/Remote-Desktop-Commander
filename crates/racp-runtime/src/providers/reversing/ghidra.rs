//! Hash-pinned installed Java/Ghidra; workspace-private headless databases and fixed bridge.
use super::plugin;
use crate::{
    identity::ProtectedProcess,
    providers::containment::{self, CommandSpec, OwnedProcess},
};
use racp_contract::{digest, new_id, RacpError};
use racp_core::{atomic_write, private_dir, read_bounded, validate_local_path, Workspaces};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    pub version: String,
    pub installation: PathBuf,
    pub java: PathBuf,
    pub java_sha256: String,
    pub files: BTreeMap<String, String>,
}
fn check_budget(deadline: Instant, cancel: &CancellationToken) -> Result<(), RacpError> {
    if cancel.is_cancelled() {
        return Err(RacpError::new("CANCELLED"));
    }
    if Instant::now() >= deadline {
        return Err(RacpError::new("TIMEOUT"));
    }
    Ok(())
}
pub fn file_hash(
    path: &Path,
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<String, RacpError> {
    validate_local_path(path)?;
    let mut file = File::open(path)?;
    use sha2::{Digest, Sha256};
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 262144];
    loop {
        check_budget(deadline, cancel)?;
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
pub struct Ghidra {
    pub catalog: Catalog,
    approved_path: PathBuf,
    approved_hash: String,
    java_checked: bool,
    pub log: Vec<u8>,
}
impl Ghidra {
    pub fn load(path: &Path, approved_hash: &str) -> Result<Self, RacpError> {
        validate_local_path(path)?;
        let bytes = read_bounded(path, 4 * 1024 * 1024, false)?;
        if digest(&bytes) != approved_hash {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let value = plugin::decode(&bytes, 4 * 1024 * 1024)?;
        let catalog: Catalog = serde_json::from_value(value)?;
        if catalog.version.split('.').count() != 3
            || !catalog
                .version
                .split('.')
                .all(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
            || catalog.files.is_empty()
            || catalog.files.len() > 20000
        {
            return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
        }
        validate_local_path(&catalog.installation)?;
        validate_local_path(&catalog.java)?;
        for (path, hash) in catalog.files.iter().chain(std::iter::once((
            &String::from("@java"),
            &catalog.java_sha256,
        ))) {
            if path.is_empty()
                || path.contains(['\\', ':'])
                || Path::new(path).is_absolute()
                || path
                    .split('/')
                    .any(|p| p.is_empty() || matches!(p, "." | ".."))
                || hash.len() != 64
                || !hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
            }
        }
        Ok(Self {
            catalog,
            approved_path: path.into(),
            approved_hash: approved_hash.into(),
            java_checked: false,
            log: vec![],
        })
    }
    pub fn verify(&self, deadline: Instant, cancel: &CancellationToken) -> Result<(), RacpError> {
        if digest(read_bounded(&self.approved_path, 4 * 1024 * 1024, false)?) != self.approved_hash
        {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let mut inventory = BTreeMap::new();
        let mut directories = vec![(self.catalog.installation.clone(), 0usize)];
        while let Some((directory, depth)) = directories.pop() {
            check_budget(deadline, cancel)?;
            validate_local_path(&directory)?;
            if depth > 64 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            for entry in std::fs::read_dir(&directory)? {
                let entry = entry?;
                let path = entry.path();
                validate_local_path(&path)?;
                let kind = entry.file_type()?;
                if kind.is_dir() {
                    if directory != self.catalog.installation || entry.file_name() != "docs" {
                        directories.push((path, depth + 1));
                    }
                } else if kind.is_file() {
                    let relative = path
                        .strip_prefix(&self.catalog.installation)
                        .map_err(|_| RacpError::new("PERMISSION_DENIED"))?
                        .to_string_lossy()
                        .replace('\\', "/");
                    if inventory.len() >= 20000 {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                    }
                    inventory.insert(relative, path);
                } else {
                    return Err(RacpError::new("PERMISSION_DENIED"));
                }
            }
        }
        if inventory.keys().ne(self.catalog.files.keys()) {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        for (key, path) in inventory {
            if file_hash(&path, deadline, cancel)? != self.catalog.files[&key] {
                return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
            }
        }
        if file_hash(&self.catalog.java, deadline, cancel)? != self.catalog.java_sha256 {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        let props = String::from_utf8(read_bounded(
            &self
                .catalog
                .installation
                .join("Ghidra/application.properties"),
            65536,
            false,
        )?)
        .map_err(|_| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
        if !props.lines().any(|line| {
            line.trim_end_matches('\r') == format!("application.version={}", self.catalog.version)
        }) {
            return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
        }
        Ok(())
    }
    fn process(
        &mut self,
        argv: Vec<String>,
        directory: &Path,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<i64, RacpError> {
        let guards = Workspaces::new(directory, &[])?;
        let mut process = OwnedProcess::spawn(CommandSpec {
            argv,
            environment: containment::environment(&Value::Null)?,
            cwd: guards.directory("default", directory)?,
        })?;
        let _protection = ProtectedProcess::register(process.pid())?;
        #[cfg(windows)]
        process.limit_memory(2 * 1024 * 1024 * 1024)?;
        self.log.clear();
        let mut total = 0usize;
        let result = (|| {
            let mut buffer = [0u8; 8192];
            loop {
                check_budget(deadline, cancel)?;
                for pipe in [&mut process.stdout, &mut process.stderr] {
                    for _ in 0..32 {
                        match pipe.read_available(&mut buffer)? {
                            Some(n) if n > 0 => {
                                total += n;
                                if total > 8 * 1024 * 1024 {
                                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                                }
                                self.log.extend_from_slice(&buffer[..n]);
                                if self.log.len() > 65536 {
                                    self.log.drain(..self.log.len() - 65536);
                                }
                            }
                            _ => break,
                        }
                    }
                }
                if let Some(code) = process.poll()? {
                    return Ok(code);
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        })();
        process.kill_tree()?;
        let until = Instant::now() + Duration::from_secs(5);
        while !process.tree_empty()? {
            if Instant::now() >= until {
                return Err(RacpError::new("CLEANUP_FAILED"));
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        result
    }
    pub fn health(
        &mut self,
        work: &Path,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<(), RacpError> {
        self.verify(deadline, cancel)?;
        if !self.java_checked {
            let code = self.process(
                vec![
                    self.catalog.java.to_string_lossy().into_owned(),
                    "-version".into(),
                ],
                work,
                deadline,
                cancel,
            )?;
            let log = String::from_utf8_lossy(&self.log);
            let major = log
                .split("version \"")
                .nth(1)
                .and_then(|s| s.split(['.', '"']).next())
                .and_then(|s| s.parse::<u32>().ok());
            if code != 0 || major.is_none_or(|v| v < 21) {
                return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
            }
            self.java_checked = true;
        }
        Ok(())
    }
    pub fn call(
        &mut self,
        target: &Path,
        payload: &Value,
        opening: bool,
        deadline: Instant,
        cancel: &CancellationToken,
    ) -> Result<Value, RacpError> {
        validate_local_path(target)?;
        self.verify(deadline, cancel)?;
        let root = target
            .parent()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let directory = root.join("ghidra");
        let project = root.join("database");
        private_dir(&directory)?;
        private_dir(&project)?;
        for name in ["home", "settings", "cache", "temp", "scripts"] {
            private_dir(&directory.join(name))?;
        }
        let script = directory.join("scripts/RACPStaticBridge.java");
        let expected = include_str!("resources/RACPStaticBridge.java").as_bytes();
        if script.exists() {
            if read_bounded(&script, 65536, true)? != expected {
                return Err(RacpError::new("PLUGIN_VERSION_MISMATCH"));
            }
        } else {
            atomic_write(&script, expected, false)?;
        }
        let id = new_id("bridge");
        let request = directory.join(format!("{id}.json"));
        let response = directory.join(format!("{id}.out"));
        atomic_write(&request, &serde_json::to_vec(payload)?, false)?;
        let mutation = matches!(payload["action"].as_str(), Some("rename" | "comment"));
        let operation = (|| {
            let props = String::from_utf8(read_bounded(
                &self.catalog.installation.join("support/launch.properties"),
                65536,
                false,
            )?)
            .map_err(|_| RacpError::new("PLUGIN_PROTOCOL_ERROR"))?;
            let platform = if cfg!(windows) {
                "VMARGS_WINDOWS="
            } else {
                "VMARGS_LINUX="
            };
            let mut argv = vec![self.catalog.java.to_string_lossy().into_owned()];
            for line in props.lines() {
                if let Some(argument) = line
                    .strip_prefix("VMARGS=")
                    .or_else(|| line.strip_prefix(platform))
                {
                    if argument.contains('\0') {
                        return Err(RacpError::new("PLUGIN_PROTOCOL_ERROR"));
                    }
                    argv.push(argument.trim_end_matches('\r').into());
                }
            }
            argv.extend(
                [
                    "-Xmx512m",
                    "-XX:ParallelGCThreads=2",
                    "-XX:CICompilerCount=2",
                    "-Djava.awt.headless=true",
                    "-Dcpu.core.limit=2",
                ]
                .into_iter()
                .map(str::to_owned),
            );
            for (name, folder) in [
                ("user.home", "home"),
                ("application.settingsdir", "settings"),
                ("application.cachedir", "cache"),
                ("application.tempdir", "temp"),
                ("java.io.tmpdir", "temp"),
            ] {
                argv.push(format!("-D{name}={}", directory.join(folder).display()));
            }
            argv.extend([
                "-cp".into(),
                self.catalog
                    .installation
                    .join("Ghidra/Framework/Utility/lib/Utility.jar")
                    .to_string_lossy()
                    .into_owned(),
                "ghidra.Ghidra".into(),
                "ghidra.app.util.headless.AnalyzeHeadless".into(),
                project.to_string_lossy().into_owned(),
                "analysis".into(),
            ]);
            if opening {
                argv.extend([
                    "-import".into(),
                    target.to_string_lossy().into_owned(),
                    "-analysisTimeoutPerFile".into(),
                    deadline
                        .saturating_duration_since(Instant::now())
                        .as_secs()
                        .max(1)
                        .to_string(),
                ]);
            } else {
                argv.extend([
                    "-process".into(),
                    target
                        .file_name()
                        .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
                        .to_string_lossy()
                        .into_owned(),
                    "-noanalysis".into(),
                ]);
                if !mutation {
                    argv.push("-readOnly".into());
                }
            }
            argv.extend([
                "-max-cpu".into(),
                "2".into(),
                "-scriptPath".into(),
                directory.join("scripts").to_string_lossy().into_owned(),
                "-postScript".into(),
                "RACPStaticBridge.java".into(),
                request.to_string_lossy().into_owned(),
                response.to_string_lossy().into_owned(),
            ]);
            let code = self.process(argv, &directory, deadline, cancel)?;
            atomic_write(&directory.join("last-operation.log"), &self.log, true)?;
            if code != 0 || !response.is_file() {
                return Err(RacpError::new(if opening || mutation {
                    "EXECUTION_UNKNOWN"
                } else {
                    "GHIDRA_OPERATION_FAILED"
                }));
            }
            let value = plugin::decode(&read_bounded(&response, 512 * 1024, true)?, 512 * 1024)?;
            if value.get("error").is_some() {
                return Err(RacpError::new(
                    if opening || value["execution_state"] != "not_started" {
                        "EXECUTION_UNKNOWN"
                    } else {
                        "GHIDRA_OPERATION_FAILED"
                    },
                ));
            }
            if value.as_object().is_none_or(|o| o.len() != 1)
                || !value["result"].is_object()
                || mutation
                    && !self
                        .log
                        .windows(b"REPORT: Save succeeded for processed file:".len())
                        .any(|w| w == b"REPORT: Save succeeded for processed file:")
            {
                return Err(RacpError::new("EXECUTION_UNKNOWN"));
            }
            Ok(value["result"].clone())
        })();
        let _ = std::fs::remove_file(request);
        let _ = std::fs::remove_file(response);
        operation
    }
}
