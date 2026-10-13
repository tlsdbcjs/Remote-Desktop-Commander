//! Native build/staging orchestration; no Python, Node, Tauri or browser download.
pub mod agent_service;
mod artifacts;
mod evidence;
mod imports;
mod verify_artifacts;
use racp_contract::{distribution::*, VERSION};
use serde_json::json;
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::Path,
    process::{Command, Stdio},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
struct Options {
    platform: String,
    arch: String,
    build_id: Option<String>,
    dry_run: bool,
    install_scope: String,
}
fn options(args: &[String]) -> Result<Options> {
    let mut result = Options {
        platform: "win".into(),
        arch: "x64".into(),
        build_id: None,
        dry_run: false,
        install_scope: "user".into(),
    };
    let mut seen = std::collections::BTreeSet::new();
    let mut index = 0;
    while index < args.len() {
        let flag = args[index].as_str();
        if !seen.insert(flag) {
            return Err("duplicate packaging option".into());
        }
        if flag == "--dry-run" {
            result.dry_run = true;
            index += 1;
            continue;
        }
        let value = args
            .get(index + 1)
            .ok_or("missing packaging option value")?;
        match flag {
            "--platform" => result.platform = value.clone(),
            "--arch" => result.arch = value.clone(),
            "--build-id" => result.build_id = Some(value.clone()),
            "--install-scope" => result.install_scope = value.clone(),
            _ => return Err("unknown packaging option".into()),
        }
        index += 2;
    }
    if !["win", "mac", "linux"].contains(&result.platform.as_str())
        || !["x64", "arm64"].contains(&result.arch.as_str())
    {
        return Err("invalid packaging target".into());
    }
    if !["user", "machine", "both"].contains(&result.install_scope.as_str())
        || result.install_scope != "user" && result.platform != "win"
    {
        return Err("invalid installation scope; machine service package is Windows only".into());
    }
    if result.build_id.as_ref().is_some_and(|id| {
        id.is_empty()
            || id.len() > 80
            || !id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    }) {
        return Err("invalid build ID".into());
    }
    Ok(result)
}
pub fn plan(root: &Path, args: &[String]) -> Result<()> {
    let options = options(args)?;
    if options.install_scope != "user" {
        return Err("Client packages support user scope only; stage-agent-service supplies the independent service payload".into());
    }
    if !options.dry_run {
        return package(root, options);
    }
    let target = format!("{}-{}", options.platform, options.arch);
    let formats = match options.platform.as_str() {
        "win" if options.install_scope == "both" => vec![
            "NSIS user setup EXE",
            "NSIS machine service setup EXE",
            "portable EXE",
            "ZIP",
        ],
        "win" if options.install_scope == "machine" => {
            vec!["NSIS machine service setup EXE", "portable EXE", "ZIP"]
        }
        "win" => vec!["NSIS user setup EXE", "portable EXE", "ZIP"],
        "mac" => vec!["DMG", "APP ZIP"],
        _ => vec!["deb", "AppImage"],
    };
    let base_files = match options.platform.as_str() {
        "win" => BASE_FILES.to_vec(),
        "mac" => vec![
            "RACP.app/Contents/MacOS/racp-client",
            "RACP.app/Contents/MacOS/racp-agent",
            "RACP.app/Contents/Resources/LICENSE",
            "RACP.app/Contents/Resources/sbom.json",
        ],
        _ => vec!["racp-client", "racp-agent", "LICENSE", "sbom.json"],
    };
    let native_target = match (options.platform.as_str(), options.arch.as_str()) {
        ("win", "x64") => "x86_64-pc-windows-msvc",
        ("win", _) => "aarch64-pc-windows-msvc",
        ("mac", "x64") => "x86_64-apple-darwin",
        ("mac", _) => "aarch64-apple-darwin",
        ("linux", "x64") => "x86_64-unknown-linux-gnu",
        _ => "aarch64-unknown-linux-gnu",
    };
    println!(
        "{}",
        serde_json::to_string_pretty(
            &json!({"mode":"definition_only","version":VERSION,"platform":options.platform,"architecture":options.arch,
        "staging":root.join(format!("dist/client/{VERSION}/{target}/<build-id>/staging")),"output":root.join(format!("dist/client/{VERSION}/{target}/<build-id>/packages")),
        "formats":formats,"install_scope":options.install_scope,"base_files":base_files,"target":native_target,"minimum_os":"pending_native_validation","optional_runtimes":"separate admission; not packaged","state_root":"user/portable isolated root; machine-service ProgramData root with explicit registration",
        "native_host_required":true,"toolchain":"1.90.0","build_entrypoint":"Rust xtask","tests":"not_run_by_xtask","artifact_execution":false,"writes_files":false,
        "deferred_platforms":["macOS","Linux"],"signed":false})
        )?
    );
    Ok(())
}
fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}
fn package(root: &Path, options: Options) -> Result<()> {
    if options.platform != "win"
        || options.arch != "x64"
        || !cfg!(all(windows, target_arch = "x86_64"))
    {
        return Err(
            "artifact build requires native Windows x64; other platforms remain deferred".into(),
        );
    }
    let id = options
        .build_id
        .ok_or("package requires an existing verified --build-id")?;
    verify(root, &["--build-id".into(), id.clone()])?;
    let staging = root.join(format!("dist/client/{VERSION}/win-x64/{id}/staging"));
    let payload = staging.join("payload");
    let _pins = racp_core::distribution::pin_payload(&payload)?;
    let parent = root.join(format!("dist/client/{VERSION}/win-x64/{id}"));
    racp_core::private_dir(&parent)?;
    let _batch_lock = racp_core::InstanceLock::acquire(&parent.join("native-build.lock"))?;
    let batch = parent.join("packages");
    if batch.try_exists()? || parent.join("packages.quarantine").try_exists()? {
        return Err("artifact batch already exists; preserve prior outputs".into());
    }
    racp_core::create_private_native_directory(&batch)?;
    let result = (|| -> Result<()> {
        let stem = format!("RACP-Native-{VERSION}-win-x64");
        let portable = batch.join(format!("{stem}-portable.exe"));
        let zip = batch.join(format!("{stem}.zip"));
        artifacts::portable(
            &payload,
            &staging.join("racp-native-portable.exe"),
            &portable,
        )?;
        artifacts::zip(&payload, &zip)?;
        verify_artifacts::portable(
            &portable,
            &payload,
            &staging.join("racp-native-portable.exe"),
        )?;
        verify_artifacts::zip(&zip, &payload)?;
        let string = |path: &Path| -> Result<String> {
            let value = path.to_str().ok_or("NSIS path must be Unicode")?;
            if value.contains(['$', '"', '\r', '\n']) {
                return Err("NSIS path contains unsupported quoting".into());
            }
            Ok(value.to_owned())
        };
        let scopes: Vec<_> = match options.install_scope.as_str() {
            "both" => vec!["user", "machine"],
            scope => vec![scope],
        };
        let mut rows = vec![artifacts::hash(&portable)?, artifacts::hash(&zip)?];
        for scope in scopes {
            let setup = batch.join(format!(
                "{stem}-{}setup.exe",
                if scope == "machine" { "machine-" } else { "" }
            ));
            let recipe = if scope == "machine" {
                "native-machine-installer.nsi"
            } else {
                "native-installer.nsi"
            };
            let status = Command::new("makensis.exe")
                .args(["/INPUTCHARSET", "UTF8"])
                .arg("/WX")
                .arg(format!("/DPRODUCT_VERSION={VERSION}"))
                .arg(format!("/DPAYLOAD={}", string(&payload)?))
                .arg(format!("/DOUTPUT={}", string(&setup)?))
                .arg(root.join("xtask").join(recipe))
                .current_dir(root)
                .stdin(Stdio::null())
                .status()?;
            if !status.success() {
                return Err("NSIS native installer compile failed".into());
            }
            rows.push(artifacts::hash(&setup)?);
        }
        let sums = rows
            .iter()
            .map(|row| {
                format!(
                    "{}  {}\n",
                    row["sha256"].as_str().unwrap_or(""),
                    row["file"].as_str().unwrap_or("")
                )
            })
            .collect::<String>();
        write_new(&batch.join("SHA256SUMS"), sums.as_bytes())?;
        let source: serde_json::Value =
            serde_json::from_slice(&fs::read(staging.join("build-manifest.json"))?)?;
        let manifest = json!({"format_version":1,"product_version":VERSION,"build_id":id,"kind":"development-native-package","platform":"win","architecture":"x64","install_scope":options.install_scope,
            "build":source,"artifacts":rows,"signed":false,"published":false,"tests":"not_run_by_xtask","native_acceptance":"not_run_by_xtask"});
        write_new(
            &batch.join("build-manifest.json"),
            &serde_json::to_vec_pretty(&manifest)?,
        )?;
        Ok(())
    })();
    if let Err(error) = result {
        racp_core::validate_local_path(&batch)?;
        let quarantine = parent.join("packages.quarantine");
        if batch.parent() != Some(parent.as_path()) {
            return Err("artifact quarantine containment failed".into());
        }
        write_new(
            &batch.join("build-failure.json"),
            b"{\"status\":\"failed\",\"published\":false}\n",
        )?;
        fs::rename(&batch, &quarantine)?;
        return Err(error);
    }
    println!("NATIVE_PACKAGE_EVIDENCE {}", batch.display());
    Ok(())
}
fn copy(root: &Path, source: &Path, name: &str) -> Result<PayloadFile> {
    let mut input = racp_core::secure_read_file(source)?;
    let (size, hash) = racp_core::distribution::hash_file(&mut input, MAX_PAYLOAD_BYTES / 2)?;
    use std::io::{Seek, SeekFrom};
    input.seek(SeekFrom::Start(0))?;
    let mut output = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(root.join(name))?;
    std::io::copy(&mut input, &mut output)?;
    output.sync_all()?;
    Ok(PayloadFile {
        path: name.into(),
        size_bytes: size,
        sha256: hash,
    })
}
pub fn stage(root: &Path, args: &[String]) -> Result<()> {
    stage_component(root, args, StageRole::Client)
}
pub fn stage_agent(root: &Path, args: &[String]) -> Result<()> {
    stage_component(root, args, StageRole::Agent)
}
pub fn stage_agent_service(root: &Path, args: &[String]) -> Result<()> {
    stage_component(root, args, StageRole::AgentService)
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum StageRole {
    Client,
    Agent,
    AgentService,
}
fn stage_component(root: &Path, args: &[String], role: StageRole) -> Result<()> {
    let options = options(args)?;
    let agent_only = role != StageRole::Client;
    let service = role == StageRole::AgentService;
    let phase = if role == StageRole::Agent {
        "build"
    } else {
        "staging"
    };
    if service
        && (options.platform != "win" || options.arch != "x64" || options.install_scope != "user")
    {
        return Err(
            "Agent service staging supports Windows x64 only; installation is a separate operation"
                .into(),
        );
    }
    if options.dry_run {
        if agent_only {
            println!(
                "{}",
                json!({"mode":"definition_only", "version":VERSION, "output":root.join(format!("dist/agent/{VERSION}/win-x64/<build-id>/{phase}")), "payload_kind":if service { "native-agent-service" } else { "development-native-agent" }, "base_files":AGENT_SERVICE_FILES, "writes_files":false})
            );
            return Ok(());
        }
        return plan(root, args);
    }
    if options.platform != "win"
        || options.arch != "x64"
        || !cfg!(all(windows, target_arch = "x86_64"))
    {
        return Err("staging requires native Windows x64; other targets remain deferred".into());
    }
    let build_id = options
        .build_id
        .ok_or("stage requires an explicit unique --build-id")?;
    let component = if agent_only { "agent" } else { "client" };
    let parent = root.join(format!("dist/{component}/{VERSION}/win-x64/{build_id}"));
    racp_core::private_dir(&parent)?;
    let _batch_lock = racp_core::InstanceLock::acquire(&parent.join("native-build.lock"))?;
    let batch = parent.join(phase);
    if batch.try_exists()? || parent.join(format!("{phase}.quarantine")).try_exists()? {
        return Err("batch already exists; preserve prior outputs".into());
    }
    racp_core::create_private_native_directory(&batch)?;
    let result = stage_batch(root, &batch, role);
    if let Err(error) = result {
        // Only the newly reserved direct-child batch is moved; no recursive delete.
        racp_core::validate_local_path(&batch)?;
        if batch.parent() != Some(parent.as_path()) {
            return Err("quarantine containment failed".into());
        }
        let quarantine = parent.join(format!("{phase}.quarantine"));
        write_new(
            &batch.join("build-failure.json"),
            b"{\"status\":\"failed\",\"artifacts_published\":false}\n",
        )?;
        fs::rename(&batch, &quarantine)?;
        return Err(error);
    }
    println!("NATIVE_STAGING_EVIDENCE {}", batch.display());
    Ok(())
}
fn stage_batch(root: &Path, batch: &Path, role: StageRole) -> Result<()> {
    let agent_only = role != StageRole::Client;
    let service = role == StageRole::AgentService;
    let cargo = std::env::var("CARGO").unwrap_or_else(|_| "cargo".into());
    let compiler = evidence::command(root, &cargo, &["--version"])?;
    if !compiler.starts_with("cargo 1.90.0 ") {
        return Err("pinned Cargo 1.90.0 required".into());
    }
    let rustc = Path::new(&cargo)
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .map(|path| path.join("rustc.exe"))
        .unwrap_or_else(|| "rustc.exe".into());
    let rustc_version = evidence::command(
        root,
        rustc.to_str().ok_or("compiler path invalid")?,
        &["--version", "--verbose"],
    )?;
    if !rustc_version.starts_with("rustc 1.90.0 ")
        || !rustc_version.contains("host: x86_64-pc-windows-msvc")
    {
        return Err("pinned native rustc 1.90.0 required".into());
    }
    let source_before = evidence::source(root)?;
    let target_dir = std::env::var_os("RACP_NATIVE_TARGET_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| root.join("target/native-product"));
    let target_dir = racp_core::validate_local_path(&target_dir)?;
    let mut command = Command::new(&cargo);
    command
        .args([
            "build",
            "--locked",
            "--release",
            "--target",
            "x86_64-pc-windows-msvc",
            "-p",
            "racp-agent",
        ])
        .current_dir(root)
        .env("CARGO_TARGET_DIR", &target_dir)
        .env_remove("CARGO_ENCODED_RUSTFLAGS")
        .env("RUSTFLAGS", "-C target-feature=+crt-static")
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    if agent_only {
        command.args(["--bin", "racp-agent"]);
    } else {
        command.args(["-p", "racp-client-native", "--bins"]);
    }
    if !command.status()?.success() {
        return Err("native production build failed".into());
    }
    if agent_only {
        let service_payload = batch.join("payload");
        let payload = if service {
            racp_core::create_private_native_directory(&service_payload)?;
            service_payload.as_path()
        } else {
            batch
        };
        let executable = copy(
            payload,
            &target_dir.join("x86_64-pc-windows-msvc/release/racp-agent.exe"),
            "racp-agent.exe",
        )?;
        let license = copy(payload, &root.join("LICENSE"), "LICENSE")?;
        let sbom = serde_json::to_vec_pretty(&evidence::sbom_for(
            root,
            &cargo,
            "RACP Agent",
            &["racp-agent"],
        )?)?;
        write_new(&payload.join("sbom.json"), &sbom)?;
        let files = vec![
            executable,
            license,
            PayloadFile {
                path: "sbom.json".into(),
                size_bytes: sbom.len() as u64,
                sha256: racp_contract::digest(&sbom),
            },
        ];
        let source = evidence::source(root)?;
        if source["input_digest"] != source_before["input_digest"] {
            return Err("Agent source inputs changed during build".into());
        }
        let mut binary = racp_core::secure_read_file(&payload.join("racp-agent.exe"))?;
        let mut build = json!({"format_version":1, "product_version":VERSION, "kind":if service { "development-native-agent-service" } else { "development-native-agent" }, "platform":"win", "architecture":"x64", "source":source, "compiler":rustc_version.trim(), "cargo":compiler.trim(), "target":"x86_64-pc-windows-msvc", "static_crt":true, "cargo_lock_sha256":racp_contract::digest(fs::read(root.join("Cargo.lock"))?), "files":files, "native_imports":imports::inspect(&mut binary)?, "signed":false, "published":false, "tests":"not_run_by_xtask"});
        if service {
            let manifest = NativePayload {
                format_version: 1,
                product_version: VERSION.into(),
                kind: PayloadKind::AgentService,
                platform: "win".into(),
                architecture: "x64".into(),
                files,
            };
            manifest.validate()?;
            let bytes = serde_json::to_vec_pretty(&manifest)?;
            write_new(&payload.join(MANIFEST_FILE), &bytes)?;
            let _pins = racp_core::distribution::pin_agent_service_payload(payload)?;
            build["payload_manifest_sha256"] = json!(racp_contract::digest(&bytes));
            build["payload"] = serde_json::to_value(manifest)?;
        }
        write_new(
            &batch.join("build-manifest.json"),
            &serde_json::to_vec_pretty(&build)?,
        )?;
        return Ok(());
    }
    let payload = batch.join("payload");
    racp_core::create_private_native_directory(&payload)?;
    let mut files = Vec::new();
    for name in ["racp-agent.exe", "racp-client.exe"] {
        files.push(copy(
            &payload,
            &target_dir.join("x86_64-pc-windows-msvc/release").join(name),
            name,
        )?);
    }
    files.push(copy(&payload, &root.join("LICENSE"), "LICENSE")?);
    let sbom = serde_json::to_vec_pretty(&evidence::sbom(root, &cargo)?)?;
    write_new(&payload.join("sbom.json"), &sbom)?;
    files.push(PayloadFile {
        path: "sbom.json".into(),
        size_bytes: sbom.len() as u64,
        sha256: racp_contract::digest(&sbom),
    });
    files.sort_by(|a, b| a.path.cmp(&b.path));
    let manifest = NativePayload {
        format_version: 1,
        product_version: VERSION.into(),
        kind: PayloadKind::Client,
        platform: "win".into(),
        architecture: "x64".into(),
        files,
    };
    manifest.validate()?;
    write_new(
        &payload.join(MANIFEST_FILE),
        &serde_json::to_vec_pretty(&manifest)?,
    )?;
    let _pins = racp_core::distribution::pin_payload(&payload)?;
    let lock = fs::read(root.join("Cargo.lock"))?;
    let launcher = copy(
        batch,
        &target_dir.join("x86_64-pc-windows-msvc/release/racp-native-portable.exe"),
        "racp-native-portable.exe",
    )?;
    let source = evidence::source(root)?;
    if source["input_digest"] != source_before["input_digest"] {
        return Err("native source inputs changed during build".into());
    }
    let mut dependencies = std::collections::BTreeMap::new();
    for name in ["racp-agent.exe", "racp-client.exe"] {
        let mut file = racp_core::secure_read_file(&payload.join(name))?;
        dependencies.insert(name, imports::inspect(&mut file)?);
    }
    let mut launcher_binary = racp_core::secure_read_file(&batch.join("racp-native-portable.exe"))?;
    dependencies.insert(
        "racp-native-portable.exe",
        imports::inspect(&mut launcher_binary)?,
    );
    let build = json!({"format_version":1,"product_version":VERSION,"kind":"development-native-staging","platform":"win","architecture":"x64","source":source,
        "compiler":rustc_version.trim(),"cargo":compiler.trim(),"native_imports":dependencies,"toolchain":"1.90.0","target":"x86_64-pc-windows-msvc","static_crt":true,"cargo_lock_sha256":racp_contract::digest(lock),
        "features":["software-renderer"],"payload":manifest,"launcher":launcher,"payload_manifest_sha256":racp_contract::digest(fs::read(payload.join(MANIFEST_FILE))?),
        "signed":false,"license_review":"pending","tests":"not_run_by_xtask","native_acceptance":"not_run_by_xtask","optional_runtimes":[],"deferred_platforms":["macOS","Linux"],"artifacts_published":false});
    write_new(
        &batch.join("build-manifest.json"),
        &serde_json::to_vec_pretty(&build)?,
    )?;
    Ok(())
}
pub fn verify_agent(root: &Path, args: &[String]) -> Result<()> {
    let options = options(args)?;
    if options.platform != "win" || options.arch != "x64" || options.dry_run {
        return Err("verify-agent requires a Windows x64 build ID".into());
    }
    let id = options.build_id.ok_or("verify-agent requires --build-id")?;
    let batch = root.join(format!("dist/agent/{VERSION}/win-x64/{id}/build"));
    racp_core::verify_private_native_directory(&batch)?;
    let manifest: serde_json::Value = serde_json::from_slice(&racp_core::read_bounded(
        &batch.join("build-manifest.json"),
        16 * 1024 * 1024,
        true,
    )?)?;
    if manifest["product_version"] != VERSION || manifest["kind"] != "development-native-agent" {
        return Err("Agent build identity mismatch".into());
    }
    let files: Vec<PayloadFile> = serde_json::from_value(manifest["files"].clone())?;
    let mut expected = vec![
        "LICENSE".to_string(),
        "racp-agent.exe".to_string(),
        "sbom.json".to_string(),
    ];
    let mut names: Vec<_> = files.iter().map(|file| file.path.clone()).collect();
    names.sort();
    if names != expected {
        return Err("Agent file manifest mismatch".into());
    }
    expected.push("build-manifest.json".into());
    expected.sort();
    let mut actual: Vec<_> = fs::read_dir(&batch)?
        .map(|entry| entry.map(|entry| entry.file_name().to_string_lossy().into_owned()))
        .collect::<std::io::Result<_>>()?;
    actual.sort();
    if actual != expected {
        return Err("Unexpected standalone Agent files".into());
    }
    for file in files {
        let mut input = racp_core::secure_read_file(&batch.join(&file.path))?;
        let (size, hash) = racp_core::distribution::hash_file(&mut input, MAX_PAYLOAD_BYTES / 2)?;
        if size != file.size_bytes || hash != file.sha256 {
            return Err("Agent file integrity mismatch".into());
        }
    }
    let mut binary = racp_core::secure_read_file(&batch.join("racp-agent.exe"))?;
    if manifest["native_imports"] != serde_json::to_value(imports::inspect(&mut binary)?)? {
        return Err("Agent native imports mismatch".into());
    }
    println!("static_pass: standalone Agent {id} hashes and native target verified");
    Ok(())
}
pub fn verify_agent_service(root: &Path, args: &[String]) -> Result<()> {
    let options = options(args)?;
    if options.platform != "win" || options.arch != "x64" || options.dry_run {
        return Err("verify-agent-service requires a Windows x64 build ID".into());
    }
    let id = options
        .build_id
        .ok_or("verify-agent-service requires --build-id")?;
    let batch = root.join(format!("dist/agent/{VERSION}/win-x64/{id}/staging"));
    racp_core::verify_private_native_directory(&batch)?;
    let metadata =
        racp_core::read_bounded(&batch.join("build-manifest.json"), 16 * 1024 * 1024, true)?;
    racp_contract::local_ipc::reject_duplicate_keys(&metadata)?;
    let build: serde_json::Value = serde_json::from_slice(&metadata)?;
    if build["format_version"] != 1
        || build["product_version"] != VERSION
        || build["kind"] != "development-native-agent-service"
        || build["platform"] != "win"
        || build["architecture"] != "x64"
    {
        return Err("Agent service build identity mismatch".into());
    }
    let payload = batch.join("payload");
    let pins = racp_core::distribution::pin_agent_service_payload(&payload)?;
    if build["payload"] != serde_json::to_value(&pins.manifest)?
        || build["files"] != serde_json::to_value(&pins.manifest.files)?
        || build["payload_manifest_sha256"]
            != racp_contract::digest(racp_core::read_bounded(
                &payload.join(MANIFEST_FILE),
                1048576,
                false,
            )?)
    {
        return Err("Agent service manifest mismatch".into());
    }
    let mut actual: Vec<_> = fs::read_dir(&batch)?
        .map(|entry| entry.map(|entry| entry.file_name().to_string_lossy().into_owned()))
        .collect::<std::io::Result<_>>()?;
    actual.sort();
    if actual != ["build-manifest.json", "payload"] {
        return Err("Unexpected Agent service staging files".into());
    }
    let mut binary = racp_core::secure_read_file(&payload.join("racp-agent.exe"))?;
    if build["native_imports"] != serde_json::to_value(imports::inspect(&mut binary)?)? {
        return Err("Agent service native imports mismatch".into());
    }
    println!("static_pass: Agent service {id} / 3 GUI-free payload files / hashes and native target verified");
    Ok(())
}
pub fn verify(root: &Path, args: &[String]) -> Result<()> {
    let options = options(args)?;
    if options.platform != "win" || options.arch != "x64" || options.dry_run {
        return Err("verify requires a Windows x64 staging build ID".into());
    }
    let id = options.build_id.ok_or("verify requires --build-id")?;
    let batch = root.join(format!("dist/client/{VERSION}/win-x64/{id}/staging"));
    racp_core::verify_private_native_directory(&batch)?;
    let pins = racp_core::distribution::pin_payload(&batch.join("payload"))?;
    let evidence: serde_json::Value = serde_json::from_slice(&racp_core::read_bounded(
        &batch.join("build-manifest.json"),
        16 * 1024 * 1024,
        true,
    )?)?;
    let mut launcher = racp_core::secure_read_file(&batch.join("racp-native-portable.exe"))?;
    let (size, hash) = racp_core::distribution::hash_file(&mut launcher, MAX_PAYLOAD_BYTES / 2)?;
    use std::io::{Seek, SeekFrom};
    launcher.seek(SeekFrom::Start(0))?;
    racp_core::runtime_admission::validate_host_binary(&mut launcher)?;
    if evidence["launcher"]["path"] != "racp-native-portable.exe"
        || evidence["launcher"]["sha256"] != hash
        || evidence["launcher"]["size_bytes"] != size
    {
        return Err("portable launcher integrity mismatch".into());
    }
    if evidence["product_version"] != VERSION
        || evidence["payload"] != serde_json::to_value(&pins.manifest)?
        || evidence["payload_manifest_sha256"]
            != racp_contract::digest(fs::read(batch.join("payload").join(MANIFEST_FILE))?)
    {
        return Err("build manifest integrity mismatch".into());
    }
    if let Some(expected) = evidence.get("native_imports") {
        for name in [
            "racp-client.exe",
            "racp-agent.exe",
            "racp-native-portable.exe",
        ] {
            let path = if name == "racp-native-portable.exe" {
                batch.join(name)
            } else {
                batch.join("payload").join(name)
            };
            let mut file = racp_core::secure_read_file(&path)?;
            if expected[name] != serde_json::to_value(imports::inspect(&mut file)?)? {
                return Err("native dependency manifest mismatch".into());
            }
        }
    }
    println!(
        "static_pass: native staging {} / {} payload files / hashes and native target verified",
        id,
        pins.manifest.files.len()
    );
    Ok(())
}
