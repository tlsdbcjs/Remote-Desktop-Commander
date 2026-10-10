#[cfg(windows)]
mod service;
use racp_contract::{decode_bridge, RacpError, VERSION};
use serde_json::{json, Value};
use std::path::PathBuf;
#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() || args.iter().any(|a| a == "--help" || a == "-h") {
        println!("racp-agent {VERSION}\n\nUsage: racp-agent <command> --state-dir <absolute-path>\n\nCommands:\n  provision-gdb / provision-ghidra / provision-native  Approve explicit local analysis tools\n  service   Fixed Windows SCM host (--state-dir or --config)\n  configure-service-login  Configure allowed user SIDs and endpoint\n  broker-login / broker-login-install / broker-login-remove  User-session registration\n  run       Run the registered Agent in the foreground (Ctrl+C stops it)\n  start     Start the registered Agent in the background\n  status    Show background Agent state as JSON\n  stop      Stop the background Agent and report cleanup\n  info      Show enrollment and execution identity (no credentials)\n  settings  Show editable settings and revision\n  activity  Show recent operation activity\n  bridge    Read one bounded JSON request from stdin and write a JSON result\n\nForeground options: --enable-cdp, --browser-allow-origin <origin> (repeatable)\nRegistration/settings updates use bridge JSON on stdin; do not put secrets in arguments.\nThe state directory is explicit so a manual run cannot silently select an existing profile.");
        return;
    }
    if args.iter().any(|a| a == "--version") {
        println!("racp-agent {VERSION}");
        return;
    }
    let action = args.first().map(String::as_str).unwrap_or("run");
    let state = args
        .windows(2)
        .find(|a| a[0] == "--state-dir")
        .map(|a| PathBuf::from(&a[1]));
    let result = async {
        if action == "collection-worker" {
            racp_runtime::providers::recipes::run_worker()?;
            return Ok(Value::Null);
        }
        if matches!(action, "plugin-gdb" | "plugin-ghidra") {
            racp_runtime::providers::reversing::run_native_plugin(action, &args)?;
            return Ok(Value::Null);
        }
        #[cfg(windows)]
        if action == "broker" || action == "guardian" {
            let path = args
                .windows(2)
                .find(|a| a[0] == "--pair-config")
                .map(|a| PathBuf::from(&a[1]))
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
            if action == "guardian" {
                racp_runtime::providers::desktop::run_guardian(&path)?;
            } else {
                racp_runtime::providers::desktop::run_broker(&path)?;
            }
            return Ok(Value::Null);
        }
        if action == "maintenance" {
            return racp_runtime::maintenance::prepare(&args).await;
        }
        #[cfg(windows)]
        if matches!(action, "broker-login" | "broker-register" | "broker-login-install" | "broker-login-remove") {
            let endpoint = args
                .windows(2)
                .find(|a| a[0] == "--login-endpoint")
                .map(|a| PathBuf::from(&a[1]))
                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
            if matches!(action, "broker-login-install" | "broker-login-remove") {
                return racp_runtime::providers::desktop::login_startup(&endpoint, action == "broker-login-remove");
            }
            if action=="broker-login" { racp_runtime::providers::desktop::run_login_broker(&endpoint)?; }
            else { racp_runtime::providers::desktop::register_login_broker(&endpoint)?; }
            return Ok(Value::Null);
        }
        #[cfg(windows)]
        if action=="service" {
            if let Some(path)=args.windows(2).find(|a|a[0]=="--config") {
                service::run_config(PathBuf::from(&path[1]))?;
                return Ok(Value::Null);
            }
        }
        let state = state.ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
        racp_core::validate_local_path(&state)?;
        #[cfg(windows)]
        if action == "configure-service-login" {
            return racp_runtime::providers::desktop::configure_login(&args, &state);
        }
        if action == "provision-native" {
            return racp_runtime::providers::provision_native(&args, &state);
        }
        if matches!(action, "provision-gdb" | "provision-ghidra") {
            return racp_runtime::providers::reversing::provision(action, &args, &state);
        }
        #[cfg(windows)]
        if action == "service" {
            service::run(state)?;
            return Ok(Value::Null);
        }
        if action == "maintenance" {
            return racp_runtime::maintenance::prepare(&args).await;
        }
        let mut browser = racp_runtime::providers::browser::BrowserConfig::bundled();
        browser.cdp_enabled = args.iter().any(|a| a == "--enable-cdp");
        browser.allow_origins = args
            .windows(2)
            .filter(|a| a[0] == "--browser-allow-origin")
            .map(|a| a[1].clone())
            .collect();
        match action {
            "info" => racp_core::information(&state),
            "settings" => racp_core::editable_settings(&state),
            "activity" => racp_runtime::activity(&state),
            "start" | "status" | "stop" => {
                if browser.cdp_enabled || !browser.allow_origins.is_empty() {
                    return Err(RacpError::new("REQUEST_INVALID"));
                }
                let control =
                    racp_runtime::ControlClient::new(state.clone(), std::env::current_exe()?);
                match action {
                    "start" => control.start().await,
                    "status" => control.status().await,
                    _ => control.stop().await,
                }
            }
            "serve" => {
                racp_runtime::serve_with_browser(&state, browser).await?;
                Ok(Value::Null)
            }
            "run" => {
                let (settings, values) = racp_core::load_settings(&state, true)?;
                let _lock = racp_core::InstanceLock::acquire(&state.join(format!(
                    "agent-{}.lock",
                    racp_contract::digest(&settings.device_id)
                )))?;
                let agent = racp_runtime::Agent::with_browser(
                    settings,
                    values["credential"].clone(),
                    browser,
                )?;
                let shutdown = tokio_util::sync::CancellationToken::new();
                let token = shutdown.clone();
                tokio::spawn(async move {
                    let _ = tokio::signal::ctrl_c().await;
                    token.cancel();
                });
                agent.run(shutdown).await?;
                Ok(Value::Null)
            }
            "bridge" => {
                use tokio::io::{AsyncBufReadExt, AsyncReadExt};
                let mut raw = vec![];
                let mut reader = tokio::io::BufReader::new(tokio::io::stdin().take(16385));
                reader.read_until(b'\n', &mut raw).await?;
                let request = decode_bridge(&raw)?;
                let control =
                    racp_runtime::ControlClient::new(state.clone(), std::env::current_exe()?);
                match request["action"].as_str() {
                    Some("info") => racp_core::information(&state),
                    Some("settings") => racp_core::editable_settings(&state),
                    Some("update_settings") => racp_core::update_settings(&state, request),
                    Some("enroll") => racp_runtime::enroll(request, &state).await,
                    Some("enroll_connection") => {
                        racp_runtime::enroll_connection(request, &state).await
                    }
                    Some("inspect_connection") => racp_core::inspect_connection(
                        std::path::Path::new(
                            request["path"]
                                .as_str()
                                .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?,
                        ),
                        None,
                    ),
                    Some("start") => control.start().await,
                    Some("status") => control.status().await,
                    Some("stop") => control.stop().await,
                    Some("activity") => racp_runtime::activity(&state),
                    _ => Err(RacpError::new("REQUEST_INVALID")),
                }
            }
            _ => Err(RacpError::new("REQUEST_INVALID")),
        }
    }
    .await;
    match result {
        Ok(value) => {
            if !matches!(
                action,
                "serve"
                    | "run"
                    | "broker"
                    | "broker-login"
                    | "broker-register"
                    | "guardian"
                    | "service"
                    | "collection-worker"
                    | "plugin-gdb"
                    | "plugin-ghidra"
            ) {
                println!("{}", json!({"ok":true,"result":value}));
            }
        }
        Err(error) => {
            if !matches!(
                action,
                "serve"
                    | "run"
                    | "broker"
                    | "broker-login"
                    | "broker-register"
                    | "guardian"
                    | "service"
                    | "plugin-gdb"
                    | "plugin-ghidra"
            ) {
                println!("{}", json!({"ok":false,"code":error.code}));
            } else {
                eprintln!("{}", error.code.0);
            }
            std::process::exit(4);
        }
    }
}
