"""Acquire the pinned official portable Ghidra release in the workspace only."""

import argparse
import hashlib
import json
import stat
import urllib.request
import zipfile
from pathlib import Path

VERSION = "12.1.4"
NAME = "ghidra_12.1.4_PUBLIC"
ARCHIVE = "ghidra_12.1.4_PUBLIC_20260921.zip"
SHA256 = "ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db"
URL = (
    "https://github.com/NationalSecurityAgency/ghidra/releases/download/"
    "Ghidra_12.1.4_build/" + ARCHIVE
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".tools"))
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / ARCHIVE
    if not archive.exists():
        partial = archive.with_suffix(".download")
        request = urllib.request.Request(URL, headers={"User-Agent": "RACP-development"})
        with urllib.request.urlopen(request, timeout=30) as response, partial.open("xb") as output:
            while piece := response.read(1024 * 1024):
                output.write(piece)
        partial.replace(archive)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != SHA256:
        raise ValueError("Ghidra archive differs from the official release digest")
    installation = root / NAME
    if not (installation / "Ghidra/application.properties").is_file():
        if installation.exists():
            raise FileExistsError("refusing to extract over an existing Ghidra directory")
        with zipfile.ZipFile(archive) as bundle:
            if sum(i.file_size for i in bundle.infolist()) > 4 * 1024**3:
                raise ValueError("Ghidra extraction size exceeds the pinned release budget")
            for item in bundle.infolist():
                destination = (root / item.filename).resolve()
                if (
                    not destination.is_relative_to(installation)
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or "\\" in item.filename
                    or ":" in item.filename
                ):
                    raise ValueError("unsafe Ghidra archive member")
            bundle.extractall(root)
    (root / "ghidra-release-manifest.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "url": URL,
                "sha256": actual,
                "installation": str(installation),
                "kind": "external-development-backend",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(installation)


if __name__ == "__main__":
    main()
