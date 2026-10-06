"""Build all workspace wheels from the frozen dependency baseline."""

import hashlib
import json
import subprocess
from pathlib import Path


def main() -> None:
    subprocess.run(["uv", "build", "--all-packages", "--wheel"], check=True)
    artifacts = []
    for path in sorted(Path("dist").glob("*.whl")):
        artifacts.append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )
    Path("dist/build-manifest.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "kind": "development-wheels",
                "signed": False,
                "lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
                "artifacts": artifacts,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
