//! Agent-only setup artifact creation. Never executes an installer or changes SCM/state.
use super::{artifacts, evidence, options, verify_agent_service, write_new, Result};
use racp_contract::{distribution::AGENT_SERVICE_FILES, VERSION};
use serde_json::json;
use std::{
    fs,
    path::Path,
    process::{Command, Stdio},
};

pub fn plan(root: &Path, args: &[String]) -> Result<()> {
    let options = options(args)?;
    if options.platform != "win"
        || options.arch != "x64"
        || args.iter().any(|arg| arg == "--install-scope")
    {
        return Err("Agent service packages require Windows x64; machine scope is fixed".into());
    }
    if options.dry_run {
        println!(
            "{}",
            json!({"mode":"definition_only","version":VERSION,
            "payload_kind":"native-agent-service","base_files":AGENT_SERVICE_FILES,
            "install_scope":"machine","formats":["NSIS Agent service setup EXE"],
            "output":root.join(format!("dist/agent/{VERSION}/win-x64/<build-id>/packages")),
            "artifact_execution":false,"writes_files":false,"signed":false,"published":false})
        );
        return Ok(());
    }
    if !cfg!(all(windows, target_arch = "x86_64")) {
        return Err("Agent service artifact creation requires native Windows x64".into());
    }
    let id = options
        .build_id
        .ok_or("package-agent-service requires a verified --build-id")?;
    verify_agent_service(root, &["--build-id".into(), id.clone()])?;
    let parent = root.join(format!("dist/agent/{VERSION}/win-x64/{id}"));
    let staging = parent.join("staging");
    let payload = staging.join("payload");
    let _pins = racp_core::distribution::pin_agent_service_payload(&payload)?;
    let build: serde_json::Value = serde_json::from_slice(&racp_core::read_bounded(
        &staging.join("build-manifest.json"),
        16 * 1024 * 1024,
        true,
    )?)?;
    if evidence::source(root)?["input_digest"] != build["source"]["input_digest"] {
        return Err("Agent service source changed since staging".into());
    }
    let _lock = racp_core::InstanceLock::acquire(&parent.join("native-build.lock"))?;
    let batch = parent.join("packages");
    if batch.try_exists()? || parent.join("packages.quarantine").try_exists()? {
        return Err("Agent service package batch exists; preserve prior outputs".into());
    }
    racp_core::create_private_native_directory(&batch)?;
    let result = (|| -> Result<()> {
        if evidence::command(root, "makensis.exe", &["/VERSION"])?.trim() != "v3.11" {
            return Err("pinned NSIS 3.11 required".into());
        }
        let setup = batch.join(format!("RACP-Agent-{VERSION}-win-x64-service-setup.exe"));
        let path = |value: &Path| -> Result<String> {
            let value = value.to_str().ok_or("invalid NSIS path")?;
            if value.contains(['\0', '\r', '\n', '"']) {
                return Err("unsafe NSIS path".into());
            }
            Ok(value.to_owned())
        };
        let status = Command::new("makensis.exe")
            .args(["/INPUTCHARSET", "UTF8", "/WX"])
            .arg(format!("/DPRODUCT_VERSION={VERSION}"))
            .arg(format!("/DPAYLOAD={}", path(&payload)?))
            .arg(format!("/DOUTPUT={}", path(&setup)?))
            .arg(root.join("xtask/native-machine-installer.nsi"))
            .current_dir(root)
            .stdin(Stdio::null())
            .status()?;
        if !status.success() {
            return Err("Agent service NSIS compilation failed".into());
        }
        if evidence::source(root)?["input_digest"] != build["source"]["input_digest"] {
            return Err("Agent service source changed during packaging".into());
        }
        let artifact = artifacts::hash(&setup)?;
        write_new(
            &batch.join("SHA256SUMS"),
            format!(
                "{}  {}\n",
                artifact["sha256"].as_str().ok_or("missing artifact hash")?,
                artifact["file"]
                    .as_str()
                    .ok_or("missing artifact filename")?
            )
            .as_bytes(),
        )?;
        write_new(
            &batch.join("build-manifest.json"),
            &serde_json::to_vec_pretty(&json!({
            "format_version":1,"product_version":VERSION,"kind":"development-native-agent-service-package",
            "platform":"win","architecture":"x64","install_scope":"machine","build":build,
            "artifacts":[artifact],"artifact_execution":false,"service_registered":false,
            "signed":false,"published":false,"native_acceptance":"not_run_by_xtask"}))?,
        )?;
        Ok(())
    })();
    if let Err(error) = result {
        racp_core::validate_local_path(&batch)?;
        if batch.parent() != Some(parent.as_path()) {
            return Err("package quarantine containment failed".into());
        }
        write_new(
            &batch.join("build-failure.json"),
            b"{\"status\":\"failed\",\"published\":false}\n",
        )?;
        fs::rename(&batch, parent.join("packages.quarantine"))?;
        return Err(error);
    }
    println!("AGENT_SERVICE_PACKAGE_EVIDENCE {}", batch.display());
    Ok(())
}
