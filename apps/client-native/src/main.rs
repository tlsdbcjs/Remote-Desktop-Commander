#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod constraints;
mod control_target;
mod i18n;
#[cfg(windows)]
mod ipc;
#[cfg(windows)]
mod native;
mod onboarding;
#[cfg(windows)]
mod slint_ui;
mod ui_model;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|arg| arg == "--version") {
        println!("racp-client {}", racp_contract::VERSION);
        return;
    }
    let startup =
        || -> Result<Option<racp_core::distribution::PinnedPayload>, racp_contract::RacpError> {
            let mut portable = false;
            let mut required = false;
            let mut start_agent = false;
            let mut service = false;
            let mut index = 0;
            while index < args.len() {
                match args[index].as_str() {
                    "--service-control" if !service => {
                        let path = args
                            .get(index + 1)
                            .ok_or_else(|| racp_contract::RacpError::new("REQUEST_INVALID"))?;
                        control_target::select_service(path.into())?;
                        service = true;
                        index += 2;
                    }
                    "--gpu" => index += 1,
                    "--start-agent" if !start_agent => {
                        start_agent = true;
                        index += 1;
                    }
                    "--require-package" if !required => {
                        required = true;
                        index += 1;
                    }
                    "--portable-state" | "--state-dir" if !portable => {
                        let path = args
                            .get(index + 1)
                            .ok_or_else(|| racp_contract::RacpError::new("REQUEST_INVALID"))?;
                        racp_core::state_schema::select_user_root(path.into())?;
                        portable = true;
                        index += 2;
                    }
                    _ => return Err(racp_contract::RacpError::new("REQUEST_INVALID")),
                }
            }
            if service && (portable || start_agent) {
                return Err(racp_contract::RacpError::new("REQUEST_INVALID"));
            }
            #[cfg(windows)]
            if !service
                && !portable
                && !start_agent
                && racp_core::machine_service::is_service_install(&std::env::current_exe()?)?
            {
                control_target::select_service(
                    racp_core::machine_service::data_parent()?.join("control"),
                )?;
                service = true;
            }
            racp_core::distribution::pin_current_payload(portable || required || service)
        };
    let _payload = match startup() {
        Ok(pins) => pins,
        Err(error) => {
            #[cfg(windows)]
            native::startup_error(error.code.0);
            std::process::exit(4);
        }
    };
    run(&args);
}

/// Slint GUI with the software renderer only (ADR-0032).
#[cfg(windows)]
fn run(args: &[String]) {
    if args.iter().any(|arg| arg == "--gpu") {
        native::startup_error("GPU_RENDERER_NOT_COMPILED");
        std::process::exit(4);
    }
    // Windows clears a hidden window's buffer but softbuffer still reports it as reused, so
    // Slint's partial repaint leaves stale areas after tray restore. Recreate it on show instead.
    // Set before the tray thread starts.
    unsafe {
        std::env::set_var("SLINT_DESTROY_WINDOW_ON_HIDE", "1");
    }
    let shell = match native::Shell::new() {
        Ok(Some(shell)) => Some(shell),
        Ok(None) => return,
        Err(code) => {
            eprintln!("native shell startup warning: {code}");
            None
        }
    };
    if let Err(code) = slint_ui::run(shell, args.iter().any(|arg| arg == "--start-agent")) {
        native::startup_error(&code);
        std::process::exit(4);
    }
}

/// macOS and Linux GUI adapters are deferred (ADR-0032 §1).
#[cfg(not(windows))]
fn run(_: &[String]) {
    eprintln!("NATIVE_PLATFORM_DEFERRED");
    std::process::exit(4);
}
