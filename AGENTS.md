# RACP Agent Guidelines & Version Management

> **Document ID**: `DOC-GOV-AGENTS`\
> **Status**: Active · **Target Version**: v0.1.10\
> **Last Updated**: 2026-10-07 · **Classification**: Repository Agent Guidelines

This document defines repository instructions, operational principles, and semantic versioning policies for AI agents (Codex, Antigravity, Gemini, etc.) and contributors working on the **Remote-Desktop-Commander (RACP)** codebase.

## Table of Contents

- [1. Project Overview](#1-project-overview)
- [2. Version Management Policy](#2-version-management-policy-semver--auto-increment)
- [3. Version Tool](#3-version-tool-scriptsversionpy)
- [4. Synchronized Targets](#4-single-source-of-truth--synchronized-targets)
- [5. Version References](#5-how-code-references-the-version)
- [6. Release Checklist](#6-pre-commit--release-checklist-for-agents)
- [7. Documentation Governance](#7-technical-documentation-governance-documentation-standards-skill)
- [8. Desktop Build Definition](#8-desktop-build-definition-desktop-build-skill)

---

## 1. Project Overview

- **Name**: Remote-Desktop-Commander (RACP - Remote Access and Control Protocol)
- **Architecture**:
  - Python workspace (`uv` workspace): `apps/agent`, `apps/cli`, `apps/gateway`, `packages/domain`, `packages/protocol`, `packages/policy`, `packages/observability`, `packages/sdk`
  - Node / Web / Desktop workspace (`pnpm` workspace): `apps/client` (Electron + React), `apps/console` (React + Vite)
- **Current Baseline Version**: Read `packages/domain/src/racp_domain/version.py` (SSOT).

---

## 2. Version Management Policy (SemVer & Auto-Increment)

Version numbering follows **Semantic Versioning (`MAJOR.MINOR.PATCH`)**:

$$\text{Version} = \text{MAJOR} . \text{MINOR} . \text{PATCH}$$

### 2.1 Rules for Version Bumping

| Component | Rule | Description |
| :--- | :--- | :--- |
| **MAJOR** ($X.0.0$) | **User-Defined Only** | Updated **only** when explicitly requested/defined by the user (e.g. major architectural redesign, breaking API/protocol changes). Patch and Minor reset to 0 unless specified. |
| **MINOR** ($0.X.0$) | **User-Defined Only** | Updated **only** when explicitly requested/defined by the user (e.g. new feature set, significant protocol additions). Patch resets to 0 unless specified. |
| **PATCH** ($0.0.X$) | **Auto-Incremented** | **Automatically increments by 1** on standard bumps, bug fixes, refactoring, routine maintenance, and release builds without requiring user intervention. |

---

## 3. Version Tool: `scripts/version.py`

Use `scripts/version.py` to inspect, increment, or set versions across all project files.

### 3.1 Commands & Examples

```bash
# 1. View current version
python scripts/version.py show
# Output: 0.1.8

# 2. Auto-increment PATCH (Default behavior: 0.1.8 -> 0.1.9)
python scripts/version.py bump

# 3. Bump MINOR (User-defined: 0.1.8 -> 0.2.0)
python scripts/version.py bump --minor

# 4. Set specific MINOR (User-defined: 0.1.8 -> 0.3.0)
python scripts/version.py bump --minor 3

# 5. Bump MAJOR (User-defined: 0.1.8 -> 1.0.0)
python scripts/version.py bump --major

# 6. Set specific MAJOR (User-defined: 0.1.8 -> 2.0.0)
python scripts/version.py bump --major 2

# 7. Set exact version explicitly
python scripts/version.py set 0.2.0

# 8. Dry-run preview (checks changes without writing to disk)
python scripts/version.py bump --dry-run
python scripts/version.py bump --minor --dry-run
```

---

## 4. Single Source of Truth & Synchronized Targets

The Single Source of Truth (SSOT) is defined in:
- `packages/domain/src/racp_domain/version.py` (`VERSION = "0.1.8"`, `__version__ = VERSION`)

When `scripts/version.py` runs, it atomically updates all target files:

1. **Python Workspace Configurations**:
   - `pyproject.toml` (root workspace)
   - `apps/agent/pyproject.toml`
   - `apps/cli/pyproject.toml`
   - `apps/gateway/pyproject.toml`
   - `packages/domain/pyproject.toml`
   - `packages/observability/pyproject.toml`
   - `packages/policy/pyproject.toml`
   - `packages/protocol/pyproject.toml`
   - `packages/sdk/pyproject.toml`

2. **JavaScript / Desktop Packages**:
   - `apps/client/package.json`
   - `apps/console/package.json`

3. **Code & UI Version Display**:
   - `packages/domain/src/racp_domain/version.py` (`VERSION`, `__version__`)
   - `apps/client/src/main.tsx` (`const CLIENT_VERSION = "..."` rendered in header)

---

## 5. How Code References the Version

- **Python modules**:
  ```python
  from racp_domain.version import VERSION, __version__

  # Or via package root:
  import racp_cli

  print(racp_cli.__version__)
  ```
- **CLI Commands**:
  - `python -m racp_cli --version` -> `racp 0.1.8`
  - `python -m racp_agent.main --version` -> `racp-agent 0.1.8`
  - `python -m racp_gateway.main --version` -> `racp-gateway 0.1.8`
- **Client Application**:
  - Displayed prominently in the top header as `<span className="version-badge">v0.1.8</span>`.
- **Dynamic Wheels & Build Scripts**:
  - `scripts/build.py` and `scripts/build_windows_client.py` reference `racp_domain.version.VERSION` dynamically.

---

## 6. Pre-Commit / Release Checklist for Agents

Before completing any version-related change or release:

1. **Verify Version State**:
   ```bash
   python scripts/version.py show
   ```
2. **Run Ruff Linting**:
   ```bash
   uv run ruff check scripts/version.py tests/unit/test_version.py packages/ apps/
   ```
3. **Run Mypy Type Checking**:
   ```bash
   uv run mypy
   ```
4. **Run Version Unit Tests**:
   ```bash
   uv run pytest tests/unit/test_version.py -v
   ```

---

## 7. Technical Documentation Governance (`documentation-standards` Skill)

All agents working on the RACP repository must follow the technical documentation governance defined in [`.agents/skills/documentation-standards/SKILL.md`](.agents/skills/documentation-standards/SKILL.md).

### 7.1 Documentation Directory Taxonomy
All technical documents MUST be placed in their dedicated category under `docs/`:
- `docs/spec/`: System architecture, protocol design, master engineering specifications.
- `docs/guides/`: User, deployment, operations, and testing guides.
- `docs/quality/`: Quality assurance, platform compatibility matrix, implementation status, and release gates.
- `docs/adr/`: Architecture Decision Records (`ADR-XXXX-*.md`).
- `docs/protocol/`: JSON schemas and OpenAPI contracts (strictly immutable file paths required by contract tests).

### 7.2 Anti-Bloat & Aggregation Policy
- **No Transient Result Files**: Agents must **NOT** create ad-hoc milestone files (such as `phase-*-result.md`, `core-*-result.md`, or temporary review notes).
- **Single Source of Truth for Progress**: All verification logs, test counts, passing metrics, and milestone achievements MUST be recorded directly in [`docs/quality/implementation-status.md`](docs/quality/implementation-status.md).

### 7.3 Standardized Document Template
Every markdown document must include the standard metadata header block (Document ID, Status, Target Version, Last Updated, Classification), structured headings, callouts (`> [!NOTE]`, `> [!IMPORTANT]`, `> [!WARNING]`), and verified relative links.

---

## 8. Desktop Build Definition (`desktop-build` Skill)

Use [`.agents/skills/desktop-build/SKILL.md`](.agents/skills/desktop-build/SKILL.md) for desktop build, setup/portable packaging, runtime staging, or build CI changes. The maintained user procedure is [Desktop Client Guide](docs/guides/desktop-client-guide.md#6-개발-빌드-및-패키징-절차).

| Platform | Setup | Portable | Current execution policy |
| :--- | :--- | :--- | :--- |
| Windows x64 | NSIS `*-setup.exe` | `*-portable.exe`; ZIP with executable and resources | Build and verify now; default local/CI target |
| macOS x64 / arm64 | DMG containing `.app` | ZIP containing `.app` | Defined; defer native builds until explicitly requested |
| Linux x64 / arm64 | deb | AppImage | Defined; defer native builds until explicitly requested |

> [!IMPORTANT]
> Build only Windows by default. Do not start macOS/Linux builds, containers, or their CI jobs during routine builds. Their native runners can be enabled explicitly using the workflow dispatch input `include_deferred_platforms`.

- Entry point: `uv run python scripts/build_client.py --platform win --arch x64`. Use `--node <path>` when the pinned Node is not on PATH; `--dry-run` prints paths/targets without building.
- Build Python, Chromium, and native dependencies on the matching OS and architecture. Reject mismatched bundles instead of copying Windows binaries to macOS/Linux. `.exe` applies to Windows; macOS/Linux use their native executable formats.
- Packaging SSOT: `apps/client/electron-builder.json`; `apps/client/electron-builder.cjs` resolves the current workspace version and native paths.
- Staging: `dist/client-agent/<version>/<win|mac|linux>-<arch>/`; output: `dist/client-desktop/<version>/<win|mac|linux>-<arch>/`.
- Bump PATCH once per build/change batch with `scripts/version.py`, refresh `uv.lock`, and reuse that version for retries and all platform artifacts. Never bump once per output format.
- Bundle Electron, CPython 3.12.11, current workspace wheels and pinned Playwright Chromium. Never package enrollment tokens, credentials, gateway-specific settings, or local device state.
- Preserve existing outputs. On a failed build, inspect and move only the failed batch into a sibling quarantine path before retrying; do not delete unrelated `dist/` contents.
- Verify lint/types/version tests, frontend build/Node tests, native Agent imports, Windows unpacked/portable smoke and packaging manifest integrity. Record checksums, passed checks and deferred platforms in [Implementation Status](docs/quality/implementation-status.md).

> [!NOTE]
> Development builds are unsigned and use `--publish never`; successful packaging does not establish clean-machine installation, upgrade/uninstall, signing, notarization, or native macOS/Linux acceptance.

