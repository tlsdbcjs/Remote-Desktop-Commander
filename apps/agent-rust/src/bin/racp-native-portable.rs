#![cfg_attr(windows, windows_subsystem = "windows")]
fn main() {
    if racp_runtime::native_portable::launch().is_err() {
        racp_runtime::native_portable::report_failure();
        std::process::exit(4);
    }
}
