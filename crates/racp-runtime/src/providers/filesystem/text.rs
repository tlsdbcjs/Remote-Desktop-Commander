//! Bounded literal observations and exact, per-file compare-and-replace edits.
use super::*;
use std::collections::{BTreeMap, BTreeSet};
impl Filesystem {
    fn text_read(
        &self,
        id: &str,
        path: &Path,
        limit: u64,
        budget: &Budget,
    ) -> Result<(Vec<u8>, FileInfo)> {
        let parent = self.guards.parent(id, path)?;
        let filename = name(path)?;
        parent.require_file(filename)?;
        let observed = info(&parent, filename)?;
        if observed.size > limit {
            return Err(RacpError::new("RESOURCE_EXHAUSTED").into());
        }
        let mut input = parent.open_read(filename)?;
        let initial = FileInfo::from_file(&input)?;
        if initial.revision() != observed.revision() {
            return Err(RacpError::new("PRECONDITION_FAILED").into());
        }
        let mut raw = vec![];
        let mut block = [0u8; 65536];
        loop {
            budget.check()?;
            let n = input.read(&mut block)?;
            if n == 0 {
                break;
            }
            if raw.len() as u64 + n as u64 > limit {
                return Err(RacpError::new("RESOURCE_EXHAUSTED").into());
            }
            raw.extend_from_slice(&block[..n]);
        }
        if raw.len() as u64 != initial.size
            || FileInfo::from_file(&input)?.revision() != initial.revision()
        {
            return Err(RacpError::new("PRECONDITION_FAILED").into());
        }
        Ok((raw, initial))
    }
    pub(super) fn search_content(
        &self,
        id: &str,
        path: &Path,
        p: &Value,
        budget: &Budget,
    ) -> Result<Value> {
        let mut pending = vec![(path.to_path_buf(), 0u64)];
        let mut items = vec![];
        let mut limits = BTreeSet::new();
        let mut skipped = BTreeMap::<String, u64>::new();
        let mut entries = 0;
        let mut files = 0;
        let mut scanned = 0u64;
        let pattern = p["pattern"].as_str().unwrap();
        let sensitive = p["case_sensitive"] == true;
        let needle = if sensitive {
            pattern.into()
        } else {
            pattern.case_fold().collect::<String>()
        };
        let mut skip = |reason: &str| {
            *skipped.entry(reason.into()).or_default() += 1;
        };
        while let Some((target, depth)) = pending.pop() {
            budget.check()?;
            let observed = if self.guards.is_root(&target) {
                self.guards.directory(id, &target)?.own_info()?
            } else {
                let parent = self.guards.parent(id, &target)?;
                match parent.info(name(&target)?)? {
                    Some(info) => info,
                    None => {
                        skip("unavailable");
                        continue;
                    }
                }
            };
            if observed.link {
                skip("link_or_reparse");
                continue;
            }
            if observed.directory {
                let directory = self.guards.directory(id, &target)?;
                for filename in directory.names()? {
                    budget.check()?;
                    entries += 1;
                    if entries > 10000 {
                        limits.insert("entry_budget");
                        break;
                    }
                    let Some(info) = directory.info(&filename)? else {
                        skip("unavailable");
                        continue;
                    };
                    if info.link {
                        skip("link_or_reparse");
                        continue;
                    }
                    if info.directory && depth >= p["max_depth"].as_u64().unwrap_or(10) {
                        limits.insert("max_depth");
                        continue;
                    }
                    pending.push((
                        target.join(filename),
                        if info.directory { depth + 1 } else { depth },
                    ));
                }
                if entries > 10000 {
                    break;
                }
                continue;
            }
            if files >= p["max_files"].as_u64().unwrap_or(1000) {
                limits.insert("file_budget");
                break;
            }
            files += 1;
            if observed.size > p["max_file_bytes"].as_u64().unwrap_or(65536) {
                skip("oversized");
                continue;
            }
            if observed.size
                > p["max_scan_bytes"]
                    .as_u64()
                    .unwrap_or(8388608)
                    .saturating_sub(scanned)
            {
                limits.insert("scan_byte_budget");
                break;
            }
            scanned += observed.size;
            let (raw, initial) = match self.text_read(id, &target, observed.size, budget) {
                Ok(v) => v,
                Err(e) => {
                    if matches!(
                        e.error.code.0,
                        "CANCELLED" | "TIMEOUT" | "PERMISSION_DENIED"
                    ) {
                        return Err(e);
                    }
                    skip(e.error.code.0);
                    continue;
                }
            };
            let text = match encoding::decode(&raw, p["encoding"].as_str().unwrap_or("utf-8")) {
                Ok(v) => v,
                Err(_) => {
                    skip("encoding");
                    continue;
                }
            };
            if text.contains('\0') {
                skip("binary");
                continue;
            }
            for (line_number, line) in text.lines().enumerate() {
                budget.check()?;
                let haystack = if sensitive {
                    line.into()
                } else {
                    line.case_fold().collect::<String>()
                };
                let Some(byte_column) = haystack.find(&needle) else {
                    continue;
                };
                if items.len() >= p["max_results"].as_u64().unwrap_or(100) as usize {
                    limits.insert("result_budget");
                    break;
                }
                let column = if sensitive {
                    line[..byte_column].chars().count()
                } else {
                    let mut folded = 0;
                    let mut source = 0;
                    for c in line.chars() {
                        let width = c.case_fold().collect::<String>().len();
                        if folded + width > byte_column {
                            break;
                        }
                        folded += width;
                        source += 1;
                    }
                    source
                };
                let start = column.saturating_sub(128);
                let preview: String = line.chars().skip(start).take(256).collect();
                items.push(json!({"path":target,"line":line_number+1,"column":column+1,"preview":preview,"preview_start_column":start+1,"preview_truncated":start>0||line.chars().count()>start+256,"revision":initial.revision()}));
            }
            if limits.contains("result_budget") {
                break;
            }
        }
        Ok(
            json!({"items":items,"truncated":!limits.is_empty(),"limits_reached":limits,"files_examined":files,"scan_bytes_reserved":scanned,"skipped":skipped,"mode":"literal_line","column_units":"unicode_codepoints","encoding":p["encoding"],"consistency":"per_file_observation"}),
        )
    }
    pub(super) fn patch_batch(&self, id: &str, p: &Value, budget: &Budget) -> Result<Value> {
        let mut seen = BTreeSet::new();
        let mut prepared = vec![];
        let mut diff_remaining = 8192;
        for file in p["files"].as_array().unwrap() {
            budget.check()?;
            let target = self.guards.path(id, file["path"].as_str().unwrap())?;
            let key = if cfg!(windows) {
                target.to_string_lossy().to_lowercase()
            } else {
                target.to_string_lossy().into_owned()
            };
            if !seen.insert(key) {
                return Err(RacpError::new("INVALID_ARGUMENT").into());
            }
            if self.protected(&target) || self.guards.protects_root(&target) {
                return Err(RacpError::new("PATH_ACCESS_DENIED").into());
            }
            let (raw, initial) = self.text_read(id, &target, 65536, budget)?;
            let hash = racp_contract::digest(&raw);
            if file["expected_sha256"] != hash
                || file["expected_revision"]
                    .as_str()
                    .is_some_and(|v| v != initial.revision())
            {
                return Err(RacpError::new("PRECONDITION_FAILED").into());
            }
            let encoding = file["encoding"].as_str().unwrap_or("utf-8");
            let bom = raw.starts_with(b"\xef\xbb\xbf") && matches!(encoding, "utf-8" | "utf-8-sig");
            let original = encoding::decode(&raw, if bom { "utf-8-sig" } else { encoding })?;
            let mut edited = original.clone();
            for edit in file["edits"].as_array().unwrap() {
                budget.check()?;
                let old = edit["old_text"].as_str().unwrap();
                let count = edit["expected_count"].as_u64().unwrap_or(1) as usize;
                if edited.matches(old).count() != count {
                    return Err(RacpError::new("PRECONDITION_FAILED").into());
                }
                edited = edited.replacen(old, edit["new_text"].as_str().unwrap(), count);
            }
            let encoding = if encoding == "utf-8-sig" {
                "utf-8"
            } else {
                encoding
            };
            let content = if bom {
                format!("\u{feff}{edited}")
            } else {
                edited.clone()
            };
            let new_raw = encoding::encode(&content, encoding)?;
            if new_raw.len() > 65536 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED").into());
            }
            let full = similar::TextDiff::from_lines(&original, &edited)
                .unified_diff()
                .context_radius(3)
                .header(&target.to_string_lossy(), &target.to_string_lossy())
                .to_string();
            let mut end = full.len().min(diff_remaining);
            while !full.is_char_boundary(end) {
                end -= 1;
            }
            let preview = &full[..end];
            diff_remaining -= end;
            let summary = json!({"path":target,"original_sha256":hash,"sha256":racp_contract::digest(&new_raw),"diff":preview,"diff_truncated":end<full.len()});
            let write = json!({"mode":"replace","content":content,"encoding":encoding,"newline":"verbatim","expected_sha256":hash,"expected_revision":initial.revision()});
            prepared.push((target, write, summary));
        }
        if p["dry_run"] == true {
            return Ok(
                json!({"files":prepared.iter().map(|v|&v.2).collect::<Vec<_>>(),"dry_run":true,"atomic_per_file":true,"atomic_batch":false,"applied":false}),
            );
        }
        let mut completed: Vec<Value> = vec![];
        for (target, write, mut summary) in prepared {
            let result = (|| {
                budget.check()?;
                self.write(id, &target, &write, budget)
            })();
            match result {
                Ok(result) => {
                    summary["revision"] = result["revision"].clone();
                    completed.push(summary);
                }
                Err(mut e) => {
                    if !completed.is_empty() {
                        e.details = json!({"completed":completed,"failed":target,"atomic_batch":false,"cleanup_status":"partial"});
                    }
                    return Err(e);
                }
            }
        }
        Ok(
            json!({"files":completed,"dry_run":false,"applied":true,"atomic_per_file":true,"atomic_batch":false,"external_writer_atomic_cas":false}),
        )
    }
}
