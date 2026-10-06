import argparse
import asyncio
import io
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from racp_sdk.security import SecretStore, digest

from racp_agent.instance_lock import InstanceLock, InstanceRunningError
from racp_agent.plugins.config import load_plugin_config
from racp_agent.runtime import Agent
from racp_agent.settings import default_state_dir, local_path, saved_settings
from racp_agent.settings_edit import settings_lock
from racp_agent.workspaces import workspace_argument


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RACP outbound device Agent")
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument(
        "--allow-workspace",
        action="append",
        type=workspace_argument,
        help="Approved folders replacing saved additions, ID=PATH",
    )
    parser.add_argument(
        "--ca-file", type=Path, help="Additional CA certificate for the Gateway TLS connection"
    )
    parser.add_argument(
        "--plugin-config", type=Path, help="Local approved plugin manifest hashes/permissions"
    )
    parser.add_argument(
        "--desktop-session-id",
        action="append",
        type=int,
        default=[],
        help="Opt into a Broker for this explicit Windows session (up to four)",
    )
    parser.add_argument(
        "--enable-desktop",
        action="store_true",
        help="Enable screen/input Broker for this Windows logon session",
    )
    parser.add_argument(
        "--enable-browser-cdp", action="store_true", help="Opt into loopback CDP attach/discovery"
    )
    parser.add_argument(
        "--desktop-login-user-sid",
        action="append",
        default=[],
        help="Allow this explicitly configured user SID to register a logon Broker",
    )
    parser.add_argument(
        "--service-sid", help="Installed Agent service SID; must be in its actual process token"
    )
    parser.add_argument(
        "--browser-allow-origin",
        action="append",
        default=[],
        help="Restrict browser page HTTP(S)/WS access to these exact origins",
    )
    parser.add_argument("--profile", choices=["read_only", "standard", "trusted_personal"])
    return parser


@contextmanager
def open_agent(credential_store: Path, args: argparse.Namespace | None = None) -> Iterator[Agent]:
    options = args or parser().parse_args([])
    local_path(credential_store.absolute())
    with ExitStack() as lifetime:
        with InstanceLock(settings_lock(credential_store)):
            credentials = SecretStore(credential_store).load()
            settings = saved_settings(credentials)
            workspace = options.workspace or (settings.workspace if settings else None)
            if workspace is None:
                raise ValueError(
                    "Legacy enrollment requires --workspace; use racp-connect for new setup"
                )
            data_dir = options.data_dir or (settings.data_dir if settings else Path(".racp/agent"))
            device_lock = credential_store.absolute().parent / (
                "agent-" + digest(credentials["device_id"]) + ".lock"
            )
            lifetime.enter_context(InstanceLock(device_lock))
            lifetime.enter_context(InstanceLock(data_dir.absolute() / "agent.lock"))
        desktop_sessions = tuple(options.desktop_session_id)
        if (options.enable_desktop or (settings and settings.desktop_enabled)) and os.name == "nt":
            from racp_agent.broker.identity import process_identity

            own_session = process_identity(os.getpid()).session
            if own_session and own_session not in desktop_sessions:
                desktop_sessions += (own_session,)
        agent = Agent(
            credentials["gateway"],
            credentials["credential"],
            credentials["device_id"],
            workspace,
            data_dir,
            profile=options.profile or (settings.profile if settings else "read_only"),
            browser_allowed_origins=tuple(options.browser_allow_origin),
            browser_cdp=options.enable_browser_cdp,
            desktop_sessions=desktop_sessions,
            desktop_login_users=tuple(options.desktop_login_user_sid),
            service_sid=options.service_sid,
            plugins=load_plugin_config(options.plugin_config),
            ca_file=options.ca_file
            or (Path(credentials["ca_file"]) if credentials.get("ca_file") else None),
            allowed_workspaces=tuple(
                options.allow_workspace
                if options.allow_workspace is not None
                else settings.allowed_workspaces
                if settings
                else []
            ),
        )
        try:
            yield agent
        finally:
            agent.journal.close()


def run(credential_store: Path, args: argparse.Namespace | None = None) -> None:
    try:
        with open_agent(credential_store, args) as agent:
            asyncio.run(agent.run())
    except KeyboardInterrupt:
        pass


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    credential_store = args.credentials or (
        Path(".racp/agent/credential.bin")
        if args.workspace
        else default_state_dir() / "credential.bin"
    )
    try:
        run(credential_store, args)
    except (OSError, ValueError, KeyError, InstanceRunningError):
        print(
            "Agent could not start; check enrollment, workspace and TLS configuration",
            file=sys.stderr,
        )
        raise SystemExit(4) from None


if __name__ == "__main__":
    main()
