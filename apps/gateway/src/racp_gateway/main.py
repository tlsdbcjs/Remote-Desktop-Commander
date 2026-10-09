import argparse
import logging
import socket
from contextlib import ExitStack
from pathlib import Path

import uvicorn
from racp_domain.version import __version__
from racp_sdk.connection_file import validate_ca_pem

from racp_gateway.app import create_app
from racp_gateway.config import load_gateway_config, resolve_gateway_paths
from racp_gateway.network import GatewayNetwork
from racp_gateway.oauth import load_oauth_config


def main() -> None:
    parser = argparse.ArgumentParser(description="RACP authenticated local/remote control plane")
    parser.add_argument("--version", action="version", version=f"racp-gateway {__version__}")
    parser.add_argument("--config", type=Path, help="Versioned Gateway configuration JSON")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--host")
    parser.add_argument(
        "--public-origin", help="Exact public origin, e.g. https://gateway.example:8765"
    )
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument(
        "--client-ca-file",
        type=Path,
        help="Public PEM CA included in owner-issued PC connection files",
    )
    parser.add_argument(
        "--oauth-config", type=Path, help="Absolute local configuration for a fixed OAuth provider"
    )
    parser.add_argument("--enable-trusted-personal", action="store_true")
    parser.add_argument("--disable-approvals", action="store_true")
    parser.add_argument(
        "--local-mcp-port",
        type=int,
        help="Optional HTTPS/OAuth MCP listener on 127.0.0.1; LAN ingress stays for Agents/API",
    )
    args = parser.parse_args()
    try:
        if args.config is not None:
            if any(
                value is not None
                for value in (
                    args.data_dir,
                    args.port,
                    args.host,
                    args.public_origin,
                    args.tls_cert,
                    args.tls_key,
                    args.client_ca_file,
                    args.oauth_config,
                    args.local_mcp_port,
                )
            ):
                raise ValueError("--config cannot be combined with legacy listener/path options")
            gateway_config = load_gateway_config(args.config)
            paths = resolve_gateway_paths(gateway_config)
            args.data_dir = paths.state_root
            args.port = gateway_config.port
            args.host = gateway_config.bind_address
            args.public_origin = gateway_config.public_origin
            args.tls_cert = (
                Path(gateway_config.tls.certificate_file)
                if gateway_config.tls.certificate_file
                else None
            )
            args.tls_key = (
                Path(gateway_config.tls.private_key_file)
                if gateway_config.tls.private_key_file
                else None
            )
            args.client_ca_file = (
                Path(gateway_config.tls.client_ca_file)
                if gateway_config.tls.client_ca_file
                else None
            )
            args.oauth_config = (
                Path(gateway_config.auth.oauth_config_file)
                if gateway_config.auth.oauth_config_file
                else None
            )
            args.local_mcp_port = gateway_config.auth.local_mcp_port
        else:
            args.data_dir = args.data_dir or Path(".racp/gateway")
            args.port = args.port or 8765
            args.host = args.host or "127.0.0.1"
        network = GatewayNetwork(
            args.host, args.port, args.public_origin, args.tls_cert, args.tls_key
        )
        network.validate()
        oauth_config = load_oauth_config(args.oauth_config)
        if args.local_mcp_port is not None and (
            not 1 <= args.local_mcp_port <= 65535
            or args.local_mcp_port == args.port
            or oauth_config is None
            or args.tls_cert is None
            or not args.public_origin
        ):
            raise ValueError(
                "Local MCP requires a distinct valid port, TLS, public origin and OAuth"
            )
        client_ca_pem = None
        if args.client_ca_file is not None:
            with args.client_ca_file.open("rb") as stream:
                raw_ca = stream.read(16385)
            if len(raw_ca) > 16384:
                raise ValueError("Client CA exceeds its bound")
            client_ca_pem = validate_ca_pem(raw_ca.decode("utf-8"))
    except (ValueError, OSError) as exception:
        parser.error(str(exception))
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with ExitStack() as resources:
        listeners = None
        if args.local_mcp_port is not None:
            listeners = []
            try:
                for host, port in [(args.host, args.port), ("127.0.0.1", args.local_mcp_port)]:
                    family = socket.AF_INET6 if ":" in host else socket.AF_INET
                    listener = resources.enter_context(socket.socket(family, socket.SOCK_STREAM))
                    listener.bind((host, port))
                    listener.listen(128)
                    listeners.append(listener)
            except OSError as exception:
                parser.error(f"Gateway listener could not bind: {exception}")
        application = create_app(
            args.data_dir,
            trusted_personal=args.enable_trusted_personal,
            approvals_enabled=not args.disable_approvals,
            public_origin=args.public_origin,
            oauth_config=oauth_config,
            client_ca_pem=client_ca_pem,
            local_mcp_port=args.local_mcp_port,
            gateway_config_path=args.config,
        )
        options = dict(
            network.uvicorn_options(),
            ws_max_size=1024 * 1024,
            ws_per_message_deflate=False,
            workers=1,
            loop="racp_gateway.loop:create_loop",
        )
        if listeners is None:
            uvicorn.run(application, **options)
        else:
            # GatewayNetwork validates the dynamic TLS/listener option dictionary.
            config = uvicorn.Config(application, **options)  # type: ignore[arg-type]
            uvicorn.Server(config).run(sockets=listeners)


if __name__ == "__main__":
    main()
