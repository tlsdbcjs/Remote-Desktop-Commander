//! Owned native test fixture. Never staged in a client bundle.
use std::{io::Write, time::Duration};
fn main() {
    match std::env::args().nth(1).as_deref() {
        Some("echo") => {
            println!("native 한글 {}", std::env::current_dir().unwrap().display());
            eprintln!("native stderr");
            std::process::exit(7)
        }
        Some("sleep") => std::thread::sleep(Duration::from_secs(60)),
        Some("delay") => {
            std::thread::sleep(Duration::from_millis(400));
            std::process::exit(5)
        }
        Some("spam") => {
            std::io::stdout()
                .write_all(&vec![b'x'; 1024 * 1024])
                .unwrap();
        }
        _ => std::process::exit(2),
    }
}
