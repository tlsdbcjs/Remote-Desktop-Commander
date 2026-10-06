"""Test a selected Windows Agent with owned processes and an isolated blank browser."""

import argparse
import asyncio
import hashlib
import json
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from racp_sdk.artifacts import ArtifactClient, file_digest
from racp_sdk.security import SecretStore, tls_context


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lab-dir", required=True, type=Path)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--portable-zip", required=True, type=Path)
    parser.add_argument("--large-artifact", action="store_true")
    args = parser.parse_args()
    owner = SecretStore(args.lab_dir / "owner.bin").load()
    evidence: dict[str, Any] = {
        "device_id": args.device,
        "started_utc": datetime.now(UTC).isoformat(),
        "steps": [],
        "status": "RUNNING",
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", "utf-8")

    with httpx.Client(
        base_url=owner["gateway"],
        verify=tls_context(args.lab_dir / "ca.pem"),
        headers={"Authorization": "Bearer " + owner["token"]},
        trust_env=False,
        follow_redirects=False,
        timeout=50,
    ) as http:

        def call(
            operation: str,
            payload: dict[str, Any],
            *,
            state: str = "SUCCEEDED",
            timeout_ms: int = 30000,
            error: str | None = None,
        ) -> dict[str, Any]:
            request = {
                "device_id": args.device,
                "operation": operation,
                "payload": payload,
                "idempotency_key": uuid.uuid4().hex,
                "execution_profile_id": "standard",
                "timeout_ms": timeout_ms,
            }
            reply = http.post("/api/v1/operations", json=request)
            if reply.status_code == 409:
                approval = reply.json().get("error", {}).get("details", {}).get("approval_id")
                if approval:
                    approved = http.post("/api/v1/approvals/" + approval + "/approve")
                    approved.raise_for_status()
                    request["approval_id"] = approval
                    reply = http.post("/api/v1/operations", json=request)
            reply.raise_for_status()
            value = reply.json()
            evidence["steps"].append(value)
            save()
            if value.get("state") != state or (
                error and value.get("error", {}).get("code") != error
            ):
                raise RuntimeError("Unexpected remote outcome: " + operation)
            return value

        def shell(command: str) -> dict[str, Any]:
            result = call(
                "shell.exec",
                {
                    "argv": [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); " + command,
                    ]
                },
            )["result"]
            if result["exit_code"] != 0:
                raise RuntimeError("Owned remote verification command failed")
            return result

        device = http.get("/api/v1/devices/" + args.device)
        device.raise_for_status()
        if device.json()["info"].get("status") != "ONLINE":
            raise RuntimeError("Selected PC is offline")
        evidence["connection_epoch"] = device.json()["epoch"]
        try:
            # Read only this product's executable metadata; never read its user credentials.
            command = (
                "$paths=@(Get-Process -Name 'RACP Client' -ErrorAction SilentlyContinue | "
                "ForEach-Object { try { $_.MainModule.FileName } catch {} } | "
                "Select-Object -Unique); "
                "$found=@(foreach($p in $paths) { $a=Join-Path (Split-Path -Parent $p) "
                "'resources/app.asar'; [pscustomobject]@{path=$p; "
                "version=(Get-Item -LiteralPath $p).VersionInfo.ProductVersion; "
                "exe_sha256=(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower(); "
                "asar_sha256=if(Test-Path -LiteralPath $a) "
                "{(Get-FileHash -LiteralPath $a -Algorithm SHA256).Hash.ToLower()} "
                "else {$null} } }); "
                "ConvertTo-Json -InputObject $found -Compress"
            )
            remote = json.loads(shell(command)["stdout"])
            with zipfile.ZipFile(args.portable_zip) as archive:
                expected = {
                    "exe_sha256": hashlib.sha256(archive.read("RACP Client.exe")).hexdigest(),
                    "asar_sha256": hashlib.sha256(archive.read("resources/app.asar")).hexdigest(),
                }
            evidence["release_artifact"] = args.portable_zip.name
            evidence["remote_client_binaries"] = remote
            evidence["current_release_binary_match"] = any(
                all(item.get(key) == checksum for key, checksum in expected.items())
                for item in remote
            )
            save()

            spawned = call(
                "process.spawn",
                {
                    "argv": [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "Start-Sleep -Seconds 90",
                    ]
                },
            )["result"]
            target = {key: spawned[key] for key in ("pid", "create_time", "agent_boot_id")}
            try:
                inspected = call("process.inspect", {"pid": target["pid"]})["result"]
                if inspected["create_time"] != target["create_time"]:
                    raise RuntimeError("Owned process identity differs")
                call("process.tree", target)
                call("process.wait", target, timeout_ms=200, state="TIMED_OUT")
                call("process.inspect", {"pid": target["pid"]})
                call(
                    "process.terminate",
                    {**target, "create_time": target["create_time"] - 1, "force": True},
                    state="FAILED",
                    error="PRECONDITION_FAILED",
                )
                call("process.inspect", {"pid": target["pid"]})
            finally:
                call("process.terminate", {**target, "force": True})
            shell(
                f"if(Get-Process -Id {target['pid']} -ErrorAction SilentlyContinue) {{ exit 3 }}; "
                "'OWNED-PROCESS-GONE'"
            )
            evidence["process_identity_wait_and_cleanup"] = "PASS"

            browser = call("browser.open", {})["result"]
            page = {"browser_id": browser["browser_id"], "page_id": browser["pages"][0]["page_id"]}
            try:

                def observe() -> dict[str, Any]:
                    snapshot = call("browser.snapshot", page)["result"]
                    return {
                        **page,
                        "observation_id": snapshot["observation_id"],
                        "navigation_revision": snapshot["navigation_revision"],
                    }

                marker = "RACP-REMOTE-" + uuid.uuid4().hex
                call(
                    "browser.evaluate",
                    {
                        **observe(),
                        "expression": (
                            "() => { document.title='RACP owned test'; document.body.innerHTML="
                            + json.dumps(
                                "<h1>"
                                + marker
                                + "</h1><label>Name <input data-testid='name'></label>"
                                "<button onclick=\"document.querySelector('#out').textContent="
                                "document.querySelector('input').value\">Submit</button>"
                                "<p id='out'></p>"
                            )
                            + "; return document.title; }"
                        ),
                    },
                )
                snapshot = call("browser.snapshot", page)["result"]
                ref = next(
                    item["ref"] for item in snapshot["elements"] if item.get("test_id") == "name"
                )
                call(
                    "browser.type",
                    {
                        **page,
                        "observation_id": snapshot["observation_id"],
                        "navigation_revision": snapshot["navigation_revision"],
                        "ref": ref,
                        "text": "원격 PC 한글 입력",
                    },
                )
                call(
                    "browser.click",
                    {**observe(), "selector": {"by": "role", "value": "button", "name": "Submit"}},
                )
                result = call(
                    "browser.evaluate",
                    {**observe(), "expression": "() => document.querySelector('#out').textContent"},
                )["result"]
                if result["value"] != "원격 PC 한글 입력":
                    raise RuntimeError("Remote browser form result differs")
                screenshot = call("browser.screenshot", page)["result"]
                response = http.get("/api/v1/artifacts/" + screenshot["artifact_id"] + "/content")
                response.raise_for_status()
                metadata = http.get("/api/v1/artifacts/" + screenshot["artifact_id"])
                metadata.raise_for_status()
                checksum = hashlib.sha256(response.content).hexdigest()
                if (
                    not response.content.startswith(b"\x89PNG\r\n\x1a\n")
                    or checksum != metadata.json()["sha256"]
                ):
                    raise RuntimeError("Remote browser screenshot integrity differs")
                image = args.output.with_suffix(".png")
                image.write_bytes(response.content)
                evidence["browser_form_and_screenshot"] = {
                    "status": "PASS",
                    "path": str(image),
                    "sha256": checksum,
                }
            finally:
                closed = call("browser.close", {"browser_id": browser["browser_id"]})["result"]
                if closed.get("cleanup_status") != "complete":
                    raise RuntimeError("Owned browser cleanup is unconfirmed")
            if args.large_artifact:
                folder = "racp-transfer-test-" + uuid.uuid4().hex
                call("filesystem.mkdir", {"path": folder})
                with tempfile.TemporaryDirectory(prefix="racp-transfer-controller-") as temporary:
                    source = Path(temporary) / "100-mib.bin"
                    block = bytes(range(256)) * 4096
                    with source.open("xb") as stream:
                        for _ in range(100):
                            stream.write(block)
                    size, checksum = file_digest(source)
                    created = http.post(
                        "/api/v1/artifact-transfers",
                        json={
                            "device_id": args.device,
                            "size_bytes": size,
                            "sha256": checksum,
                        },
                    )
                    created.raise_for_status()
                    transfer = created.json()
                    prefix = block * 4
                    upload_url = "/api/v1/artifact-transfers/" + transfer["id"] + "/content"

                    def headers(offset: int) -> dict[str, str]:
                        return {
                            "Authorization": "Bearer " + transfer["credential"],
                            "Content-Range": f"bytes {offset}-{offset + len(prefix) - 1}/{size}",
                            "X-Chunk-SHA256": hashlib.sha256(prefix).hexdigest(),
                        }

                    committed = http.put(upload_url, content=prefix, headers=headers(0))
                    committed.raise_for_status()

                    def interrupted() -> Any:
                        yield prefix[: len(prefix) // 2]
                        raise RuntimeError("owned fixture upload interruption")

                    try:
                        http.put(upload_url, content=interrupted(), headers=headers(len(prefix)))
                    except RuntimeError as failure:
                        if str(failure) != "owned fixture upload interruption":
                            raise
                    else:
                        raise RuntimeError("Transfer disconnect injection did not occur")
                    resumed = http.get("/api/v1/artifact-transfers/" + transfer["id"])
                    resumed.raise_for_status()
                    if int(resumed.json()["committed_bytes"]) != len(prefix):
                        raise RuntimeError("Partial upload changed committed bytes")
                    artifact = asyncio.run(
                        ArtifactClient(
                            owner["gateway"],
                            owner["token"],
                            args.device,
                            ca_file=args.lab_dir / "ca.pem",
                        ).upload(source, transfer_id=transfer["id"])
                    )
                    target_path = folder + "/100-mib.bin"
                    call(
                        "filesystem.write",
                        {"path": target_path, "artifact_id": artifact["id"]},
                        timeout_ms=120000,
                    )
                    remote_hash = call("filesystem.hash", {"path": target_path})["result"]["sha256"]
                    if remote_hash != checksum:
                        raise RuntimeError("Remote 100 MiB file differs")
                    output = call(
                        "filesystem.read", {"path": target_path, "binary": True}, timeout_ms=120000
                    )["result"]
                    content_url = "/api/v1/artifacts/" + output["artifact_id"] + "/content"
                    local = Path(temporary) / "download.bin"
                    cut = 8 * 1024 * 1024
                    with http.stream("GET", content_url) as response:
                        response.raise_for_status()
                        etag = response.headers["etag"]
                        with local.open("xb") as stream:
                            for chunk in response.iter_bytes(65536):
                                stream.write(chunk)
                                if stream.tell() >= cut:
                                    break
                    offset = local.stat().st_size
                    with http.stream(
                        "GET", content_url, headers={"Range": f"bytes={offset}-", "If-Range": etag}
                    ) as response:
                        if response.status_code != 206:
                            raise RuntimeError("Artifact download did not resume with HTTP 206")
                        with local.open("ab") as stream:
                            for chunk in response.iter_bytes(65536):
                                stream.write(chunk)
                    if file_digest(local) != (size, checksum):
                        raise RuntimeError("Resumed 100 MiB download differs")
                    revision = call("filesystem.stat", {"path": target_path})["result"]["revision"]
                    call("filesystem.delete", {"path": target_path, "expected_revision": revision})
                    evidence["large_artifact"] = {
                        "status": "PASS",
                        "size_bytes": size,
                        "sha256": checksum,
                        "upload_disconnect_committed_bytes": len(prefix),
                        "download_resume_offset": offset,
                        "remote_fixture_folder": folder,
                        "remote_large_file_deleted": True,
                        "interruption_scope": (
                            "controller HTTPS transfer; remote Agent stayed connected"
                        ),
                    }
            evidence["status"] = "PASS"
            evidence["completed_utc"] = datetime.now(UTC).isoformat()
            save()
        except BaseException:
            evidence["status"] = "FAIL"
            save()
            raise
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "device": args.device,
                "release_binary_match": evidence["current_release_binary_match"],
                "evidence": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
