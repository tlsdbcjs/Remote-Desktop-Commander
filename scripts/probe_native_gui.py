"""Isolated native GUI fixture and PID-scoped UIA probe; no existing profiles."""

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from racp_domain.version import VERSION


def prepare(root: Path, payload_source: Path | None) -> None:
    import secrets

    import httpx
    from racp_gateway.store import GatewayStore
    from racp_sdk.security import digest
    from test_native_agent import tls_files

    if payload_source is None:
        raise ValueError("--payload-source must name a verified native staging payload")
    payload_source = payload_source.resolve()
    manifest = json.loads((payload_source / "payload-manifest.json").read_text(encoding="utf-8"))
    if manifest["product_version"] != VERSION:
        raise ValueError("GUI fixture payload version does not match the workspace")
    root.mkdir(parents=True, exist_ok=False)
    payload = root / "payload"
    shutil.copytree(payload_source, payload)
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "gui-read.txt").write_text("GUI-approval-한글-fixture", encoding="utf-8")
    gateway_dir = root / "gateway"
    gateway_dir.mkdir()
    owner = secrets.token_urlsafe(32)
    (root / "owner.key").write_text(owner)
    store = GatewayStore(gateway_dir / "gateway.db")
    store.initialize(digest(owner))
    store.close()
    tls_files(root)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    with (root / "gateway.log").open("w", encoding="utf-8") as log:
        gateway = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "racp_gateway.main",
                "--data-dir",
                str(gateway_dir),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--public-origin",
                origin,
                "--tls-cert",
                str(root / "test-server.pem"),
                "--tls-key",
                str(root / "test-tls.key"),
                "--client-ca-file",
                str(root / "test-ca.pem"),
                "--enable-trusted-personal",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    (root / "gateway.pid").write_text(str(gateway.pid))
    (root / "origin.txt").write_text(origin)
    with httpx.Client(
        base_url=origin,
        verify=str(root / "test-ca.pem"),
        trust_env=False,
        headers={"Authorization": "Bearer " + owner},
        timeout=10,
    ) as client:
        deadline = time.monotonic() + 20
        while True:
            try:
                if client.get("/readyz").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if gateway.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("GUI fixture Gateway failed to start")
            time.sleep(0.1)
        response = client.post(
            "/api/v1/enrollment-tokens",
            json={"name": "native-gui", "include_connection_file": True},
        )
        response.raise_for_status()
        (root / "connection.json").write_text(
            json.dumps(response.json()["connection_file"]), encoding="utf-8"
        )
    process = subprocess.Popen(
        [str(payload / "racp-client.exe"), "--state-dir", str(root / "native-state")]
    )
    (root / "client.pid").write_text(str(process.pid))
    print(json.dumps({"client_pid": process.pid, "gateway_pid": gateway.pid, "root": str(root)}))


def request(root: Path, label: str | None = None) -> None:
    import httpx

    state = json.loads((root / "native-state/native-state.json").read_text())
    with httpx.Client(
        base_url=(root / "origin.txt").read_text(),
        verify=str(root / "test-ca.pem"),
        trust_env=False,
        headers={"Authorization": "Bearer " + (root / "owner.key").read_text()},
        timeout=60,
    ) as client:
        response = client.post(
            "/api/v1/operations",
            json={
                "device_id": state["device_id"],
                "operation": "filesystem.read",
                "payload": {"path": "gui-read.txt"},
                "execution_profile_id": "trusted_personal",
                "idempotency_key": "gui-read-" + str(time.time_ns()),
            },
        )
        value = {"status": response.status_code, "response": response.json()}
        (root / ((label or "remote-read-result") + ".json")).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(value, ensure_ascii=False))


def probe(args: argparse.Namespace, root: Path) -> None:
    from pywinauto import Application

    pid = int((root / "client.pid").read_text())
    app = Application(backend="uia").connect(process=pid, timeout=10)
    window = app.window(title=f"RACP Native Client v{VERSION}")
    wrapper = window.wrapper_object()
    if args.action in {"invoke", "focus", "set", "type"}:
        matches = [
            e
            for e in wrapper.descendants()
            if e.window_text() == args.name
            and (not args.role or e.element_info.control_type == args.role)
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one observed control, found {len(matches)}")
        control = matches[0]
        if args.action == "invoke":
            control.invoke()
        elif args.action == "focus":
            control.set_focus()
        elif args.action == "set":
            control.iface_value.SetValue(args.value)
        else:
            if not control.has_keyboard_focus():
                raise RuntimeError("Target does not have keyboard focus")
            control.type_keys(args.value, with_spaces=True)
    elif args.action == "keys":
        wrapper.type_keys(args.value)
    time.sleep(0.3)
    if not window.exists(timeout=0):
        value = {"client_pid": pid, "window_closed": True}
        if args.capture:
            (root / (args.capture + ".json")).write_text(json.dumps(value), encoding="utf-8")
        print(json.dumps(value))
        return
    wrapper = window.wrapper_object()
    data = []
    for control in wrapper.descendants():
        item = {
            "name": control.window_text(),
            "role": control.element_info.control_type,
            "enabled": control.is_enabled(),
            "focused": control.has_keyboard_focus(),
            "rectangle": str(control.rectangle()),
        }
        try:
            item["value"] = control.iface_value.CurrentValue
        except Exception:
            pass
        data.append(item)
    if args.capture:
        wrapper.capture_as_image().save(root / (args.capture + ".png"))
        (root / (args.capture + ".json")).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.name:
        observed = [item for item in data if args.name in item["name"] or item["focused"]]
    else:
        observed = [
            item
            for item in data
            if item["role"] in {"Text", "Edit", "ComboBox", "CheckBox"}
            and item["rectangle"] != "(L0, T0, R0, B0)"
        ]
    print(
        json.dumps(
            {"element_count": len(data), "observed": observed[:25]}, ensure_ascii=False, indent=2
        )
    )


def decision(args: argparse.Namespace, root: Path) -> None:
    """Respond through the live GUI within the Agent's 30-second prompt lifetime."""
    from pywinauto import Application

    pid = int((root / "client.pid").read_text())
    app = Application(backend="uia").connect(process=pid, timeout=10)
    window = app.window(title=f"RACP Native Client v{VERSION}")
    suffix = "이번 요청 허용" if args.action == "gui-allow" else "거부"
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(request, root, (args.capture or args.action) + "-result")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            matches = [
                item
                for item in window.descendants()
                if item.element_info.control_type == "Button"
                and item.window_text().startswith("filesystem.read · 요청 ")
                and item.window_text().endswith(" · " + suffix)
            ]
            if len(matches) == 1:
                break
            if pending.done():
                pending.result()
                raise RuntimeError("Request finished without a GUI approval prompt")
            time.sleep(0.1)
        else:
            raise RuntimeError("Live GUI approval prompt was not observed")
        control = matches[0]
        label = args.capture or args.action
        window.capture_as_image().save(root / (label + "-pending.png"))
        (root / (label + "-control.json")).write_text(
            json.dumps(
                {"name": control.window_text(), "enabled": control.is_enabled()}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        control.set_focus()
        focus_deadline = time.monotonic() + 2
        while not control.has_keyboard_focus() and time.monotonic() < focus_deadline:
            time.sleep(0.05)
        if not control.has_keyboard_focus():
            raise RuntimeError("Approval button did not receive keyboard focus")
        control.type_keys("{SPACE}")
        pending.result(timeout=40)
    args.action = "inspect"
    probe(args, root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "action",
        choices=[
            "prepare",
            "inspect",
            "invoke",
            "focus",
            "set",
            "type",
            "keys",
            "request",
            "gui-allow",
            "gui-deny",
        ],
    )
    parser.add_argument("--name")
    parser.add_argument("--role")
    parser.add_argument("--value")
    parser.add_argument("--capture")
    parser.add_argument("--payload-source", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        prepare(root, args.payload_source)
    elif args.action == "request":
        request(root, args.capture)
    elif args.action in {"gui-allow", "gui-deny"}:
        decision(args, root)
    else:
        probe(args, root)


if __name__ == "__main__":
    main()
