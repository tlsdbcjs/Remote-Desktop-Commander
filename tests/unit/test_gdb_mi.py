import hashlib
from pathlib import Path

import pytest
from racp_agent.plugins.gdb_mi import parse_record
from racp_agent.plugins.gdb_plugin import GDBPlugin
from racp_domain.models import RACPError


def test_mi_token_nested_lists_repeated_frame_entries_and_c_string_escapes() -> None:
    record = parse_record(
        b'27^done,stack=[frame={level="0",addr="0x140001000",func="main"},frame={level="1",addr="0x140002000",func="callee"}]\r\n'
    )
    assert record.token == "27" and record.kind == "^" and record.name == "done"
    assert record.data["stack"][1]["frame"]["func"] == "callee"
    assert parse_record(b'~"RACP\\n\\342\\234\\223"\n').data["text"] == "RACP\n✓"
    memory = parse_record(
        b'2^done,memory=[{begin="0x1000",offset="0x0",end="0x1002",contents="aabb"}]\n'
    )
    assert memory.data["memory"][0]["contents"] == "aabb"


@pytest.mark.parametrize(
    "raw",
    [
        b'1^done,x="a",x="b"\n',
        b'1^done,x={a="1",a="2"}\n',
        b'1^done,x="unterminated\n',
        b'1^done,x=["1" garbage]\n',
        b'1^done,x="\\q"\n',
        b"x" * (1024 * 1024 + 1),
    ],
    ids=["duplicate-result", "duplicate-tuple", "unterminated", "separator", "escape", "oversized"],
)
def test_mi_rejects_ambiguous_or_unbounded_records(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_record(raw)


def test_backend_replacement_requires_explicit_new_hash_approval(tmp_path: Path) -> None:
    binary = tmp_path / "gdb.exe"
    binary.write_bytes(b"approved backend")
    approved = hashlib.sha256(binary.read_bytes()).hexdigest()
    plugin = GDBPlugin(tmp_path / "manifest.json", binary, approved)
    plugin.verify_backend()
    binary.write_bytes(b"same version but replaced backend")
    with pytest.raises(RACPError) as error:
        plugin.verify_backend()
    assert error.value.error.code == "PLUGIN_VERSION_MISMATCH"
