---
name: version-manager
description: >-
  Manages semantic versioning (MAJOR.MINOR.PATCH) for the RACP repository.
  Use when inspecting versions, bumping patch versions automatically,
  applying user-defined major/minor releases, or synchronizing versions across codebases.
---

# RACP Version Manager Skill

> **Document ID**: `DOC-SKILL-VERSION`\
> **Status**: Active · **Target Version**: v0.1.21\
> **Last Updated**: 2026-10-10 · **Classification**: Version Operations

This skill provides step-by-step procedures for managing semantic versioning in the RACP project.

## Table of Contents

- [Version Numbering Rules](#version-numbering-rules)
- [Quick Reference CLI](#quick-reference-cli)
- [Automated File Synchronization](#automated-file-synchronization)
- [Verification Runbook](#verification-runbook)

## Version Numbering Rules

- **Format**: `MAJOR.MINOR.PATCH` (e.g., `0.1.8`)
- **MAJOR**: User-defined. Increment or set **only** when the user explicitly requests a major version update.
- **MINOR**: User-defined. Increment or set **only** when the user explicitly requests a minor version update.
- **PATCH**: Auto-incremented. Default behavior when bumping versions (increments by 1 on routine fixes, builds, and releases).

For desktop setup/portable builds, use [Desktop Build](../desktop-build/SKILL.md).
Bump once per change/build batch, reuse the version for retries and formats, and run
`uv sync --all-packages` after the bump to refresh local workspace versions in `uv.lock`.

> [!NOTE]
> Setup and portable artifacts in the same build batch share one workspace version.

---

## Quick Reference CLI

All version operations use `scripts/version.py`:

```bash
# Check current version
python scripts/version.py show

# Auto-increment patch (0.1.8 -> 0.1.9)
python scripts/version.py bump

# Bump minor when user specifies (0.1.8 -> 0.2.0)
python scripts/version.py bump --minor

# Set specific user-defined minor (0.1.8 -> 0.3.0)
python scripts/version.py bump --minor 3

# Bump major when user specifies (0.1.8 -> 1.0.0)
python scripts/version.py bump --major

# Set specific user-defined major (0.1.8 -> 2.0.0)
python scripts/version.py bump --major 2

# Explicit version set
python scripts/version.py set <VERSION>

# Dry-run preview
python scripts/version.py bump --dry-run
```

---

## Automated File Synchronization

Running `scripts/version.py` automatically updates:
1. `packages/domain/src/racp_domain/version.py` (`VERSION`, `__version__`)
2. `pyproject.toml` (root workspace)
3. `Cargo.toml` (Rust workspace)
4. `apps/cli/pyproject.toml`
5. `apps/gateway/pyproject.toml`
6. `packages/domain/pyproject.toml`
7. `packages/observability/pyproject.toml`
8. `packages/policy/pyproject.toml`
9. `packages/protocol/pyproject.toml`
10. `packages/sdk/pyproject.toml`
11. `apps/client/package.json`
12. `apps/console/package.json`
13. `apps/client/src/main.tsx` (`CLIENT_VERSION`)
14. `apps/client/src-tauri/Cargo.toml`
15. `apps/client/src-tauri/tauri.conf.json`

---

## Verification Runbook

After any version update:
```bash
# 1. Run lint
uv run ruff check scripts/version.py tests/unit/test_version.py packages/ apps/

# 2. Run type check
uv run mypy

# 3. Run unit tests
uv run pytest tests/unit/test_version.py -v

# 4. Check CLI version output
uv run python -m racp_cli --version
```
