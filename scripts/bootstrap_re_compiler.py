"""Pinned portable compiler for known-source RE fixtures; never modifies system PATH."""

import argparse
import hashlib
import json
import stat
import urllib.request
import zipfile
from pathlib import Path

VERSION = "20260908"
NAME = f"llvm-mingw-{VERSION}-ucrt-x86_64"
SHA256 = "1bcf74d06b724aeecaa6412ca85f5b26fb1da770e7cdcefa9263c9c5c3ad34b6"
URL = f"https://github.com/mstorsjo/llvm-mingw/releases/download/{VERSION}/{NAME}.zip"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".tools"))
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / (NAME + ".zip")
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=30) as response, archive.open("xb") as output:
            while piece := response.read(1024 * 1024):
                output.write(piece)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != SHA256:
        raise ValueError("portable RE compiler archive differs from release digest")
    compiler = root / NAME / "bin/clang.exe"
    if not compiler.is_file():
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                destination = (root / item.filename).resolve()
                if (
                    not destination.is_relative_to(root / NAME)
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or "\\" in item.filename
                    or ":" in item.filename
                ):
                    raise ValueError("unsafe compiler archive member")
            bundle.extractall(root)
    (root / "re-compiler-manifest.json").write_text(
        json.dumps(
            {"version": VERSION, "url": URL, "sha256": actual, "compiler": str(compiler)}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    print(compiler)


if __name__ == "__main__":
    main()
