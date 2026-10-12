# RACP Agent Guidelines & Version Management

> **Document ID**: `DOC-GOV-AGENTS`\
> **Status**: Active · **Target Version**: v0.1.26\
> **Last Updated**: 2026-10-12 · **Classification**: Repository Agent Guidelines

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
- [9. Build Output Layout](#9-build-output-layout)

---

## 1. Project Overview

- **Name**: Remote-Desktop-Commander (RACP - Remote Access and Control Protocol)
- **Architecture**:
  - Python workspace (`uv` workspace): `apps/cli`, `apps/gateway`, `packages/domain`, `packages/protocol`, `packages/policy`, `packages/observability`, `packages/sdk`
  - Rust workspace: `apps/agent-rust`, `apps/client-native` (Iced, including its local `src/ui_model.rs`), `crates/racp-contract`, `crates/racp-core`, `crates/racp-runtime`, `xtask`; opt-in independent `apps/analysis-host`
  - Node / Web workspace (`pnpm` workspace): `apps/console` (React + Vite) only
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
   - `apps/cli/pyproject.toml`
   - `apps/gateway/pyproject.toml`
   - `packages/domain/pyproject.toml`
   - `packages/observability/pyproject.toml`
   - `packages/policy/pyproject.toml`
   - `packages/protocol/pyproject.toml`
   - `packages/sdk/pyproject.toml`

2. **JavaScript / Desktop Packages**:
   - `apps/console/package.json`

3. **Rust Native Version Configuration**:
   - `Cargo.toml`
   - `apps/analysis-host/Cargo.toml` (independent optional host; same SSOT)

4. **Code & UI Version Display**:
   - `packages/domain/src/racp_domain/version.py` (`VERSION`, `__version__`)
   - `apps/client-native` reads `racp_contract::VERSION` (`CARGO_PKG_VERSION`) for its header

5. **Published Console Contract Metadata**:
   - `docs/protocol/console-openapi-v1.json` (`info.version` only; schema paths and API definitions unchanged)

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
  - `cargo run -p racp-agent -- --version` -> `racp-agent 0.1.8`
  - `python -m racp_gateway.main --version` -> `racp-gateway 0.1.8`
- **Client Application**:
  - Displayed prominently in the top header as `<span className="version-badge">v0.1.8</span>`.
- **Dynamic Wheels & Build Scripts**:
  - `scripts/build.py` imports `racp_domain.version.VERSION`; native `xtask` uses `racp_contract::VERSION`.

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

- Entry point: `cargo run --locked -p xtask -- stage --platform win --arch x64 --build-id <unique-id>`. Use Rust 1.90.0; `xtask package --dry-run` prints definitions without writing. Node 22.23.0/pnpm 11.19.0 belong to the retained Console only.
- Build native dependencies on the matching OS and architecture. Optional Chromium/analysis runtimes require separate manifest admission. Reject mismatched bundles; `.exe` applies to Windows while macOS/Linux definitions use native executable formats.
- Packaging SSOT: `xtask/src/package.rs`, `xtask/native-installer.nsi`, `crates/racp-contract/src/distribution.rs`. Product artifact generation is separate from development staging and requires its explicit request.
- Client staging: `dist/client/<version>/<target>/<build-id>/staging/`; product output: the same batch's `packages/`. Standalone Agent: `dist/agent/<version>/win-x64/<build-id>/build/`, using `xtask stage-agent` and `xtask verify-agent`.
- Bump PATCH once per build/change batch with `scripts/version.py`, refresh `uv.lock`, and reuse that version for retries and all platform artifacts. Never bump once per output format.
- Bundle native Iced Client, Rust Agent and required static assets/license/SBOM only. Do not bundle CPython, Node, Electron, React or WebView2. Optional runtimes and analysis host are separate. Never package enrollment tokens, credentials, gateway-specific settings or local device state.
- Preserve existing outputs by default. An explicit user request to reset `dist/` authorizes removing its old outputs, including legacy backups and test evidence; it does not authorize deleting source or registered state. Validate the exact workspace path, ensure output processes have stopped, and unlink reparse points without following their external targets before recursive deletion. On ordinary build failure, inspect and quarantine only the failed phase before retrying under a new build ID.
- Verify production Rust/TypeScript builds and packaging manifest integrity. The Windows installer request authorizes setup artifact creation. The user's latest instruction, **do not actually install**, prohibits any further installer execution or install/uninstall tests on this machine. Earlier isolated tests completed before that instruction are historical evidence only; do not rerun them without a new explicit request. Preserve existing registered profiles and services. Machine service installation, signing, clean-machine acceptance and macOS/Linux native builds remain deferred. Record checksums, passed checks and limitations in [Implementation Status](docs/quality/implementation-status.md).

> [!NOTE]
> Development builds are unsigned and use `--publish never`; successful packaging does not establish clean-machine installation, upgrade/uninstall, signing, notarization, or native macOS/Linux acceptance.

The user installer bundles Client and Agent in one setup EXE. Its default path is `%LOCALAPPDATA%\Programs\RACP-Native\<version>\payload\`; Start Menu and Windows Apps entries launch/remove this installed version. Registration/state stays at `%LOCALAPPDATA%\RACP\native-client` and is preserved on removal. `staging/racp-native-portable.exe` is a packaging stub, not a runnable portable distribution. Deliver the verified `packages/*-setup.exe` to users.

## 9. Build Output Layout

`dist/` contains generated outputs. `apps/` contains application source, `crates/` shared Rust libraries, and `target/` disposable Cargo compilation caches. Caches are not deployment artifacts and can be reused for a current-source rebuild.

| Component | Default path | Contents / entry point |
|---|---|---|
| Gateway | `dist/gateway/<version>/win-x64/<build-id>/packages/` | Python server/runtime and compiled Web Console, portable ZIP; `uv run --frozen python scripts/build_gateway.py --node <node.exe> --build-id <id> --targets portable` |
| Gateway workspace wheels | `dist/gateway/<version>/python-any/<build-id>/wheels/` | Python Gateway/CLI/shared-package wheels; `uv run --frozen python scripts/build.py --build-id <id>` |
| Agent | `dist/agent/<version>/win-x64/<build-id>/build/` | Rust Agent EXE, LICENSE, Agent-only SBOM and hash manifest; `cargo run --locked -p xtask -- stage-agent --build-id <id>` |
| Client | `dist/client/<version>/win-x64/<build-id>/staging/` | Iced Client, required companion Agent, LICENSE/SBOM/manifest and native launcher; `xtask stage` / `verify-staging` |
| Client product artifacts | `dist/client/<version>/<target>/<build-id>/packages/` | Explicitly requested setup/portable/ZIP; `xtask package` |
| Console | `dist/console/<version>/web/<build-id>/build/` | `web/` static frontend and build manifest; `uv run --frozen python scripts/console_build.py --node <node.exe> --build-id <id>` |
| Verification | `dist/verification/<component>/<version>/<run-id>/` | Test reports, logs and isolated fixtures. Pass explicit output paths to Agent/GUI probes; never package these files. Known-source RE fixtures use `dist/verification/fixtures/<version>/re-fixture/`. |

> [!IMPORTANT]
> Do not write loose wheels, screenshots or build manifests into the `dist/` root. Do not create new `client-agent`, `client-native`, `client-desktop` or legacy-backup output trees. The Gateway runtime belongs only to Gateway outputs; standalone Agent outputs contain no GUI/Python/Node. Client's companion Agent is intentional. No output may contain enrollment tokens, credentials or registered device state.

