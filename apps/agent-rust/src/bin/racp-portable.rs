#![cfg_attr(windows, windows_subsystem = "windows")]
fn main() {
    if racp_runtime::portable::launch().is_err() {
        std::process::exit(4);
    }
}
