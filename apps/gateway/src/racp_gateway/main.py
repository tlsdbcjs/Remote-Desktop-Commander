import argparse
import logging
import socket
from contextlib import ExitStack
from pathlib import Path

import uvicorn
from racp_domain.version import __version__
from racp_sdk.connection_file import validate_ca_pem

from racp_gateway.app import create_app
from racp_gateway.network import GatewayNetwork
from racp_gateway.oauth import load_oauth_config


def main() -> None:
    parser = argparse.ArgumentParser(description="RACP authenticated local/remote control plane")
    parser.add_argument("--version", action="version", version=f"racp-gateway {__version__}")
    parser.add_argument("--data-dir", type=Path, default=Path(".racp/gateway"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
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
