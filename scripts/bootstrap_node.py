"""Install a checksum-verified portable reference Node in this workspace on Windows."""

import hashlib
import json
import os
import urllib.request
import zipfile
from pathlib import Path


def main() -> None:
    if os.name != "nt":
        raise SystemExit(
            "Use Node 22.23.0 from your platform's official distribution or CI setup-node"
        )
    version = "22.23.0"
    name = f"node-v{version}-win-x64.zip"
    url = f"https://nodejs.org/dist/v{version}/"
    root = Path(".tools").resolve()
    root.mkdir(exist_ok=True)
    with urllib.request.urlopen(url + "SHASUMS256.txt", timeout=30) as response:
        checks = response.read().decode()
    expected = next(line.split()[0] for line in checks.splitlines() if line.split()[-1] == name)
    archive = root / name
    if not archive.exists():
        with urllib.request.urlopen(url + name, timeout=60) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise SystemExit("Node archive checksum differs; no executable was extracted")
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            (root / item.filename).resolve().relative_to(root)
        bundle.extractall(root)
    (root / "node-manifest.json").write_text(
        json.dumps({"version": version, "source": url + name, "sha256": expected}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Verified Node {version}: {root / f'node-v{version}-win-x64/node.exe'}")


if __name__ == "__main__":
    main()
