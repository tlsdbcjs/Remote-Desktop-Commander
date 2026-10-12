use super::*;
use crate::providers::containment::{CommandSpec, OwnedProcess};
use image::{ImageReader, Limits};
use std::io::{BufRead, Cursor};

impl Filesystem {
    pub(super) fn preview_image(
        &self,
        workspace: &str,
        path: &Path,
        p: &Value,
        budget: &Budget,
    ) -> Result<Value> {
        let parent = self.guards.parent(workspace, path)?;
        let filename = name(path)?;
        let before = info(&parent, filename)?;
        if before.directory
            || before.size > p["max_source_bytes"].as_u64().unwrap_or(16 * 1024 * 1024)
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED").into());
        }
        precondition(&parent, filename, p, budget)?;
        let input = self
            .spool
            .join(format!("{}.preview-source", budget.operation));
        let output = self.spool.join(format!("{}.preview.png", budget.operation));
        let mut source = parent.open_read(filename)?;
        let mut target = racp_core::secure_create_file(&input)?;
        let mut block = [0u8; 65536];
        let mut length = 0u64;
        let mut hash = Sha256::new();
        let result = (|| -> std::result::Result<Value, RacpError> {
            loop {
                budget.check()?;
                let n = source.read(&mut block)?;
                if n == 0 {
                    break;
                }
                length = length
                    .checked_add(n as u64)
                    .filter(|v| *v <= 16 * 1024 * 1024)
                    .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
                target.write_all(&block[..n])?;
                hash.update(&block[..n]);
            }
            target.sync_all()?;
            drop(target);
            if info(&parent, filename)?.revision() != before.revision() || length != before.size {
                return Err(RacpError::new("PRECONDITION_FAILED"));
            }
            let cwd =
                racp_core::Workspaces::new(&self.spool, &[])?.directory("default", &self.spool)?;
            let exe = std::env::current_exe()?;
            let (mut child, mut stdin) = OwnedProcess::spawn_stdio(CommandSpec {
                argv: vec![
                    exe.to_string_lossy().into_owned(),
                    "image-preview-worker".into(),
                ],
                environment: std::collections::BTreeMap::new(),
                cwd,
            })?;
            #[cfg(windows)]
            child.limit_memory(256 * 1024 * 1024)?;
            let _protected = crate::identity::ProtectedProcess::register(child.pid())?;
            stdin.write_all(&serde_json::to_vec(&json!({"input":input,"output":output,"width":p["width"],"height":p["height"],"max_pixels":p["max_pixels"]}))?)?;
            stdin.write_all(b"\n")?;
            drop(stdin);
            let mut stdout = vec![];
            let mut stderr = vec![];
            let mut buffer = [0u8; 4096];
            let work = (|| loop {
                budget.check()?;
                for (pipe, data) in [
                    (&mut child.stdout, &mut stdout),
                    (&mut child.stderr, &mut stderr),
                ] {
                    for _ in 0..4 {
                        match pipe.read_available(&mut buffer)? {
                            Some(n) if n > 0 => {
                                if data.len() + n > 16384 {
                                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                                }
                                data.extend_from_slice(&buffer[..n]);
                            }
                            _ => break,
                        }
                    }
                }
                if let Some(code) = child.poll()? {
                    for _ in 0..4 {
                        if let Some(n) = child.stdout.read_available(&mut buffer)? {
                            if stdout.len() + n > 16384 {
                                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                            }
                            stdout.extend_from_slice(&buffer[..n]);
                        }
                    }
                    racp_contract::local_ipc::reject_duplicate_keys(&stdout)?;
                    let receipt: Value = serde_json::from_slice(&stdout)?;
                    if code != 0 || receipt["ok"] != true {
                        return Err(RacpError::new("IMAGE_DECODE_REJECTED"));
                    }
                    return Ok(receipt["result"].clone());
                }
                std::thread::sleep(Duration::from_millis(5));
            })();
            child.kill_tree()?;
            let deadline = Instant::now() + Duration::from_secs(5);
            while !child.tree_empty()? {
                if Instant::now() >= deadline {
                    return Err(RacpError::new("CLEANUP_UNKNOWN"));
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            let mut receipt = work?;
            budget.check()?;
            let file = racp_core::secure_read_file(&output)?;
            if file.metadata()?.len() > 16 * 1024 * 1024 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            receipt["source_sha256"] = json!(format!("{:x}", hash.finalize()));
            receipt["source_revision"] = json!(before.revision());
            receipt["spool_path"] = json!(output);
            receipt["artifact_media_type"] = json!("image/png");
            receipt["cleanup_status"] = json!("complete");
            Ok(receipt)
        })();
        let _ = std::fs::remove_file(&input);
        if result.is_err() {
            let _ = std::fs::remove_file(&output);
        }
        result.map_err(Into::into)
    }
}
pub fn run_worker() -> std::result::Result<(), RacpError> {
    let mut line = vec![];
    std::io::stdin()
        .lock()
        .take(16385)
        .read_until(b'\n', &mut line)?;
    if line.len() > 16384 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    racp_contract::local_ipc::reject_duplicate_keys(&line)?;
    #[derive(serde::Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Input {
        input: PathBuf,
        output: PathBuf,
        width: u32,
        height: u32,
        max_pixels: u64,
    }
    let p: Input = serde_json::from_slice(&line)?;
    if !(1..=2048).contains(&p.width)
        || !(1..=2048).contains(&p.height)
        || p.max_pixels == 0
        || p.max_pixels > 16_777_216
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let bytes = racp_core::read_bounded(&p.input, 16 * 1024 * 1024, false)?;
    let dimensions = ImageReader::new(Cursor::new(&bytes))
        .with_guessed_format()?
        .into_dimensions()
        .map_err(|_| RacpError::new("IMAGE_DECODE_REJECTED"))?;
    if dimensions.0 == 0
        || dimensions.1 == 0
        || u64::from(dimensions.0) * u64::from(dimensions.1) > p.max_pixels
    {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let mut reader = ImageReader::new(Cursor::new(&bytes)).with_guessed_format()?;
    let mut limits = Limits::default();
    limits.max_image_width = Some(dimensions.0);
    limits.max_image_height = Some(dimensions.1);
    limits.max_alloc = Some(128 * 1024 * 1024);
    reader.limits(limits);
    let image = reader
        .decode()
        .map_err(|_| RacpError::new("IMAGE_DECODE_REJECTED"))?;
    if (image.width(), image.height()) != dimensions {
        return Err(RacpError::new("IMAGE_DECODE_REJECTED"));
    }
    let thumbnail = image.thumbnail(p.width, p.height);
    let mut encoded = Cursor::new(vec![]);
    thumbnail
        .write_to(&mut encoded, image::ImageFormat::Png)
        .map_err(|_| RacpError::new("IMAGE_DECODE_REJECTED"))?;
    racp_core::atomic_write(&p.output, &encoded.into_inner(), false)?;
    println!(
        "{}",
        json!({"ok":true,"result":{"source_width":dimensions.0,"source_height":dimensions.1,"width":thumbnail.width(),"height":thumbnail.height(),"frame":"first","format":"png"}})
    );
    Ok(())
}
