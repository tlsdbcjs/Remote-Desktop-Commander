"""Build all workspace wheels from the frozen dependency baseline."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from racp_domain.version import VERSION


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", default=datetime.now(UTC).strftime("local-%Y%m%d-%H%M%S-%f"))
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.build_id):
        parser.error("invalid build ID")
    output = Path("dist/gateway") / VERSION / "python-any" / args.build_id / "wheels"
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run(
        ["uv", "build", "--all-packages", "--wheel", "--out-dir", str(output)], check=True
    )
    artifacts = []
    for path in sorted(output.glob("*.whl")):
        artifacts.append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )
    (output / "build-manifest.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "kind": "development-gateway-workspace-wheels",
                "component": "gateway",
                "build_id": args.build_id,
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
