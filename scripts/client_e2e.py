"""Test the native desktop client against an isolated real Gateway."""

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import socket
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psutil
import uvicorn
from racp_domain.version import VERSION
from racp_gateway.app import create_app
from racp_gateway.store import GatewayStore
from racp_sdk.connection_file import ConnectionFile
from racp_sdk.security import digest, tls_context, token


async def run(node: Path, backend: Path, screenshot: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="racp-desktop-") as temporary:
        root = await asyncio.to_thread(Path(temporary).resolve)
        workspace = root / "자료 workspace"
        workspace.mkdir()
        owner = token()
        store = GatewayStore(root / "gateway/gateway.db")
        store.initialize(digest(owner))
        expired = store.enrollment("Expired client fixture", ttl_seconds=-1)
        store.close()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        spec = importlib.util.spec_from_file_location(
            "racp_tls_fixture", Path("tests/tls_fixture.py")
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("TLS test fixture unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ca, cert, key = module.certificates(root / "tls")
        ca_pem = await asyncio.to_thread(ca.read_text, encoding="utf-8")
        gateway = f"https://127.0.0.1:{port}"
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(
                    root / "gateway",
                    trusted_personal=True,
                    public_origin=gateway,
                    client_ca_pem=ca_pem,
                ),
                log_level="error",
                lifespan="on",
                ws_per_message_deflate=False,
                ssl_certfile=str(cert),
                ssl_keyfile=str(key),
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            if not server.started:
                raise RuntimeError("Desktop fixture Gateway did not start")
            async with httpx.AsyncClient(
                base_url=gateway,
                verify=tls_context(ca),
                headers={"Authorization": "Bearer " + owner},
            ) as http:
                ticket = await http.post(
                    "/api/v1/enrollment-tokens",
                    json={
                        "name": "Desktop connection file fixture",
                        "include_connection_file": True,
                    },
                )
                ticket.raise_for_status()
                issued = ticket.json()
            connection_path = root / "RACP-connection.racp"
            await asyncio.to_thread(
                connection_path.write_text, json.dumps(issued["connection_file"]), encoding="utf-8"
            )
            expired_path = root / "expired.racp"
            await asyncio.to_thread(
                expired_path.write_text,
                ConnectionFile(
                    gateway=gateway,
                    token=expired,
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                    ca_pem=ca_pem,
                ).model_dump_json(),
                encoding="utf-8",
            )

            def run_client_test(*arguments, **options):
                data = options.pop("input")
                deadline = options.pop("timeout")
                with subprocess.Popen(*arguments, stdin=subprocess.PIPE, **options) as process:
                    try:
                        process.communicate(input=data, timeout=deadline)
                    except subprocess.TimeoutExpired:
                        # Retire only the process tree launched for this isolated fixture.
                        children = psutil.Process(process.pid).children(recursive=True)
                        for child in reversed(children):
                            try:
                                child.terminate()
                            except psutil.NoSuchProcess:
                                pass
                        process.kill()
                        process.wait(timeout=10)
                        psutil.wait_procs(children, timeout=5)
                        raise RuntimeError(
                            "Owned Client E2E exceeded its total test budget"
                        ) from None
                    return process

            completed = await asyncio.to_thread(
                run_client_test,
                [str(node), "apps/client/tests/e2e.cjs"],
                input=json.dumps(
                    {
                        "gateway": gateway,
                        "owner": owner,
                        "token": issued["token"],
                        "expired_token": expired,
                        "connection_path": str(connection_path),
                        "expired_connection_path": str(expired_path),
                        "ca": str(ca),
                        "workspace": str(workspace),
                        "state": str(root / "state"),
                        "backend": str(backend),
                        "screenshot": str(screenshot),
                    }
                ),
                text=True,
                encoding="utf-8",
                timeout=600,
                env=dict(os.environ, NODE_EXTRA_CA_CERTS=str(ca)),
            )
            return completed.returncode
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 15)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True, type=Path)
    target = {"Windows": "win", "Darwin": "mac", "Linux": "linux"}[platform.system()]
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}[
        platform.machine().lower()
    ]
    parser.add_argument(
        "--backend", type=Path, default=Path("dist/client-agent") / VERSION / f"{target}-{arch}"
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                args.node.absolute(),
                args.backend.absolute(),
                Path("dist/client-desktop-e2e.png").absolute(),
            )
        )
    )


if __name__ == "__main__":
    main()
