"""Publish a verified portable ZIP from an already packaged desktop directory."""

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


def sha256(stream: BinaryIO) -> str:
    value = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        value.update(chunk)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or source in output.parents:
        raise ValueError("Use a new ZIP outside the packaged source directory")
    manifest = json.loads((source / "resources/agent/agent-manifest.json").read_text("utf-8"))
    expected = {"resources/agent/" + entry["file"]: entry["sha256"] for entry in manifest["files"]}
    for name in ("RACP Client.exe", "resources/app.asar"):
        with (source / name).open("rb") as stream:
            expected[name] = sha256(stream)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid4().hex + ".partial")
    with zipfile.ZipFile(temporary, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                if file.is_symlink():
                    raise ValueError("Windows portable ZIP cannot contain symlinks")
                archive.write(file, file.relative_to(source).as_posix())
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError("Portable ZIP failed CRC verification")
        for name, checksum in expected.items():
            with archive.open(name) as stream:
                if sha256(stream) != checksum:
                    raise ValueError("Portable ZIP content differs from the packaged manifest")
    # Exclusive publish: preserve another concurrent producer's result.
    if os.name == "nt":
        os.rename(temporary, output)
    else:
        os.link(temporary, output)
        temporary.unlink()
    with output.open("rb") as stream:
        checksum = sha256(stream)
    print(
        json.dumps(
            {
                "zip": str(output),
                "size": output.stat().st_size,
                "sha256": checksum,
                "verified_files": len(expected),
                "crc": "passed",
            }
        )
    )


if __name__ == "__main__":
    main()
