#[test]
#[ignore = "owned subprocess fixture only"]
#[allow(clippy::zombie_processes)] // The fixture deliberately exits first; containment must kill its child.
fn fixture_child() {
    match std::env::var("FIXTURE_MODE").unwrap().as_str() {
        "echo" => {
            println!(
                "unicode 한글 {}",
                std::env::current_dir().unwrap().display()
            );
            eprintln!("stderr marker");
            std::process::exit(7)
        }
        "spam" => {
            use std::io::Write;
            std::io::stdout()
                .write_all(&vec![b'x'; 1024 * 1024])
                .unwrap();
        }
        "sleep" => {
            println!("started");
            std::thread::sleep(std::time::Duration::from_secs(60));
        }
        "tree" => {
            let child = std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--ignored",
                    "--exact",
                    "support::fixture_child",
                    "--nocapture",
                ])
                .env("FIXTURE_MODE", "sleep")
                .spawn()
                .unwrap();
            println!("child_pid={}", child.id());
        }
        _ => panic!("unknown owned fixture"),
    }
}
