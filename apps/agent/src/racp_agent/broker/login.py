"""User-logon entry point: authenticate a configured local Agent, then run the GUI Broker."""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from racp_domain.models import RACPError

from racp_agent.broker.access import grant_own_access
from racp_agent.broker.guard_config import Controller, GuardConfig
from racp_agent.broker.guard_spawn import GuardProcess, start_guard
from racp_agent.broker.identity import process_identity
from racp_agent.broker.login_registration import LoginEndpoint, register
from racp_agent.broker.main import run_config
from racp_agent.broker.pipe import NativeError
from racp_agent.broker.session_state import query, same_logon
from racp_agent.providers.paths import is_link
from racp_agent.providers.shell import execution_env


def read_endpoint(path: Path) -> LoginEndpoint:
    if is_link(path.lstat()) or path.stat().st_size > 4096:
        raise PermissionError("invalid login endpoint descriptor")
    return LoginEndpoint.model_validate(json.loads(path.read_text(encoding="utf-8")))


def run_login(endpoint: LoginEndpoint) -> None:
    if os.name != "nt":
        raise OSError("login Broker requires Windows")
    restore = grant_own_access((endpoint.principal,), managed_broker=True)
    guardian: GuardProcess | None = None
    try:
        config = register(endpoint)
        actor = Controller(
            pid=config.agent_pid,
            created=config.agent_created,
            sid=config.agent_sid,
            session=config.agent_session,
            service_sid=config.agent_service_sid,
        )
        try:
            guard_config = GuardConfig.pair_guard(
                process_identity(os.getpid()), config.agent_acl_sid, actor, config.job_name
            )
            # Task Scheduler COM stays on a dedicated thread; the Broker RPC thread is MTA.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="racp-login-guard") as worker:
                guardian = worker.submit(start_guard, guard_config).result()
        except (RACPError, OSError, NativeError, TimeoutError, ValueError, PermissionError):
            pass
        run_config(config, guardian.config if guardian is not None else None)
    finally:
        try:
            if guardian is not None:
                guardian.close()
        finally:
            restore()


def supervise(path: Path) -> None:
    identity = process_identity(os.getpid())
    if identity.session == 0:
        raise RACPError(
            "SESSION_UNAVAILABLE", "logon launcher requires a user session", layer="broker"
        )
    backoff = 0.5
    while same_logon(query(identity.session), identity.sid):
        started = time.monotonic()
        with subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "racp_agent.broker.login",
                "--endpoint-file",
                str(path),
                "--once",
            ],
            env=execution_env({}),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as child:
            try:
                while child.poll() is None:
                    if not same_logon(query(identity.session), identity.sid):
                        child.terminate()
                        break
                    time.sleep(0.25)
                child.wait()
            except BaseException:
                child.terminate()
                child.wait(5)
                raise
        if time.monotonic() - started > 30:
            backoff = 0.5
        time.sleep(backoff)
        backoff = min(30, backoff * 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="RACP Broker started by the configured logon user")
    parser.add_argument("--endpoint-file", type=Path, required=True)
    parser.add_argument(
        "--once", action="store_true", help="Exit after one pairing/lifetime (diagnostics)"
    )
    args = parser.parse_args()
    try:
        if args.once:
            run_login(read_endpoint(args.endpoint_file))
        else:
            supervise(args.endpoint_file)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
