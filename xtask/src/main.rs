mod inventory;
mod package;

use std::path::Path;

fn main() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("inventory-check" | "contract-check") if args.len() == 1 => inventory::check(root),
        Some("package") => package::plan(root, &args[1..]),
        Some("stage") => package::stage(root, &args[1..]),
        Some("stage-agent") => package::stage_agent(root, &args[1..]),
        Some("verify-agent") => package::verify_agent(root, &args[1..]),
        Some("verify-staging") => package::verify(root, &args[1..]),
        _ => Err("usage: xtask inventory-check | contract-check | package --platform win --arch x64 --dry-run".into()),
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
