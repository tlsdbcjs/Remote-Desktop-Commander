"""Exercise Codex's OAuth client against the owned local PKCE/consent fixture."""

import html
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from racp_sdk.security import SecretStore, tls_context


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    lab = repository / ".racp/two-pc-lab"
    profile = Path(os.environ["LOCALAPPDATA"]) / "RACP/codex-oauth-lab"
    account = SecretStore(profile / "account.bin").load()
    environment = dict(
        os.environ,
        CODEX_CA_CERTIFICATE=str((lab / "ca.pem").absolute()),
        SSL_CERT_FILE=str((lab / "ca.pem").absolute()),
    )
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "codex -c mcp_oauth_credentials_store=keyring mcp login racp --no-browser "
            "--scopes racp.read,racp.execute,offline_access",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines: queue.Queue[str | None] = queue.Queue()
    assert process.stdout and process.stdin

    def receive() -> None:
        assert process.stdout
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=receive, daemon=True).start()
    try:
        deadline = time.monotonic() + 75
        authorization = None
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=2)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("Codex OAuth discovery failed before an authorization URL")
            any_url = re.search(r"https?://[^\s]+", line)
            if any_url and urlsplit(any_url.group(0)).netloc != "127.0.0.1:19443":
                raise RuntimeError("Codex selected an unexpected OAuth authorization origin")
            found = re.search(r"https://127\.0\.0\.1:19443/[^\s]+", line)
            if found:
                authorization = found.group(0)
                break
        if not authorization:
            raise RuntimeError("Codex OAuth authorization URL was not received")
        with httpx.Client(
            verify=tls_context(lab / "ca.pem"), follow_redirects=False, trust_env=False, timeout=10
        ) as http:
            response = http.get(authorization)
            response.raise_for_status()
            callback = None
            consent = False
            for _ in range(12):
                if response.status_code in (302, 303):
                    location = urljoin(str(response.url), response.headers["location"])
                    if location.startswith(account["callback"] + "?"):
                        callback = location
                        break
                    parsed = urlsplit(location)
                    if parsed.hostname != "127.0.0.1" or parsed.port != 19443:
                        raise RuntimeError("Owned OAuth flow redirected outside its provider")
                    response = http.get(location)
                    continue
                match = re.search(r'<form\b[^>]*action="([^"]+)"', response.text)
                if not match:
                    raise RuntimeError("Owned OAuth form was not found")
                action = urljoin(str(response.url), html.unescape(match.group(1)))
                parsed = urlsplit(action)
                if parsed.hostname != "127.0.0.1" or parsed.port != 19443:
                    raise RuntimeError("Owned OAuth form action changed origin")
                if 'name="username"' in response.text:
                    data = {
                        "username": account["username"],
                        "password": account["password"],
                        "credentialId": "",
                    }
                else:
                    data = {
                        name: html.unescape(value)
                        for name, value in re.findall(
                            r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', response.text
                        )
                        if name not in {"cancel", "reject"}
                    }
                    data["accept"] = "Yes"
                    consent = True
                response = http.post(action, data=data)
            if not callback or not consent:
                raise RuntimeError("Owned OAuth PKCE and explicit consent did not complete")
        process.stdin.write(callback + "\n")
        process.stdin.flush()
        process.stdin.close()
        return_code = process.wait(timeout=40)
        if return_code:
            raise RuntimeError("Codex rejected the OAuth callback or credential storage")
        print("PASS: native Codex OAuth login, PKCE/consent and OS credential storage")
    finally:
        if process.poll() is None:
            if not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
