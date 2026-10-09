use racp_contract::{decode_bridge, RacpError, VERSION};
use serde_json::{json, Value};
use std::path::PathBuf;
#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
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
        let state = state.ok_or_else(|| RacpError::new("REQUEST_INVALID"))?;
        racp_core::validate_local_path(&state)?;
        let mut browser = racp_runtime::providers::browser::BrowserConfig::bundled();
        browser.cdp_enabled = args.iter().any(|a| a == "--enable-cdp");
        browser.allow_origins = args
            .windows(2)
            .filter(|a| a[0] == "--browser-allow-origin")
            .map(|a| a[1].clone())
            .collect();
        match action {
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
            if action == "bridge" {
                println!("{}", json!({"ok":true,"result":value}));
            }
        }
        Err(error) => {
            if action == "bridge" {
                println!("{}", json!({"ok":false,"code":error.code}));
            } else {
                eprintln!("{}", error.code.0);
            }
            std::process::exit(4);
        }
    }
}
