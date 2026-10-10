#![cfg_attr(windows, windows_subsystem = "windows")]
fn main() {
    if racp_runtime::portable::launch().is_err() {
        racp_runtime::portable::report_failure();
        std::process::exit(4);
    }
}
