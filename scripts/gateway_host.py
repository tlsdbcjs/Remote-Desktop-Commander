"""Start a self-contained Windows Gateway with private, persistent host state."""

import argparse
import ipaddress
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psutil
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from racp_gateway.config import GatewayConfig, load_gateway_config, write_gateway_config
from racp_gateway.oauth import load_oauth_config
from racp_gateway.store import GatewayStore
from racp_protocol.models import new_id
from racp_sdk.security import SecretStore, digest, tls_context, token

if __package__:
    from .create_connection_file import create_file
    from .create_console_login import create_login
else:
    from create_connection_file import create_file
    from create_console_login import create_login

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / ".racp" / "host"


def private_directory(path: Path) -> None:
    """Apply the current Windows user DACL before writing any host secrets."""
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    if os.name != "nt":
        return
    import win32api
    import win32con
    import win32security

    handle = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(handle, win32security.TokenUser)[0]
    finally:
        handle.Close()
    acl = win32security.ACL()
    acl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE,
        win32con.GENERIC_ALL,
        sid,
    )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )


def local_addresses() -> list[str]:
    return sorted(
        {
            entry.address
            for entries in psutil.net_if_addrs().values()
            for entry in entries
            if entry.family == socket.AF_INET
            and ipaddress.ip_address(entry.address).is_private
            and not ipaddress.ip_address(entry.address).is_loopback
            and not ipaddress.ip_address(entry.address).is_link_local
            and not ipaddress.ip_address(entry.address).is_unspecified
        }
    )


def choose_host() -> str:
    addresses = local_addresses()
    if len(addresses) == 1:
        return addresses[0]
    print("Gateway에 사용할 이 PC의 IPv4 주소: " + ", ".join(addresses))
    selected = input("호스트 IPv4 주소를 입력하세요: ").strip()
    if selected not in addresses:
        raise ValueError("Select an address assigned to this host")
    return selected


def check_listener(host: str, port: int) -> None:
    address = ipaddress.IPv4Address(host)
    if not (address.is_private or address.is_loopback) or address.is_unspecified:
        raise ValueError("A private host IPv4 address is required")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid Gateway port")
    with socket.socket() as probe:
        probe.bind((host, port))


def setup(state: Path, host: str, port: int) -> None:
    check_listener(host, port)
    private_directory(state)
    now = datetime.now(UTC)
    signer = ec.generate_private_key(ec.SECP256R1())
    key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RACP private host CA")])
    expiry = now + timedelta(days=365)
    ca = (
        x509.CertificateBuilder()
        .subject_name(issuer)
        .issuer_name(issuer)
        .public_key(signer.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(expiry)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(signer, hashes.SHA256())
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(expiry)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(host)), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(signer, hashes.SHA256())
    )
    (state / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (state / "RACP-Host-CA.crt").write_bytes(ca.public_bytes(serialization.Encoding.DER))
    (state / "server.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (state / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    secret = token()
    store = GatewayStore(state / "gateway" / "gateway.db")
    try:
        store.initialize(digest(secret))
    finally:
        store.close()
    SecretStore(state / "owner.bin").save(
        {"token": secret, "gateway": f"https://{host}:{port}"}, overwrite=False
    )
    instance_id = new_id("gateway")
    gateway_config = GatewayConfig(
        instance_id=instance_id,
        mode="portable",
        bind_address=host,
        port=port,
        public_origin=f"https://{host}:{port}",
        state_root=str((state / "gateway").resolve()),
        tls={
            "certificate_file": str((state / "server.pem").resolve()),
            "private_key_file": str((state / "server.key").resolve()),
            "client_ca_file": str((state / "ca.pem").resolve()),
        },
    )
    write_gateway_config(state / "gateway" / "config" / "gateway.json", gateway_config)
    (state / "host.json").write_text(
        json.dumps(
            {
                "host": host,
                "port": port,
                "instance_id": instance_id,
                "certificate_expires": expiry.isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def start(state: Path, host: str | None, port: int | None, oauth_path: Path | None) -> int:
    if not state.exists():
        setup(state, host or choose_host(), port or 8765)
    config = json.loads((state / "host.json").read_text(encoding="utf-8"))
    if (host is not None and host != config["host"]) or (
        port is not None and port != config["port"]
    ):
        raise ValueError("Existing host settings cannot be overwritten")
    check_listener(config["host"], config["port"])
    if oauth_path is not None:
        oauth_path = oauth_path.resolve(strict=True)
        load_oauth_config(oauth_path)
        config["oauth_config"] = str(oauth_path)
        (state / "host.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        gateway_config_path = state / "gateway" / "config" / "gateway.json"
        if gateway_config_path.is_file():
            gateway_config = load_gateway_config(gateway_config_path)
            gateway_config = gateway_config.model_copy(
                update={
                    "auth": gateway_config.auth.model_copy(
                        update={"oauth_config_file": str(oauth_path)}
                    ),
                    "revision": gateway_config.revision + 1,
                }
            )
            write_gateway_config(
                gateway_config_path,
                gateway_config,
                expected_revision=gateway_config.revision - 1,
            )
    oauth = config.get("oauth_config")
    if oauth:
        load_oauth_config(Path(oauth))
    origin = f"https://{config['host']}:{config['port']}"
    owner = SecretStore(state / "owner.bin").load()
    if owner["gateway"] != origin:
        raise ValueError("Host identity and listener settings disagree")
    command = [
        sys.executable,
        "-I",
        "-B",
        "-m",
        "racp_gateway.main",
        "--data-dir",
        str(state / "gateway"),
        "--host",
        config["host"],
        "--port",
        str(config["port"]),
        "--public-origin",
        origin,
        "--tls-cert",
        str(state / "server.pem"),
        "--tls-key",
        str(state / "server.key"),
        "--client-ca-file",
        str(state / "ca.pem"),
    ]
    if oauth:
        command.extend(["--oauth-config", oauth])
    print("Console: " + origin + "/console/", flush=True)
    print("MCP: " + origin + "/mcp (" + ("OAuth" if oauth else "OAuth 설정 필요") + ")")
    print("Gateway 실행 창을 유지하세요. 종료: Ctrl+C", flush=True)
    process = subprocess.Popen(command, cwd=ROOT)
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Ctrl+C also reaches the foreground child; let Uvicorn shut down gracefully.
        try:
            return process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            print("Gateway 종료가 지연됩니다. 이 창의 서버 로그를 확인하세요.")
            return 1


def status(state: Path) -> None:
    owner = SecretStore(state / "owner.bin").load()
    with httpx.Client(
        base_url=owner["gateway"],
        verify=tls_context(state / "ca.pem"),
        headers={"Authorization": "Bearer " + owner["token"]},
        trust_env=False,
        timeout=10,
    ) as client:
        response = client.get("/api/v1/doctor")
        response.raise_for_status()
        data = response.json()
    print(
        json.dumps(
            {
                "gateway": owner["gateway"],
                "status": data["status"],
                "mcp": data["gateway"]["mcp"],
                "devices": [
                    {k: d.get(k) for k in ("id", "name", "status")} for d in data["devices"]
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "connection", "login"])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--oauth-config", type=Path)
    parser.add_argument("--name", default="Windows PC")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    try:
        state = args.state_dir.resolve()
        if args.action == "start":
            return start(state, args.host, args.port, args.oauth_config)
        if args.action == "status":
            status(state)
            return 0
        if args.action == "connection":
            file, expiry = create_file(state, state / "connection-files", args.name)
            print(f"연결 파일: {file}\n1회 사용 / 만료: {expiry:%Y-%m-%d %H:%M:%S %Z}")
        else:
            file, url = create_login(state)
            print(f"Console: {url}\n5분 유효 / 1회 로그인 코드 파일: {file}")
        if os.name == "nt" and not args.no_open:
            subprocess.Popen(["explorer.exe", "/select," + str(file)])
        return 0
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        print("Gateway 작업에 실패했습니다. 호스트 IP·포트·설정·TLS 인증서를 확인하세요.")
        print("로그인/연결 파일 발급은 Gateway 시작 후 실행하세요. 기존 상태 폴더는 보존됩니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
