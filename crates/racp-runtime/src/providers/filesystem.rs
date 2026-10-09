use super::{cursor::Cursor, encoding, Provider};
use futures_util::future::BoxFuture;
use racp_contract::{new_id, registry, RacpError};
use racp_core::{private_dir, AgentSettings, Directory, FileInfo, Workspaces};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;
use unicode_casefold::UnicodeCaseFold;
#[derive(Clone)]
pub struct Filesystem {
    pub guards: Workspaces,
    spool: PathBuf,
    lock: Arc<Mutex<()>>,
    cursor: Cursor,
    protected: Arc<Vec<PathBuf>>,
}
struct Budget {
    deadline: Instant,
    cancel: CancellationToken,
    operation: String,
}
impl Budget {
    fn check(&self) -> std::result::Result<(), RacpError> {
        if self.cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if Instant::now() >= self.deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        Ok(())
    }
}
struct Failure {
    error: RacpError,
    details: Value,
}
impl From<RacpError> for Failure {
    fn from(error: RacpError) -> Self {
        Self {
            error,
            details: json!({}),
        }
    }
}
impl From<std::io::Error> for Failure {
    fn from(_: std::io::Error) -> Self {
        RacpError::new("RESOURCE_EXHAUSTED").into()
    }
}
type Result<T> = std::result::Result<T, Failure>;
fn name(path: &Path) -> std::result::Result<&str, RacpError> {
    path.file_name()
        .and_then(|s| s.to_str())
        .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))
}
fn info(parent: &Directory, name: &str) -> std::result::Result<FileInfo, RacpError> {
    let info = parent
        .info(name)?
        .ok_or_else(|| RacpError::new("PATH_NOT_FOUND"))?;
    if info.link {
        return Err(RacpError::new("PATH_ACCESS_DENIED"));
    }
    Ok(info)
}
fn hash_file(
    parent: &Directory,
    name: &str,
    budget: &Budget,
) -> std::result::Result<String, RacpError> {
    let mut file = parent.open_read(name)?;
    let mut hash = Sha256::new();
    let mut block = [0u8; 65536];
    loop {
        budget.check()?;
        let n = file.read(&mut block)?;
        if n == 0 {
            break;
        }
        hash.update(&block[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn precondition(
    parent: &Directory,
    name: &str,
    payload: &Value,
    budget: &Budget,
) -> std::result::Result<(), RacpError> {
    let current = parent.info(name)?;
    if current.as_ref().is_some_and(|i| i.link) {
        return Err(RacpError::new("PATH_ACCESS_DENIED"));
    }
    if let Some(expected) = payload["expected_revision"].as_str() {
        if current.as_ref().is_none_or(|i| i.revision() != expected) {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
    }
    if let Some(expected) = payload["expected_sha256"].as_str() {
        if current.is_none() || hash_file(parent, name, budget)? != expected {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
    }
    Ok(())
}
impl Filesystem {
    pub fn new(settings: &AgentSettings) -> std::result::Result<Self, RacpError> {
        let guards = Workspaces::new(&settings.workspace, &settings.allowed_workspaces)?;
        let spool = settings.data_dir.join("spool");
        private_dir(&spool)?;
        #[cfg(unix)]
        let protected = [
            "/proc", "/sys", "/dev", "/etc", "/usr", "/bin", "/sbin", "/boot",
        ]
        .iter()
        .map(PathBuf::from)
        .collect();
        #[cfg(windows)]
        let protected = vec![
            std::env::var_os("SYSTEMROOT")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("C:\\Windows")),
            std::env::var_os("PROGRAMFILES")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("C:\\Program Files")),
        ];
        Ok(Self {
            guards,
            spool,
            lock: Arc::new(Mutex::new(())),
            cursor: Cursor::default(),
            protected: Arc::new(protected),
        })
    }
    fn protected(&self, path: &Path) -> bool {
        self.protected
            .iter()
            .any(|root| racp_core::path_within(path, root))
    }
    fn read(
        &self,
        id: &str,
        target: &Path,
        p: &Value,
        budget: &Budget,
        operation: &str,
    ) -> Result<Value> {
        let parent = self.guards.parent(id, target)?;
        let metadata = info(&parent, name(target)?)?.metadata(target);
        let mut source = parent.open_read(name(target)?)?;
        let limit = p["max_bytes"].as_u64().unwrap_or(65536);
        let mut initial = vec![];
        std::io::Read::by_ref(&mut source)
            .take(limit + 1)
            .read_to_end(&mut initial)?;
        budget.check()?;
        if p["binary"] != true && initial.len() as u64 <= limit {
            let mut result = metadata;
            result["text"] = json!(encoding::decode(
                &initial,
                p["encoding"].as_str().unwrap_or("utf-8")
            )?);
            result["encoding"] = p["encoding"].clone();
            result["bom"] = json!(initial.starts_with(b"\xef\xbb\xbf"));
            result["artifact_id"] = Value::Null;
            return Ok(result);
        }
        let spool = self.spool.join(format!("{operation}.binary"));
        let mut output = racp_core::secure_append_file(&spool)?;
        if output.metadata()?.len() != 0 {
            return Err(RacpError::new("CONFLICT").into());
        }
        let copied = (|| {
            let mut total = initial.len() as u64;
            output.write_all(&initial)?;
            let mut block = [0; 65536];
            loop {
                budget.check()?;
                let n = source.read(&mut block)?;
                if n == 0 {
                    break;
                }
                total += n as u64;
                if total > 1024 * 1024 * 1024 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED").into());
                }
                output.write_all(&block[..n])?;
            }
            output.sync_all()?;
            Ok::<(), Failure>(())
        })();
        if let Err(error) = copied {
            drop(output);
            let _ = std::fs::remove_file(&spool);
            return Err(error);
        }
        let mut result = metadata;
        result["text"] = Value::Null;
        result["artifact_id"] = Value::Null;
        result["artifact_media_type"] = json!("application/octet-stream");
        result["spool_path"] = json!(spool);
        Ok(result)
    }
    fn write(&self, id: &str, target: &Path, p: &Value, budget: &Budget) -> Result<Value> {
        let parent = self.guards.parent(id, target)?;
        let filename = name(target)?;
        let original = parent.info(filename)?;
        if original.as_ref().is_some_and(|i| i.link || i.directory) {
            return Err(RacpError::new("PATH_ACCESS_DENIED").into());
        }
        precondition(&parent, filename, p, budget)?;
        let mode = p["mode"].as_str().unwrap_or("create");
        if mode == "create" && original.is_some() {
            return Err(RacpError::new("CONFLICT").into());
        }
        if mode != "create" && original.is_none() {
            return Err(RacpError::new("PATH_NOT_FOUND").into());
        }
        let mut text = p["content"].as_str().unwrap_or("").to_string();
        let binary = p["artifact_id"].is_string();
        let mut prefix = vec![];
        let mut newline = p["newline"].as_str().unwrap_or("preserve");
        if !binary && original.is_some() {
            let mut sample = vec![];
            parent
                .open_read(filename)?
                .take(65536)
                .read_to_end(&mut sample)?;
            if mode == "replace"
                && sample.starts_with(b"\xef\xbb\xbf")
                && p["encoding"] == "utf-8"
                && !text.starts_with('\u{feff}')
            {
                prefix = b"\xef\xbb\xbf".to_vec();
            }
            if newline == "preserve" {
                newline = if sample.windows(2).any(|w| w == b"\r\n") {
                    "crlf"
                } else {
                    "lf"
                };
            }
        }
        if newline == "lf" || newline == "crlf" {
            text = text.replace("\r\n", "\n");
            if newline == "crlf" {
                text = text.replace('\n', "\r\n");
            }
        }
        let raw = if binary {
            vec![]
        } else {
            let mut bytes = prefix;
            bytes.extend(encoding::encode(
                &text,
                p["encoding"].as_str().unwrap_or("utf-8"),
            )?);
            if bytes.len() > 65536 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED").into());
            }
            bytes
        };
        let write_blocks = |output: &mut std::fs::File| -> Result<u64> {
            if !binary {
                budget.check()?;
                output.write_all(&raw)?;
                return Ok(raw.len() as u64);
            }
            let input = p["_artifact_path"]
                .as_str()
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let input = Path::new(input);
            if input.parent() != Some(self.spool.as_path()) {
                return Err(RacpError::new("PERMISSION_DENIED").into());
            }
            let mut source = racp_core::secure_read_file(input)?;
            let mut count = 0u64;
            let mut hash = Sha256::new();
            let mut block = [0; 65536];
            loop {
                budget.check()?;
                let n = source.read(&mut block)?;
                if n == 0 {
                    break;
                }
                count += n as u64;
                if count > p["_artifact_size"].as_u64().unwrap_or(0) {
                    return Err(RacpError::new("CHECKSUM_MISMATCH").into());
                }
                hash.update(&block[..n]);
                output.write_all(&block[..n])?;
            }
            if p["_artifact_size"].as_u64() != Some(count)
                || p["_artifact_sha256"] != format!("{:x}", hash.finalize())
            {
                return Err(RacpError::new("CHECKSUM_MISMATCH").into());
            }
            Ok(count)
        };
        if mode == "append" {
            let expected = p["expected_offset"]
                .as_str()
                .and_then(|s| s.parse::<u64>().ok())
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let mut output = parent.append(filename)?;
            if output.metadata()?.len() != expected {
                return Err(RacpError::new("PRECONDITION_FAILED").into());
            }
            let written = match write_blocks(&mut output).and_then(|n| {
                output.sync_all()?;
                Ok(n)
            }) {
                Ok(n) => n,
                Err(mut e) => {
                    let accepted = output.metadata()?.len().saturating_sub(expected);
                    e.details = json!({"accepted_bytes":accepted,"atomic":false,"cleanup_status":if accepted>0{"partial"}else{"complete"}});
                    return Err(e);
                }
            };
            let mut result = info(&parent, filename)?.metadata(target);
            result["written_bytes"] = json!(written);
            result["atomic"] = json!(false);
            result["external_writer_atomic_cas"] = json!(false);
            return Ok(result);
        }
        let temporary = format!(".racp-{}.tmp", new_id("write"));
        let result = (|| {
            let mut output = parent.create(&temporary)?;
            let written = write_blocks(&mut output)?;
            if let Some(original) = &original {
                parent.preserve_permissions(&output, original.mode)?;
            }
            output.sync_all()?;
            drop(output);
            if original.is_some() {
                parent.preserve_acl(filename, &temporary)?;
            }
            precondition(&parent, filename, p, budget)?;
            budget.check()?;
            parent.publish(&temporary, filename, mode != "create")?;
            parent.sync()?;
            let mut result = info(&parent, filename)?.metadata(target);
            result["written_bytes"] = json!(written);
            result["atomic"] = json!(true);
            result["external_writer_atomic_cas"] = json!(false);
            Ok(result)
        })();
        let _ = parent.unlink(&temporary, false);
        result
    }
    fn mkdir(&self, id: &str, target: &Path, p: &Value, budget: &Budget) -> Result<Value> {
        if self.guards.is_root(target) {
            return Err(RacpError::new("PATH_ACCESS_DENIED").into());
        }
        let paths = if p["parents"] == true {
            let root = self.guards.root(id)?;
            let mut paths = vec![];
            let mut current = root.to_path_buf();
            for part in target
                .strip_prefix(root)
                .map_err(|_| RacpError::new("PATH_ACCESS_DENIED"))?
                .components()
            {
                current.push(part);
                paths.push(current.clone());
            }
            paths
        } else {
            vec![target.to_path_buf()]
        };
        for path in paths {
            budget.check()?;
            let parent = self.guards.parent(id, &path)?;
            let name = name(&path)?;
            if let Some(info) = parent.info(name)? {
                if info.link || !info.directory || (path == target && p["exist_ok"] != true) {
                    return Err(RacpError::new("CONFLICT").into());
                }
            } else {
                parent.mkdir(name)?;
                parent.sync()?;
            }
        }
        Ok(json!({"path":target,"created":true}))
    }
    fn walk(
        &self,
        id: &str,
        target: &Path,
        budget: &Budget,
        depth: u64,
        complete: bool,
    ) -> Result<Vec<PathBuf>> {
        let mut pending = vec![(target.to_path_buf(), 0)];
        let mut paths = vec![];
        while let Some((directory, level)) = pending.pop() {
            budget.check()?;
            let parent = self.guards.directory(id, &directory)?;
            for name in parent.names()? {
                let info = info(&parent, &name)?;
                let path = directory.join(name);
                paths.push(path.clone());
                if paths.len() > 10000 {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED").into());
                }
                if info.directory {
                    if level < depth {
                        pending.push((path, level + 1));
                    } else if complete {
                        return Err(RacpError::new("RESOURCE_EXHAUSTED").into());
                    }
                }
            }
        }
        Ok(paths)
    }
    fn copy_file(
        &self,
        id: &str,
        source: &Path,
        destination_id: &str,
        destination: &Path,
        p: &Value,
        budget: &Budget,
    ) -> Result<Value> {
        let source_parent = self.guards.parent(id, source)?;
        let parent = self.guards.parent(destination_id, destination)?;
        let source_name = name(source)?;
        let destination_name = name(destination)?;
        source_parent.require_file(source_name)?;
        if let Some(original) = parent.info(destination_name)? {
            if original.link || original.directory {
                return Err(RacpError::new("PATH_ACCESS_DENIED").into());
            }
            if p["overwrite"] != true {
                return Err(RacpError::new("CONFLICT").into());
            }
        }
        precondition(&source_parent, source_name, p, budget)?;
        let temporary = format!(".racp-{}.tmp", new_id("copy"));
        let result = (|| {
            let mut source_file = source_parent.open_read(source_name)?;
            let mut output = parent.create(&temporary)?;
            let mut size = 0u64;
            let mut hash = Sha256::new();
            let mut block = [0; 65536];
            loop {
                budget.check()?;
                let n = source_file.read(&mut block)?;
                if n == 0 {
                    break;
                }
                size += n as u64;
                hash.update(&block[..n]);
                output.write_all(&block[..n])?;
            }
            output.sync_all()?;
            drop(output);
            let hash = format!("{:x}", hash.finalize());
            if p["expected_sha256"].is_string() && p["expected_sha256"] != hash {
                return Err(RacpError::new("PRECONDITION_FAILED").into());
            }
            budget.check()?;
            parent.publish(&temporary, destination_name, p["overwrite"] == true)?;
            parent.sync()?;
            Ok(json!({"source":source,"destination":destination,"copied_bytes":size,"sha256":hash}))
        })();
        let _ = parent.unlink(&temporary, false);
        result
    }
    fn partial(
        &self,
        operation: &str,
        completed: Vec<PathBuf>,
        failed: &Path,
        error: Failure,
    ) -> Failure {
        let report = self.spool.join(format!("{operation}.report.json"));
        if racp_core::atomic_write(
            &report,
            &json!({"completed":completed,"failed":[failed]})
                .to_string()
                .into_bytes(),
            true,
        )
        .is_err()
        {
            return Failure {
                error: error.error,
                details: json!({"cleanup_status":"partial","completed_count":completed.len()}),
            };
        }
        Failure {
            error: error.error,
            details: json!({"report_spool_path":report,"cleanup_status":"partial"}),
        }
    }
    fn copy_tree(
        &self,
        id: &str,
        source: &Path,
        destination_id: &str,
        destination: &Path,
        p: &Value,
        budget: &Budget,
    ) -> Result<Value> {
        let operation = budget.operation.as_str();
        if p["recursive"] != true {
            return Err(RacpError::new("INVALID_ARGUMENT").into());
        }
        if source.starts_with(destination) || destination.starts_with(source) {
            return Err(RacpError::new("INVALID_ARGUMENT").into());
        }
        let mut paths = self.walk(id, source, budget, 20, true)?;
        self.mkdir(
            destination_id,
            destination,
            &json!({"parents":false,"exist_ok":p["overwrite"]}),
            budget,
        )?;
        let mut completed = vec![destination.to_path_buf()];
        let mut manifest = serde_json::Map::new();
        paths.sort_by_key(|p| p.components().count());
        for path in paths {
            let target = destination.join(
                path.strip_prefix(source)
                    .map_err(|_| RacpError::new("PATH_ACCESS_DENIED"))?,
            );
            let copied = (|| {
                let parent = self.guards.parent(id, &path)?;
                let info = info(&parent, name(&path)?)?;
                if info.directory {
                    self.mkdir(
                        destination_id,
                        &target,
                        &json!({"parents":false,"exist_ok":p["overwrite"]}),
                        budget,
                    )?;
                    Ok(Value::Null)
                } else {
                    Ok(
                        self.copy_file(id, &path, destination_id, &target, p, budget)?["sha256"]
                            .clone(),
                    )
                }
            })();
            match copied {
                Ok(hash) => {
                    manifest.insert(path.to_string_lossy().into_owned(), hash);
                    completed.push(target);
                }
                Err(error) => return Err(self.partial(operation, completed, &target, error)),
            }
        }
        let mut result =
            json!({"source":source,"destination":destination,"completed_count":completed.len()});
        if p["copy_and_delete"] == true {
            result["source_manifest"] = Value::Object(manifest);
        }
        Ok(result)
    }
    fn run(&self, request: &Value, budget: &Budget) -> Result<Value> {
        budget.check()?;
        let op = request["operation"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let payload = &request["payload"];
        let id = request["context"]["workspace_id"]
            .as_str()
            .unwrap_or("default");
        let operation = request["operation_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let target = self.guards.path(
            id,
            payload["path"]
                .as_str()
                .or(payload["source"].as_str())
                .unwrap_or("."),
        )?;
        if ["filesystem.delete", "filesystem.move"].contains(&op)
            && self.guards.protects_root(&target)
        {
            return Err(RacpError::new("PATH_ACCESS_DENIED").into());
        }
        if [
            "filesystem.delete",
            "filesystem.move",
            "filesystem.mkdir",
            "filesystem.write",
        ]
        .contains(&op)
            && self.protected(&target)
        {
            return Err(RacpError::new("PATH_ACCESS_DENIED").into());
        }
        match op {
            "filesystem.read" => return self.read(id, &target, payload, budget, operation),
            "filesystem.stat" => {
                let info = if self.guards.is_root(&target) {
                    self.guards.directory(id, &target)?.own_info()?
                } else {
                    let parent = self.guards.parent(id, &target)?;
                    parent
                        .info(name(&target)?)?
                        .ok_or_else(|| RacpError::new("PATH_NOT_FOUND"))?
                };
                return Ok(info.metadata(&target));
            }
            "filesystem.hash" => {
                let parent = self.guards.parent(id, &target)?;
                return Ok(
                    json!({"path":target,"algorithm":"sha256","sha256":hash_file(&parent,name(&target)?,budget)?}),
                );
            }
            "filesystem.list" => {
                let parent = self.guards.directory(id, &target)?;
                let revision = parent.own_info()?.revision();
                let limit = payload["limit"].as_u64().unwrap_or(100) as usize;
                let scope = format!("{}:{limit}", target.to_string_lossy());
                let offset = self
                    .cursor
                    .decode(payload["cursor"].as_str(), &scope, &revision)?;
                let names = parent.names()?;
                let mut items = vec![];
                for filename in names.iter().skip(offset).take(limit) {
                    budget.check()?;
                    if let Some(info) = parent.info(filename)? {
                        items.push(info.metadata(&target.join(filename)));
                    }
                }
                let end = offset.saturating_add(limit);
                return Ok(
                    json!({"items":items,"next_cursor":if end<names.len(){Some(self.cursor.encode(end,&scope,&revision)?)}else{None},"consistency":"best_effort"}),
                );
            }
            "filesystem.search" => {
                let paths = self.walk(
                    id,
                    &target,
                    budget,
                    payload["max_depth"].as_u64().unwrap_or(20),
                    false,
                )?;
                let pattern = payload["pattern"]
                    .as_str()
                    .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
                let sensitive = payload["case_sensitive"] == true;
                let needle = if sensitive {
                    pattern.into()
                } else {
                    pattern.case_fold().collect::<String>()
                };
                let matched: Vec<PathBuf> = paths
                    .into_iter()
                    .filter(|path| {
                        let name = path.file_name().unwrap_or_default().to_string_lossy();
                        if sensitive {
                            name.contains(&needle)
                        } else {
                            name.case_fold().collect::<String>().contains(&needle)
                        }
                    })
                    .collect();
                let max = payload["max_results"].as_u64().unwrap_or(1000) as usize;
                return Ok(
                    json!({"items":&matched[..matched.len().min(max)],"truncated":matched.len()>max,"max_depth":payload["max_depth"],"max_results":max,"mode":"literal"}),
                );
            }
            _ => {}
        }
        let _lock = self
            .lock
            .lock()
            .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
        budget.check()?;
        if op == "filesystem.write" {
            return self.write(id, &target, payload, budget);
        }
        if op == "filesystem.mkdir" {
            return self.mkdir(id, &target, payload, budget);
        }
        if op == "filesystem.copy" || op == "filesystem.move" {
            let destination_id = payload["destination_workspace_id"].as_str().unwrap_or(id);
            let destination = self.guards.path(
                destination_id,
                payload["destination"]
                    .as_str()
                    .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?,
            )?;
            if self.protected(&destination) || self.guards.is_root(&destination) {
                return Err(RacpError::new("PATH_ACCESS_DENIED").into());
            }
            let source_info = if self.guards.is_root(&target) {
                self.guards.directory(id, &target)?.own_info()?
            } else {
                let parent = self.guards.parent(id, &target)?;
                info(&parent, name(&target)?)?
            };
            if op == "filesystem.copy" {
                return if source_info.directory {
                    self.copy_tree(id, &target, destination_id, &destination, payload, budget)
                } else {
                    self.copy_file(id, &target, destination_id, &destination, payload, budget)
                };
            }
            let source = self.guards.parent(id, &target)?;
            let dest = self.guards.parent(destination_id, &destination)?;
            let source_name = name(&target)?;
            let destination_name = name(&destination)?;
            precondition(&source, source_name, payload, budget)?;
            if let Some(existing) = dest.info(destination_name)? {
                if existing.link {
                    return Err(RacpError::new("PATH_ACCESS_DENIED").into());
                }
                if payload["overwrite"] != true {
                    return Err(RacpError::new("CONFLICT").into());
                }
            }
            budget.check()?;
            match source.move_to(
                source_name,
                &dest,
                destination_name,
                payload["overwrite"] == true,
            ) {
                Ok(()) => {
                    return Ok(json!({"source":target,"destination":destination,"atomic":true}))
                }
                Err(error) if error.code.0 == "CROSS_VOLUME" => {
                    if payload["copy_and_delete"] != true || request["execution_mode"] != "job" {
                        return Err(RacpError::new("CONFLICT").into());
                    }
                    if source_info.directory {
                        let copied = self.copy_tree(
                            id,
                            &target,
                            destination_id,
                            &destination,
                            payload,
                            budget,
                        )?;
                        let manifest = copied["source_manifest"]
                            .as_object()
                            .ok_or_else(|| RacpError::new("EXECUTION_UNKNOWN"))?;
                        let mut remaining = self.walk(id, &target, budget, 20, true)?;
                        if remaining.len() != manifest.len()
                            || remaining
                                .iter()
                                .any(|path| !manifest.contains_key(path.to_string_lossy().as_ref()))
                        {
                            return Err(self.partial(
                                operation,
                                vec![destination],
                                &target,
                                RacpError::new("PRECONDITION_FAILED").into(),
                            ));
                        }
                        remaining.sort_by_key(|path| std::cmp::Reverse(path.components().count()));
                        for path in remaining {
                            let removed = (|| {
                                budget.check()?;
                                let parent = self.guards.parent(id, &path)?;
                                let filename = name(&path)?;
                                let info = info(&parent, filename)?;
                                let hash = &manifest[path.to_string_lossy().as_ref()];
                                if hash.is_string() {
                                    precondition(
                                        &parent,
                                        filename,
                                        &json!({"expected_sha256":hash}),
                                        budget,
                                    )?;
                                }
                                parent.unlink(filename, info.directory)?;
                                Ok::<(), Failure>(())
                            })();
                            if let Err(error) = removed {
                                return Err(self.partial(
                                    operation,
                                    vec![destination],
                                    &path,
                                    error,
                                ));
                            }
                        }
                        source.unlink(source_name, true)?;
                    } else {
                        let copied = self.copy_file(
                            id,
                            &target,
                            destination_id,
                            &destination,
                            payload,
                            budget,
                        )?;
                        let removed = (|| {
                            precondition(
                                &source,
                                source_name,
                                &json!({"expected_sha256":copied["sha256"]}),
                                budget,
                            )?;
                            budget.check()?;
                            source.unlink(source_name, false)?;
                            Ok::<(), Failure>(())
                        })();
                        if let Err(error) = removed {
                            return Err(self.partial(operation, vec![destination], &target, error));
                        }
                    }
                    source.sync()?;
                    return Ok(
                        json!({"source":target,"destination":destination,"atomic":false,"external_writer_atomic_cas":false}),
                    );
                }
                Err(error) => return Err(error.into()),
            }
        }
        if op == "filesystem.delete" {
            let parent = self.guards.parent(id, &target)?;
            let filename = name(&target)?;
            let metadata = info(&parent, filename)?;
            precondition(&parent, filename, payload, budget)?;
            if metadata.directory && payload["recursive"] == true {
                if !payload["expected_revision"].is_string() {
                    return Err(RacpError::new("INVALID_ARGUMENT").into());
                }
                let mut paths = self.walk(id, &target, budget, 20, true)?;
                paths.sort_by_key(|path| std::cmp::Reverse(path.components().count()));
                let mut completed = vec![];
                for path in paths {
                    let removed = (|| {
                        budget.check()?;
                        let parent = self.guards.parent(id, &path)?;
                        let filename = name(&path)?;
                        let info = info(&parent, filename)?;
                        parent.unlink(filename, info.directory)?;
                        parent.sync()?;
                        Ok::<(), Failure>(())
                    })();
                    if let Err(error) = removed {
                        return Err(self.partial(operation, completed, &path, error));
                    }
                    completed.push(path);
                }
            }
            budget.check()?;
            parent.unlink(filename, metadata.directory)?;
            parent.sync()?;
            return Ok(json!({"path":target,"deleted":true}));
        }
        Err(RacpError::new("OPERATION_NOT_SUPPORTED").into())
    }
}
impl Provider for Filesystem {
    fn capabilities(&self) -> Vec<Value> {
        vec![
            json!({"name":"filesystem","version":"1.0.0","operations":registry().as_object().unwrap().keys().filter(|name|name.starts_with("filesystem.")).collect::<Vec<_>>(),"installed":true,"supported":true,"enabled":true,"healthy":true,"unavailable_reason":null,"attributes":{"workspace":self.guards.root("default").ok(),"workspaces":self.guards.inventory(),"link_policy":"no_follow","external_writer_atomic_cas":false}}),
        ]
    }
    fn execute(
        &self,
        request: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, std::result::Result<Value, RacpError>> {
        let files = self.clone();
        let deadline = Instant::now()
            + Duration::from_millis(request["remaining_timeout_ms"].as_u64().unwrap_or(30000));
        Box::pin(async move {
            let result = tokio::task::spawn_blocking(move || {
                files.run(
                    &request,
                    &Budget {
                        deadline,
                        cancel,
                        operation: request["operation_id"]
                            .as_str()
                            .unwrap_or("op_invalid")
                            .into(),
                    },
                )
            })
            .await
            .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
            Ok(match result {
                Ok(result) => json!({"state":"SUCCEEDED","result":result,"error":null}),
                Err(error) => {
                    let state = match error.error.code.0 {
                        "CANCELLED" => "CANCELLED",
                        "TIMEOUT" => "TIMED_OUT",
                        "EXECUTION_UNKNOWN" => "UNKNOWN",
                        _ => "FAILED",
                    };
                    let partial = error.details["cleanup_status"] == "partial";
                    json!({"state":state,"result":null,"error":{"code":error.error.code,"message":"filesystem operation interrupted or rejected","layer":"provider","retryable":false,"execution_state":if partial{"completed"}else{"not_started"},"details":error.details}})
                }
            })
        })
    }
}
