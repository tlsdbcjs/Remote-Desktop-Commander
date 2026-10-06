"""Stdlib-only base interpreter bootstrap: no venv redirector or pre-readiness children."""

import json
import os
import site
import sys
from pathlib import Path


def main() -> None:
    scheduled = len(sys.argv) > 1
    if scheduled:
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config-file", type=Path, required=True)
        parser.add_argument("--site", required=True)
        args = parser.parse_args()
        if args.config_file.is_symlink() or args.config_file.stat().st_size > 4096:
            raise PermissionError("invalid guardian private config")
        value = {
            "site": args.site,
            "config": json.loads(args.config_file.read_text(encoding="utf-8")),
        }
    else:
        raw = sys.stdin.buffer.readline(65537)
        if not 0 < len(raw) <= 65536:
            raise ValueError("invalid guardian bootstrap")
        value = json.loads(raw)
    if value.keys() != {"site", "config"} or not isinstance(value["config"], dict):
        raise ValueError("invalid guardian bootstrap fields")
    directory = value["site"]
    if (
        not isinstance(directory, str)
        or not os.path.isabs(directory)
        or not os.path.isdir(directory)
    ):
        raise ValueError("invalid installed runtime directory")
    # This path is chosen by the local installed runtime, never an operation payload.
    site.addsitedir(directory)
    from racp_agent.broker.guard_config import GuardConfig
    from racp_agent.broker.input_guardian import run

    config = GuardConfig.model_validate(value["config"])
    try:
        run(config, report_ready=not scheduled)
    finally:
        if scheduled:
            from concurrent.futures import ThreadPoolExecutor

            from racp_agent.broker.guard_task import cleanup_guard_config

            # The ephemeral task/config also disappear after Agent or Broker crash.
            # Keep Scheduler COM on its own thread, before this process exits.
            with ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="racp-guard-cleanup"
            ) as worker:
                worker.submit(cleanup_guard_config, config).result()


if __name__ == "__main__":
    main()
