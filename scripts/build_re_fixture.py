"""Build the small, known-source fixture with symbols and record reproducible inputs."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("dist/re-fixture"))
    args = parser.parse_args()
    source = Path("tests/fixtures/re_program.c").resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / ("racp-fixture.exe" if os.name == "nt" else "racp-fixture")
    argv = [
        str(args.compiler.resolve(strict=True)),
        "-g",
        "-gdwarf-4",
        "-O0",
        str(source),
        "-o",
        str(target),
    ]
    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    subprocess.run(argv, check=True, timeout=60, **kwargs)
    version = subprocess.check_output([argv[0], "--version"], timeout=10, **kwargs).decode(
        errors="replace"
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "source": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "target": str(target),
                "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "compiler": argv[0],
                "compiler_version": version,
                "argv": argv,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()
