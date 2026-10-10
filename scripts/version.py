"""Unified version management CLI for RACP workspace.

Follows Semantic Versioning (MAJOR.MINOR.PATCH):
- MAJOR / MINOR: User-defined / explicitly incremented when requested.
- PATCH: Automatically increments on default bump (auto-increment).

Usage:
  # View current version
  python scripts/version.py show

  # Auto-increment patch (0.1.8 -> 0.1.9)
  python scripts/version.py bump

  # Bump minor (0.1.8 -> 0.2.0)
  python scripts/version.py bump --minor

  # Set specific minor (0.1.8 -> 0.3.0)
  python scripts/version.py bump --minor 3

  # Bump major (0.1.8 -> 1.0.0)
  python scripts/version.py bump --major

  # Set specific version explicitly
  python scripts/version.py set 0.2.0

  # Dry-run preview
  python scripts/version.py bump --dry-run
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION_FILE = ROOT / "packages/domain/src/racp_domain/version.py"

TARGET_PYPROJECTS = [
    ROOT / "pyproject.toml",
    ROOT / "apps/cli/pyproject.toml",
    ROOT / "apps/gateway/pyproject.toml",
    ROOT / "packages/domain/pyproject.toml",
    ROOT / "packages/observability/pyproject.toml",
    ROOT / "packages/policy/pyproject.toml",
    ROOT / "packages/protocol/pyproject.toml",
    ROOT / "packages/sdk/pyproject.toml",
]

TARGET_PACKAGE_JSONS = [
    ROOT / "apps/client/package.json",
    ROOT / "apps/console/package.json",
    ROOT / "apps/client/src-tauri/tauri.conf.json",
]

TARGET_CARGO = [ROOT / "Cargo.toml", ROOT / "apps/client/src-tauri/Cargo.toml"]

TARGET_CODE_FILES = [
    VERSION_FILE,
    ROOT / "apps/client/src/main.tsx",
]


def parse_semver(version_str: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version_str.strip())
    if not match:
        raise ValueError(
            f"Invalid semantic version '{version_str}'. "
            "Expected format: MAJOR.MINOR.PATCH (e.g. 0.1.8)"
        )
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def get_current_version() -> str:
    if not VERSION_FILE.exists():
        raise FileNotFoundError(f"Version definition file not found: {VERSION_FILE}")
    content = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise ValueError(f"Could not find VERSION in {VERSION_FILE}")
    return match.group(1).strip()


def calculate_next_version(
    current: str,
    major_val: int | None = None,
    bump_major: bool = False,
    minor_val: int | None = None,
    bump_minor: bool = False,
    patch_val: int | None = None,
) -> str:
    cur_major, cur_minor, cur_patch = parse_semver(current)

    if major_val is not None:
        new_major = major_val
        new_minor = minor_val if minor_val is not None else 0
        new_patch = patch_val if patch_val is not None else 0
    elif bump_major:
        new_major = cur_major + 1
        new_minor = minor_val if minor_val is not None else 0
        new_patch = patch_val if patch_val is not None else 0
    elif minor_val is not None:
        new_major = cur_major
        new_minor = minor_val
        new_patch = patch_val if patch_val is not None else 0
    elif bump_minor:
        new_major = cur_major
        new_minor = cur_minor + 1
        new_patch = patch_val if patch_val is not None else 0
    elif patch_val is not None:
        new_major = cur_major
        new_minor = cur_minor
        new_patch = patch_val
    else:
        # Default behavior: auto-increment the last version number (patch)
        new_major = cur_major
        new_minor = cur_minor
        new_patch = cur_patch + 1

    return f"{new_major}.{new_minor}.{new_patch}"


def update_file(path: Path, old_version: str, new_version: str, dry_run: bool) -> bool:
    if not path.exists():
        return False

    original = path.read_text(encoding="utf-8")
    updated = original

    if path.name in {"pyproject.toml", "Cargo.toml"}:
        updated = re.sub(
            r'^(version\s*=\s*["\'])[^"\']+(["\'])',
            rf"\g<1>{new_version}\g<2>",
            original,
            flags=re.MULTILINE,
        )
    elif path.name in {"package.json", "tauri.conf.json"}:
        updated = re.sub(
            r'("version"\s*:\s*")[^"]+(")',
            rf"\g<1>{new_version}\g<2>",
            original,
        )
    elif path.name == "version.py":
        updated = re.sub(
            r'(VERSION\s*=\s*["\'])[^"\']+(["\'])',
            rf"\g<1>{new_version}\g<2>",
            original,
        )
    elif path.name == "main.tsx":
        updated = re.sub(
            r'(const CLIENT_VERSION\s*=\s*["\'])[^"\']+(["\'])',
            rf"\g<1>{new_version}\g<2>",
            original,
        )

    if updated != original:
        if not dry_run:
            path.write_text(updated, encoding="utf-8")
        return True
    return False


def apply_version(new_version: str, dry_run: bool = False) -> list[str]:
    # Validate semver
    parse_semver(new_version)
    current = get_current_version()

    targets: list[Path] = []
    targets.extend(TARGET_PYPROJECTS)
    targets.extend(TARGET_PACKAGE_JSONS)
    targets.extend(TARGET_CARGO)
    targets.extend(TARGET_CODE_FILES)

    modified: list[str] = []
    for target in targets:
        if update_file(target, current, new_version, dry_run):
            rel = target.relative_to(ROOT).as_posix()
            modified.append(rel)

    return modified


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RACP Version Manager (Semantic Versioning with Auto-Increment Patch)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # show / current
    subparsers.add_parser("show", help="Show current version")
    subparsers.add_parser("current", help="Alias for show")

    # bump
    bump_parser = subparsers.add_parser("bump", help="Bump version")
    bump_parser.add_argument(
        "--major",
        nargs="?",
        const=True,
        default=False,
        help="Increment major or set explicit major integer (e.g. --major or --major 2)",
    )
    bump_parser.add_argument(
        "--minor",
        nargs="?",
        const=True,
        default=False,
        help="Increment minor or set explicit minor integer (e.g. --minor or --minor 3)",
    )
    bump_parser.add_argument(
        "--patch",
        type=int,
        help="Explicit patch integer",
    )
    bump_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the change without writing to files",
    )

    # set
    set_parser = subparsers.add_parser("set", help="Set explicit version")
    set_parser.add_argument("version", help="Explicit version (e.g. 0.1.8)")
    set_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the change without writing to files",
    )

    args = parser.parse_args()

    if args.command in ("show", "current"):
        print(get_current_version())
        return 0

    current = get_current_version()

    if args.command == "set":
        target_version = args.version
        parse_semver(target_version)
        dry_run = args.dry_run
    elif args.command == "bump":
        dry_run = args.dry_run

        major_val: int | None = None
        bump_major = False
        if args.major is True:
            bump_major = True
        elif args.major is not False and args.major is not None:
            major_val = int(args.major)

        minor_val: int | None = None
        bump_minor = False
        if args.minor is True:
            bump_minor = True
        elif args.minor is not False and args.minor is not None:
            minor_val = int(args.minor)

        target_version = calculate_next_version(
            current=current,
            major_val=major_val,
            bump_major=bump_major,
            minor_val=minor_val,
            bump_minor=bump_minor,
            patch_val=args.patch,
        )
    else:
        parser.print_help()
        return 1

    action_label = "[DRY-RUN] Would update" if dry_run else "Updating"
    print(f"{action_label} version: {current} -> {target_version}")

    modified = apply_version(target_version, dry_run=dry_run)
    for path in modified:
        print(f"  - {path}")

    print(f"Success! {len(modified)} files updated to version {target_version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
