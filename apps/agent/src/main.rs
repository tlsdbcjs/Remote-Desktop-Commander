mod cli;
mod platform;
#[cfg(windows)]
use platform::windows::{
    service::{native_service, service_admin, service_provision},
    setup::{machine_install, machine_setup},
};
use racp_contract::{RacpError, VERSION};
use serde_json::{json, Value};
use std::path::PathBuf;

// Validate the whole command before inspecting a package or touching local state.
fn arguments(args: &[String]) -> Result<Option<PathBuf>, RacpError> {
    let action = args.first().map(String::as_str).unwrap_or("");
    let flags: &[&str] = match action {
        "collection-worker" | "image-preview-worker" | "diagnostic-worker" | "trash-worker" => &[],
        "broker" | "guardian" => &["--pair-config"],
        "native-service-session" | "native-service-control" => &["--service-control"],
        "native-setup-host" => &["--setup-root", "--input-sha256"],
        "native-machine-setup" => &["--state-dir"],
        "native-machine-install-protect" | "native-machine-uninstall-preflight" => &[],
        "native-machine-setup-cleanup" => &[],
        "plugin-gdb" => &["--manifest", "--gdb", "--gdb-sha256"],
        "native-service" if args.len() == 5 => &["--state-dir", "--bootstrap-sha256"],
        "native-enroll"
        | "native-control"
        | "native-run"
        | "native-runtime-admit"
        | "native-runtime-configure"
        | "native-settings-configure"
        | "native-login-configure"
        | "native-runtime-status"
        | "native-login-status"
        | "native-maintenance-status"
        | "native-service"
        | "native-service-admin" => &["--state-dir"],
        "native-service-provision" => &["--state-dir"],
        _ => return Err(RacpError::new("REQUEST_INVALID")),
    };
    if args.len() != 1 + flags.len() * 2 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let mut seen = std::collections::BTreeSet::new();
    for pair in args[1..].chunks_exact(2) {
        if !flags.contains(&pair[0].as_str())
            || !seen.insert(pair[0].as_str())
            || pair[1].is_empty()
            || pair[1].contains(['\0', '\r', '\n'])
        {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
    }
    let state = args[1..]
        .chunks_exact(2)
        .find(|pair| pair[0] == "--state-dir")
        .map(|pair| PathBuf::from(&pair[1]));
    if let Some(path) = &state {
        racp_core::validate_local_path(path)?;
    }
    Ok(state)
}
fn framed(action: &str) -> bool {
    matches!(
        action,
        "broker"
            | "guardian"
            | "collection-worker"
            | "image-preview-worker"
            | "diagnostic-worker"
            | "trash-worker"
            | "plugin-gdb"
            | "native-run"
            | "native-service"
            | "native-service-session"
            | "native-control"
            | "native-service-control"
            | "native-setup-host"
    )
}
async fn input() -> Result<Vec<u8>, RacpError> {
    use tokio::io::{AsyncBufReadExt, AsyncReadExt};
    let mut raw = vec![];
    let mut reader = tokio::io::BufReader::new(tokio::io::stdin().take(65537));
    reader.read_until(b'\n', &mut raw).await?;
    if raw.len() > 65536 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    Ok(raw)
}
fn shutdown() -> tokio_util::sync::CancellationToken {
    let shutdown = tokio_util::sync::CancellationToken::new();
    let token = shutdown.clone();
    tokio::spawn(async move {
        let _ = tokio::signal::ctrl_c().await;
        token.cancel();
    });
    shutdown
}
#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() || matches!(args.as_slice(), [arg] if arg == "--help" || arg == "-h") {
        println!("racp-agent {VERSION}\n\nUsage: racp-agent <command> --state-dir <absolute-path>\n\nNative commands:\n  native-enroll  Explicit new registration; bounded JSON on stdin\n  native-run  User Agent with authenticated Named Pipe control\n  native-settings-configure  Stopped Agent workspace/profile revision CAS\n  native-login-status / native-login-configure  Current user's fixed login entry\n  native-runtime-status / native-runtime-admit / native-runtime-configure  Optional runtime admission/selection\n  native-service  RACPNativeAgent SCM host with separate private native config\n  native-service-admin  SYSTEM-only config handoff/reconcile; bounded JSON on stdin\n\nSecrets are never accepted in command arguments. Native state never imports legacy credentials. Service provisioning and native session Broker remain incomplete. GUI/acceptance gates remain deferred.");
        println!("  native-maintenance-status  Packaged user preflight: confirmed cleanup, storage format, login references; no stop/removal/rollback.");
        println!("  native-control --state-dir <path> / native-service-control --service-control <path>  Authenticated headless IPC; ControlRequest JSON on stdin, ControlReply/event JSON lines on stdout. Subscribe watches up to 5 seconds/256 events.");
        println!("  native-service-provision  Fresh SYSTEM-only fixed service/data roots from bounded controllers+enrollment JSON; prepares but never starts/enrolls.");
        println!("  native-service-session --service-control <absolute-path>  Controller's interactive desktop bridge; no service credential access. GUI provides explicit connect/disconnect.");
        println!("  native-machine-setup  Elevated administrator to fixed LocalSystem setup runner; fresh provisioning JSON on stdin; no Agent start/enrollment.");
        println!("  native-service-admin  SYSTEM handoff accepts configure, reconcile, or recover_sessions; recovery does not terminate user processes.");
        println!("  native-machine-install-protect / native-machine-uninstall-preflight  Fixed ProgramFiles versioned service-package ownership and removal gates.");
        println!("  native-machine-setup-cleanup  Elevated explicit stopped helper cleanup from setup_root/input_sha256 JSON; target service/state retained.");
        return;
    }
    if matches!(args.as_slice(), [arg] if arg == "--version") {
        println!("racp-agent {VERSION}");
        return;
    }
    let action = args[0].as_str();
    let result = async {
        let state = arguments(&args)?;
        #[cfg(windows)]
        if action == "native-machine-install-protect" {
            return machine_install::prepare();
        }
        #[cfg(windows)]
        if action == "native-machine-uninstall-preflight" {
            return machine_install::uninstall_preflight();
        }
        #[cfg(windows)]
        if action == "native-machine-setup-cleanup" {
            let raw = input().await?;
            return tokio::task::spawn_blocking(move || machine_setup::cleanup(&raw))
                .await
                .map_err(|_| RacpError::new("MACHINE_SETUP_CLEANUP_UNKNOWN"))?;
        }
        #[cfg(windows)]
        if action == "native-setup-host" {
            let value = |flag: &str| {
                args[1..]
                    .chunks_exact(2)
                    .find(|pair| pair[0] == flag)
                    .map(|pair| pair[1].clone())
                    .ok_or_else(|| RacpError::new("REQUEST_INVALID"))
            };
            machine_setup::host(
                PathBuf::from(value("--setup-root")?),
                value("--input-sha256")?,
            )?;
            return Ok(Value::Null);
        }
        let _payload = if matches!(
            action,
            "native-service"
                | "native-service-admin"
                | "native-service-provision"
                | "native-machine-setup"
        ) {
            racp_core::distribution::pin_current_agent_service_payload()?
        } else {
            racp_core::distribution::pin_current_agent_payload(
                action == "native-maintenance-status",
            )?
        };
        if action == "native-control" || action == "native-service-control" {
            let root = if action == "native-service-control" {
                PathBuf::from(&args[2])
            } else {
                state
                    .clone()
                    .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?
            };
            racp_core::validate_local_path(&root)?;
            cli::control::run(&root, action == "native-service-control", &input().await?).await?;
            return Ok(Value::Null);
        }
        match action {
            "collection-worker" => {
                racp_runtime::providers::recipes::run_worker()?;
                return Ok(Value::Null);
            }
            "image-preview-worker" => {
                racp_runtime::providers::run_preview_worker()?;
                return Ok(Value::Null);
            }
            "diagnostic-worker" => {
                racp_runtime::providers::run_diagnostic_worker()?;
                return Ok(Value::Null);
            }
            "plugin-gdb" => {
                racp_runtime::providers::reversing::run_native_plugin(action, &args)?;
                return Ok(Value::Null);
            }
            _ => (),
        }
        #[cfg(windows)]
        if action == "trash-worker" {
            racp_runtime::providers::run_trash_worker()?;
            return Ok(Value::Null);
        }
        #[cfg(windows)]
        if action == "broker" || action == "guardian" {
            let path = PathBuf::from(&args[2]);
            if action == "guardian" {
                racp_runtime::providers::desktop::run_guardian(&path)?;
            } else {
                racp_runtime::providers::desktop::run_broker(&path)?;
            }
            return Ok(Value::Null);
        }
        #[cfg(windows)]
        if action == "native-service-session" {
            racp_runtime::providers::desktop::run_service_session(
                std::path::Path::new(&args[2]),
                shutdown(),
            )
            .await?;
            return Ok(Value::Null);
        }
        let state = state.ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
        #[cfg(windows)]
        if action == "native-machine-setup" {
            let raw = input().await?;
            return tokio::task::spawn_blocking(move || machine_setup::execute(&state, &raw))
                .await
                .map_err(|_| RacpError::new("MACHINE_SETUP_OUTCOME_UNKNOWN"))?;
        }
        #[cfg(windows)]
        if action == "native-service-admin" {
            let raw = input().await?;
            return tokio::task::spawn_blocking(move || service_admin::execute(&state, &raw))
                .await
                .map_err(|_| RacpError::new("SERVICE_CONFIG_OUTCOME_UNKNOWN"))?;
        }
        #[cfg(windows)]
        if action == "native-service-provision" {
            let raw = input().await?;
            return tokio::task::spawn_blocking(move || service_provision::execute(&state, &raw))
                .await
                .map_err(|_| RacpError::new("SERVICE_PROVISION_OUTCOME_UNKNOWN"))?;
        }
        if matches!(
            action,
            "native-runtime-admit"
                | "native-runtime-configure"
                | "native-settings-configure"
                | "native-login-configure"
        ) {
            return racp_runtime::native_client::runtime_command(
                action,
                &input().await?,
                &state,
                shutdown(),
            )
            .await;
        }
        if action == "native-runtime-status" {
            racp_core::state_schema::NativeState::load(&state)?;
            return Ok(serde_json::to_value(
                racp_core::runtime_admission::NativeRuntimeConfig::load(&state)?,
            )?);
        }
        #[cfg(windows)]
        if action == "native-login-status" {
            return racp_runtime::native_login::status(&state);
        }
        #[cfg(windows)]
        if action == "native-maintenance-status" {
            return racp_runtime::native_maintenance::inspect(&state);
        }
        if action == "native-enroll" {
            return racp_runtime::native_client::enroll(&input().await?, &state).await;
        }
        #[cfg(windows)]
        if action == "native-run" {
            racp_runtime::native_client::run(&state, shutdown()).await?;
            return Ok(Value::Null);
        }
        #[cfg(windows)]
        if action == "native-service" {
            let bootstrap_hash = args[1..]
                .chunks_exact(2)
                .find(|pair| pair[0] == "--bootstrap-sha256")
                .map(|pair| pair[1].clone());
            native_service::run(state, bootstrap_hash)?;
            return Ok(Value::Null);
        }
        Err(RacpError::new("REQUEST_INVALID"))
    }
    .await;
    match result {
        Ok(value) if !framed(action) => println!("{}", json!({"ok":true,"result":value})),
        Ok(_) => (),
        Err(error) => {
            if framed(action) {
                eprintln!("{}", error.code.0);
            } else {
                println!("{}", json!({"ok":false,"error":{"code":error.code}}));
            }
            std::process::exit(4);
        }
    }
}
