import argparse
import asyncio
import getpass
import hashlib
import io
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from racp_domain.models import RACPError
from racp_protocol.registry import INPUT_MODELS, REGISTRY
from racp_sdk.artifacts import ArtifactClient
from racp_sdk.journal import Journal
from racp_sdk.security import SecretStore, digest, require_secure_url, tls_context, token
from racp_sdk.terminal import TerminalStreamClient


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="RACP authenticated management CLI")
    root.add_argument("--gateway", default="http://127.0.0.1:8765")
    root.add_argument(
        "--ca-file", type=Path, help="Additional CA certificate for the Gateway TLS connection"
    )
    root.add_argument("--owner-store", type=Path, default=Path(".racp/owner.bin"))
    root.add_argument("--json", action="store_true")
    groups = root.add_subparsers(dest="group", required=True)
    console = groups.add_parser("console").add_subparsers(dest="action", required=True)
    console.add_parser("setup")
    gateway = groups.add_parser("gateway").add_subparsers(dest="action", required=True)
    init = gateway.add_parser("init")
    init.add_argument("--data-dir", type=Path, default=Path(".racp/gateway"))
    devices = groups.add_parser("device").add_subparsers(dest="action", required=True)
    devices.add_parser("list")
    enroll = devices.add_parser("create-enrollment")
    enroll.add_argument("name")
    enroll.add_argument("--token-output", type=Path, default=Path(".racp/enrollment.bin"))
    revoke = devices.add_parser("revoke")
    revoke.add_argument("device_id")
    agent = groups.add_parser("agent").add_subparsers(dest="action", required=True)
    enrollment = agent.add_parser("enroll")
    enrollment.add_argument("--token-file", type=Path)
    enrollment.add_argument("--token-stdin", action="store_true")
    enrollment.add_argument("--credentials", type=Path, default=Path(".racp/agent/credential.bin"))
    shell = groups.add_parser("shell")
    shell.set_defaults(action="exec")
    shell.add_argument("device_id")
    shell.add_argument("--workspace-id", default="default")
    shell.add_argument("--key", required=True)
    shell.add_argument(
        "--profile", choices=["read_only", "standard", "trusted_personal"], default="read_only"
    )
    shell.add_argument("--timeout-ms", type=int)
    shell.add_argument("--cwd")
    shell.add_argument("--job", action="store_true")
    shell.add_argument("--approval-id")
    shell.add_argument("argv", nargs="+")
    operations = groups.add_parser("operation").add_subparsers(dest="action", required=True)
    for name in ("get", "cancel", "resolutions", "outputs"):
        action = operations.add_parser(name)
        action.add_argument("operation_id")
    jobs = groups.add_parser("job").add_subparsers(dest="action", required=True)
    for name in ("get", "cancel"):
        action = jobs.add_parser(name)
        action.add_argument("job_id")
    listing = jobs.add_parser("list")
    listing.add_argument("--device-id")
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--cursor")
    approvals = groups.add_parser("approval").add_subparsers(dest="action", required=True)
    for name in ("get", "approve", "deny"):
        action = approvals.add_parser(name)
        action.add_argument("approval_id")
    for group, capability in (
        ("fs", "filesystem"),
        ("process", "process"),
        ("terminal", "terminal"),
        ("browser", "browser"),
        ("desktop", "desktop"),
        ("re", "re"),
        ("debugger", "debugger"),
    ):
        actions = groups.add_parser(group).add_subparsers(dest="action", required=True)
        for name, spec in REGISTRY.items():
            if spec.capability != capability:
                continue
            action = actions.add_parser(name.split(".")[1])
            action.set_defaults(operation=name)
            action.add_argument("device_id")
            action.add_argument("--workspace-id", default="default")
            action.add_argument("--key", required=spec.side_effect)
            action.add_argument(
                "--profile",
                choices=["read_only", "standard", "trusted_personal"],
                default="read_only",
            )
            action.add_argument("--timeout-ms", type=int)
            action.add_argument("--job", action="store_true")
            action.add_argument("--approval-id")
            if getattr(INPUT_MODELS[name], "__pydantic_root_model__", False):
                action.add_argument("--payload-json", type=json.loads, required=True)
                continue
            properties = INPUT_MODELS[name].model_json_schema()["properties"]
            for field_name, field in INPUT_MODELS[name].model_fields.items():
                if name == "process.spawn" and field_name in {"command", "shell", "mode"}:
                    continue
                if field_name == "argv":
                    if capability == "terminal":
                        action.add_argument("--argv", type=json.loads)
                    else:
                        action.add_argument("argv", nargs="+")
                    continue
                if field_name == "artifact_id" and name == "filesystem.write":
                    continue
                if field_name == "content":
                    source = action.add_mutually_exclusive_group(required=True)
                    source.add_argument("--content")
                    source.add_argument("--content-file", type=Path)
                    source.add_argument("--artifact-id")
                    continue
                schema = properties[field_name]
                kind = schema.get("type")
                if not kind:
                    kind = next(
                        (
                            item.get("type")
                            for item in schema.get("anyOf", [])
                            if item.get("type") != "null"
                        ),
                        "string",
                    )
                positional = field_name in {"path", "source", "destination", "pid", "handle_id"}
                option = field_name if positional else "--" + field_name.replace("_", "-")
                settings: dict[str, Any] = {}
                if name == "browser.key" and field_name == "key":
                    option = "--press-key"
                    settings["dest"] = "press_key"
                if not positional:
                    settings["default"] = field.get_default(call_default_factory=True)
                    if field.is_required():
                        settings["required"] = True
                if kind == "boolean":
                    settings["action"] = (
                        argparse.BooleanOptionalAction
                        if settings.get("default") is True
                        else "store_true"
                    )
                elif kind == "integer":
                    settings["type"] = int
                elif kind == "number":
                    settings["type"] = float
                elif kind in {"object", "array"} or field_name == "selector":
                    settings["type"] = json.loads
                if "enum" in schema:
                    settings["choices"] = schema["enum"]
                action.add_argument(option, **settings)
        if capability == "terminal":
            stream = actions.add_parser("stream")
            stream.add_argument("device_id")
            stream.add_argument("handle_id")
            stream.add_argument("--cursor", default="0")
            stream.add_argument("--max-bytes", type=int, default=65536)
            stream.add_argument("--window-bytes", type=int, default=256 * 1024)
    artifact = groups.add_parser("artifact").add_subparsers(dest="action", required=True)
    artifact_get = artifact.add_parser("get")
    artifact_get.add_argument("artifact_id")
    upload = artifact.add_parser("upload")
    upload.add_argument("device_id")
    upload.add_argument("source", type=Path)
    upload.add_argument("--resume-transfer-id")
    upload.add_argument("--media-type", default="application/octet-stream")
    download = artifact.add_parser("download")
    download.add_argument("artifact_id")
    download.add_argument("output", type=Path)
    download.add_argument("--overwrite", action="store_true")
    groups.add_parser("doctor").set_defaults(action="doctor")
    return root


def request(
    args: argparse.Namespace,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    authenticated: bool = True,
) -> dict[str, Any]:
    require_secure_url(args.gateway)
    headers: dict[str, str] = {}
    if authenticated:
        headers["Authorization"] = "Bearer " + SecretStore(args.owner_store).load()["token"]
    with httpx.Client(
        timeout=max(40, (getattr(args, "timeout_ms", None) or 30000) / 1000 + 10),
        follow_redirects=False,
        verify=tls_context(getattr(args, "ca_file", None)) or True,
    ) as client:
        response = client.request(
            method, args.gateway.rstrip("/") + path, json=body, headers=headers
        )
        result: dict[str, Any] = response.json()
        if response.status_code >= 400:
            error = result.get("error", {})
            raise RACPError(
                error.get("code", "INVALID_ARGUMENT"),
                error.get("message", "request rejected"),
                layer=error.get("layer", "gateway"),
                execution_state=error.get("execution_state", "not_started"),
                **error.get("details", {}),
            )
        return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.group == "gateway":
        db = Journal(args.data_dir / "gateway.db")
        try:
            db.db.execute(
                "CREATE TABLE IF NOT EXISTS owner "
                "(id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL)"
            )
            with db.transaction():
                if db.db.execute("SELECT 1 FROM owner").fetchone():
                    raise RACPError("CONFLICT", "owner is already initialized")
                secret = token()
                SecretStore(args.owner_store).save({"token": secret, "gateway": args.gateway})
                db.db.execute("INSERT INTO owner VALUES ('owner_local', ?)", (digest(secret),))
        finally:
            db.close()
        return {
            "owner": "owner_local",
            "secret_store": str(args.owner_store),
            "state": "initialized",
        }
    if args.group == "device":
        if args.action == "list":
            return request(args, "GET", "/api/v1/devices")
        if args.action == "revoke":
            return request(args, "POST", f"/api/v1/devices/{args.device_id}/revoke")
        result = request(args, "POST", "/api/v1/enrollment-tokens", {"name": args.name})
        SecretStore(args.token_output).save({"token": result.pop("token"), "gateway": args.gateway})
        return {**result, "token_store": str(args.token_output)}
    if args.group == "agent":
        secret = (
            SecretStore(args.token_file).load()["token"]
            if args.token_file
            else sys.stdin.readline().strip()
            if args.token_stdin
            else getpass.getpass("Enrollment token: ")
        )
        result = request(args, "POST", "/agent/v1/enroll", {"token": secret}, authenticated=False)
        saved = {**result, "gateway": args.gateway}
        if getattr(args, "ca_file", None) is not None:
            saved["ca_file"] = str(args.ca_file.resolve(strict=True))
        SecretStore(args.credentials).save(saved)
        return {"device_id": result["device_id"], "credential_store": str(args.credentials)}
    if args.group == "shell":
        argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
        if not argv:
            raise RACPError("INVALID_ARGUMENT", "specify -- followed by argv")
        return request(
            args,
            "POST",
            "/api/v1/operations",
            {
                "device_id": args.device_id,
                "operation": "shell.exec",
                "workspace_id": args.workspace_id,
                "payload": {"mode": "argv", "argv": argv, "cwd": args.cwd},
                "idempotency_key": args.key,
                "timeout_ms": args.timeout_ms,
                "execution_profile_id": args.profile,
                "approval_id": args.approval_id,
                "execution_mode": "job" if args.job else "sync",
            },
        )
    if args.group in {"fs", "process", "terminal", "browser", "desktop", "re", "debugger"}:
        if args.group == "terminal" and args.action == "stream":

            async def follow() -> dict[str, Any]:
                from contextlib import aclosing

                client = TerminalStreamClient(
                    args.gateway,
                    SecretStore(args.owner_store).load()["token"],
                    ca_file=getattr(args, "ca_file", None),
                )
                last_cursor = args.cursor
                events = client.events(
                    args.device_id,
                    args.handle_id,
                    cursor=args.cursor,
                    max_bytes=args.max_bytes,
                    window_bytes=args.window_bytes,
                )
                async with aclosing(events):
                    async for event in events:
                        print(json.dumps(event, ensure_ascii=False), flush=True)
                        if event["type"] == "stream_data":
                            last_cursor = event["next_cursor"]
                return {
                    "state": "COMPLETED",
                    "handle_id": args.handle_id,
                    "last_cursor": last_cursor,
                }

            return asyncio.run(follow())
        payload = {
            name: getattr(
                args, "press_key" if args.operation == "browser.key" and name == "key" else name
            )
            for name in INPUT_MODELS[args.operation].model_fields
            if hasattr(
                args, "press_key" if args.operation == "browser.key" and name == "key" else name
            )
        }
        if hasattr(args, "payload_json"):
            payload = args.payload_json
        if hasattr(args, "content_file") and args.content_file:
            with args.content_file.open(encoding="utf-8", newline="") as source:
                payload["content"] = source.read(65537)
        return request(
            args,
            "POST",
            "/api/v1/operations",
            {
                "device_id": args.device_id,
                "operation": args.operation,
                "workspace_id": args.workspace_id,
                "payload": payload,
                "idempotency_key": args.key,
                "execution_profile_id": args.profile,
                "timeout_ms": args.timeout_ms,
                "execution_mode": "job" if args.job else "sync",
                "approval_id": args.approval_id,
            },
        )
    if args.group == "artifact":
        if args.action == "upload":
            identity = SecretStore(args.owner_store).load()["token"]
            transfer_client = ArtifactClient(
                args.gateway, identity, args.device_id, ca_file=getattr(args, "ca_file", None)
            )
            return asyncio.run(
                transfer_client.upload(
                    args.source, transfer_id=args.resume_transfer_id, media_type=args.media_type
                )
            )
        metadata = request(args, "GET", f"/api/v1/artifacts/{args.artifact_id}")
        if args.action == "get":
            return metadata
        target = args.output.absolute()
        if target.exists() and not args.overwrite:
            raise RACPError("CONFLICT", "output exists; overwrite must be explicit")
        temporary = target.with_name(target.name + "." + secrets.token_hex(16) + ".partial")
        hasher = hashlib.sha256()
        total = 0
        try:
            headers = {"Authorization": "Bearer " + SecretStore(args.owner_store).load()["token"]}
            with (
                httpx.Client(
                    timeout=30,
                    follow_redirects=False,
                    verify=tls_context(getattr(args, "ca_file", None)) or True,
                ) as client,
                client.stream(
                    "GET",
                    args.gateway.rstrip("/") + f"/api/v1/artifacts/{args.artifact_id}/content",
                    headers=headers,
                ) as response,
                temporary.open("xb") as output,
            ):
                response.raise_for_status()
                for chunk in response.iter_bytes(65536):
                    total += len(chunk)
                    if total > metadata["size_bytes"]:
                        raise RACPError("CHECKSUM_MISMATCH", "download size exceeds metadata")
                    hasher.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if total != metadata["size_bytes"] or hasher.hexdigest() != metadata["sha256"]:
                raise RACPError("CHECKSUM_MISMATCH", "download hash/size mismatch")
            if args.overwrite:
                os.replace(temporary, target)
            elif os.name == "nt":
                os.rename(temporary, target)
            else:
                os.link(temporary, target)
                temporary.unlink()
            return {
                "artifact_id": args.artifact_id,
                "output": str(target),
                "sha256": hasher.hexdigest(),
                "size_bytes": total,
            }
        finally:
            temporary.unlink(missing_ok=True)
    if args.group == "console":
        return request(args, "POST", "/api/v1/console/setup-token")
    if args.group == "operation":
        path = f"/api/v1/operations/{args.operation_id}"
        if args.action in {"resolutions", "outputs"}:
            return request(args, "GET", path + "/" + args.action)
        return request(
            args,
            "GET" if args.action == "get" else "POST",
            path if args.action == "get" else path + "/cancel",
        )
    if args.group == "job":
        if args.action == "list":
            query = {
                name: getattr(args, name)
                for name in ("device_id", "limit", "cursor")
                if getattr(args, name) is not None
            }
            return request(args, "GET", "/api/v1/jobs?" + urlencode(query))
        path = f"/api/v1/jobs/{args.job_id}"
        return request(
            args,
            "GET" if args.action == "get" else "POST",
            path if args.action == "get" else path + "/cancel",
        )
    if args.group == "approval":
        path = f"/api/v1/approvals/{args.approval_id}"
        return request(
            args,
            "GET" if args.action == "get" else "POST",
            path if args.action == "get" else path + "/" + args.action,
        )
    return {
        "gateway": request(args, "GET", "/readyz"),
        "devices": request(args, "GET", "/api/v1/devices"),
        "diagnostics": request(args, "GET", "/api/v1/doctor"),
        "unverified": [
            "Linux",
            "Windows Service/Broker",
            "Console",
            "Browser",
            "RE",
            "Codex/ChatGPT host",
        ],
    }


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        result = run(args)
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=None if args.group == "terminal" and args.action == "stream" else 2,
            )
        )
        if result.get("state") in {"UNKNOWN", "RECONCILING"}:
            raise SystemExit(4)
        if result.get("state") in {"FAILED", "TIMED_OUT", "CANCELLED"}:
            raise SystemExit(1)
    except RACPError as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": exc.error.code,
                        "message": exc.error.message,
                        "details": exc.error.details,
                    }
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        code = exc.error.code
        raise SystemExit(
            5
            if code == "APPROVAL_REQUIRED"
            else 3
            if code in {"UNAUTHENTICATED", "PERMISSION_DENIED", "DEVICE_REVOKED"}
            else 4
            if code in {"DEVICE_OFFLINE", "EXECUTION_UNKNOWN", "TRANSPORT_ERROR"}
            else 2
            if code == "INVALID_ARGUMENT"
            else 1
        ) from exc
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "TRANSPORT_ERROR",
                        "message": "connection or secure store unavailable",
                    }
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(4) from exc
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
