#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod accessibility;
mod app;
mod constraints;
mod control_target;
mod controls;
#[cfg(windows)]
mod native;
mod onboarding;
mod ui_model;

fn main() -> iced::Result {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|arg| arg == "--version") {
        println!("racp-client {}", racp_contract::VERSION);
        return Ok(());
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
    // Select before tray/Iced/Tokio create any other threads. Software is the product default.
    let gpu = args.iter().any(|arg| arg == "--gpu");
    if gpu && !cfg!(feature = "gpu-renderer") {
        eprintln!("GPU renderer is not compiled in this build");
        #[cfg(windows)]
        native::startup_error("GPU_RENDERER_NOT_COMPILED");
        std::process::exit(4);
    }
    unsafe {
        std::env::set_var("ICED_BACKEND", if gpu { "wgpu" } else { "tiny-skia" });
    }
    #[cfg(windows)]
    let shell = match native::Shell::new() {
        Ok(Some(shell)) => shell,
        Ok(None) => return Ok(()),
        Err(code) => {
            eprintln!("native shell startup: {code}");
            native::startup_error(&code);
            std::process::exit(4);
        }
    };
    let icon = image::load_from_memory_with_format(
        include_bytes!("../assets/app.png"),
        image::ImageFormat::Png,
    )
    .ok()
    .and_then(|image| {
        let rgba = image.to_rgba8();
        let (width, height) = rgba.dimensions();
        iced::window::icon::from_rgba(rgba.into_raw(), width, height).ok()
    });
    let result = iced::application(app::App::title, app::App::update, app::App::view)
        .theme(|_| iced::Theme::Dark)
        .subscription(app::App::subscription)
        .default_font(iced::Font::with_name("Malgun Gothic"))
        .window(iced::window::Settings {
            size: iced::Size::new(960.0, 680.0),
            visible: !cfg!(windows),
            icon,
            exit_on_close_request: !cfg!(windows),
            ..Default::default()
        })
        .run_with(move || {
            let (mut app, task) = app::App::new(args.iter().any(|arg| arg == "--start-agent"));
            #[cfg(windows)]
            {
                app.attach_shell(shell);
            }
            (app, task)
        });
    #[cfg(windows)]
    if result.is_err() {
        native::startup_error("GUI_RENDERER_OR_WINDOW_FAILED");
    }
    result
}
