"""Run the reproducible local quality gate; stop at the first failing command."""

import subprocess


def main() -> None:
    commands = [
        ["uv", "sync", "--all-packages", "--frozen"],
        ["uv", "run", "--frozen", "ruff", "format", "--check"],
        ["uv", "run", "--frozen", "ruff", "check"],
        ["uv", "run", "--frozen", "mypy"],
        ["uv", "run", "--frozen", "pytest", "-q", "--junitxml=dist/test-results.xml"],
    ]
    for command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
