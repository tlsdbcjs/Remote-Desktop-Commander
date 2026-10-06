"""Verify pinned Node, generated client drift and Console production build."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "node"))
    args = parser.parse_args()
    node = str(args.node.resolve())
    version = subprocess.check_output([node, "--version"], text=True).strip()
    if version != "v22.23.0":
        raise SystemExit("Console reference build requires Node 22.23.0")
    root = Path("apps/console")
    modules = root / "node_modules"
    prettier = str(modules / "prettier/bin/prettier.cjs")
    subprocess.run(
        [
            node,
            prettier,
            "--check",
            str(root / "src"),
            str(root / "tests"),
            *map(str, root.glob("*.ts")),
            *map(str, root.glob("*.json")),
            str(root / "index.html"),
        ],
        check=True,
    )
    subprocess.run(
        [
            node,
            str(modules / "typescript/bin/tsc"),
            "--project",
            str(root / "tsconfig.json"),
            "--noEmit",
        ],
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="racp-client-") as temporary:
        generated = Path(temporary) / "generated.ts"
        subprocess.run(
            [
                node,
                str(modules / "openapi-typescript/bin/cli.js"),
                "docs/protocol/console-openapi-v1.json",
                "-o",
                str(generated),
            ],
            check=True,
        )
        subprocess.run([node, prettier, "--write", str(generated)], check=True)
        if generated.read_text(encoding="utf-8") != (root / "src/generated.ts").read_text(
            encoding="utf-8"
        ):
            raise SystemExit(
                "Console TypeScript client drift; regenerate from the published OpenAPI"
            )
    subprocess.run(
        [
            node,
            str(modules / "vite/bin/vite.js"),
            "build",
            "--config",
            str(root / "vite.config.ts"),
            str(root),
        ],
        check=True,
    )
    manifest = {
        "node": version,
        "kind": "development-console",
        "lock_sha256": hashlib.sha256(Path("pnpm-lock.yaml").read_bytes()).hexdigest(),
        "artifacts": [
            {
                "file": str(path.relative_to(root / "dist")),
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted((root / "dist").rglob("*"))
            if path.is_file()
        ],
    }
    Path("dist").mkdir(exist_ok=True)
    Path("dist/console-build-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
