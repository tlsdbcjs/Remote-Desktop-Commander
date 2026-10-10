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
        "memory" => {
            use std::io::Write;
            let data: Vec<u8> = (0..128 * 1024).map(|index| (index % 256) as u8).collect();
            println!(
                "memory_fixture={}",
                serde_json::json!({"address":format!("0x{:x}",data.as_ptr() as usize),"size_bytes":data.len()})
            );
            std::io::stdout().flush().unwrap();
            let mut input = String::new();
            let _ = std::io::stdin().read_line(&mut input);
            std::hint::black_box(&data);
        }
        "sleep" => {
            println!("started");
            std::thread::sleep(std::time::Duration::from_secs(60));
        }
        "terminal" => {
            println!("terminal ready");
            let mut input = String::new();
            loop {
                input.clear();
                if std::io::stdin().read_line(&mut input).unwrap() == 0 {
                    break;
                }
                println!("response={}", input.trim());
                if input.trim() == "quit" {
                    break;
                }
            }
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
