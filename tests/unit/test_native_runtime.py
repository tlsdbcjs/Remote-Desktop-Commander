import hashlib
import json
import os
import struct
from pathlib import Path

import pytest
from pydantic import ValidationError
from racp_agent.native_runtime import ManagedNativeRuntime, NativeRuntimeManifest
from racp_domain.models import RACPError


def prepare(root: Path) -> str:
    image = bytearray(512)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 128)
    image[128:132] = b"PE\0\0"
    struct.pack_into("<H", image, 132, 0x8664)
    files = {}
    for name in ("cdb.exe", "dbgeng.dll", "dbghelp.dll"):
        (root / name).write_bytes(image)
        files[name] = hashlib.sha256(image).hexdigest()
    manifest = {"version": 1, "engine": "cdb-win-x64", "engine_version": "10.0.1.0", "files": files}
    raw = json.dumps(manifest).encode()
    (root / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 1.0},
        {"engine": "cmd"},
        {"command": "cmd.exe"},
        {"engine_version": ""},
        {"files": {"../cdb.exe": "a" * 64}},
        {"files": {"cdb.exe": "a" * 64}},
        {"files": {"cdb.exe": "??", "dbgeng.dll": "a" * 64, "dbghelp.dll": "a" * 64}},
        {
            "files": {
                "cdb.exe": "a" * 64,
                "CDB.EXE": "a" * 64,
                "dbgeng.dll": "a" * 64,
                "dbghelp.dll": "a" * 64,
            }
        },
    ],
)
def test_manifest_rejects_unpinned_or_ambiguous_runtime(tmp_path, change) -> None:
    prepare(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    with pytest.raises(ValidationError):
        NativeRuntimeManifest.model_validate({**manifest, **change})


@pytest.mark.skipif(os.name != "nt", reason="Windows pinned-handle proof")
def test_pin_prevents_component_write_until_release_and_fixed_reverse_recipe(tmp_path) -> None:
    digest = prepare(tmp_path)
    runtime = ManagedNativeRuntime(tmp_path, digest)
    with runtime.pin() as pinned:
        command = pinned.reverse_command(1234, 50123, tmp_path)
        assert command[:3] == [
            str(tmp_path / "cdb.exe"),
            "-server",
            "tcp:port=50123,clicon=127.0.0.1",
        ]
        assert "-pd" in command and "-noshell" in command and "-noinh" in command
        assert "0.0.0.0" not in " ".join(command)
        with pytest.raises(OSError):
            (tmp_path / "dbgeng.dll").write_bytes(b"changed")
    (tmp_path / "dbgeng.dll").write_bytes(b"changed")
    with pytest.raises(RACPError):
        with runtime.pin():
            pass


@pytest.mark.skipif(os.name != "nt", reason="Windows runtime proof")
@pytest.mark.parametrize("damage", ["manifest", "component", "extra", "architecture"])
def test_runtime_fails_closed_before_command_on_integrity_mismatch(tmp_path, damage) -> None:
    digest = prepare(tmp_path)
    if damage == "manifest":
        (tmp_path / "manifest.json").write_text("{}")
    elif damage == "component":
        (tmp_path / "cdb.exe").write_bytes(b"other executable")
    elif damage == "extra":
        (tmp_path / "unlisted.dll").write_bytes(b"extra")
    else:
        path = tmp_path / "cdb.exe"
        image = bytearray(path.read_bytes())
        struct.pack_into("<H", image, 132, 0x14C)
        path.write_bytes(image)
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        manifest["files"]["cdb.exe"] = hashlib.sha256(image).hexdigest()
        raw = json.dumps(manifest).encode()
        (tmp_path / "manifest.json").write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(RACPError):
        with ManagedNativeRuntime(tmp_path, digest).pin():
            raise AssertionError("invalid runtime reached execution recipe")


def test_extension_must_be_manifested_and_is_not_arbitrary_command(tmp_path) -> None:
    prepare(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    for extension in ("exts.dll", "other.dll", "cmd.exe"):
        with pytest.raises(ValidationError):
            NativeRuntimeManifest.model_validate({**manifest, "extension": extension})
