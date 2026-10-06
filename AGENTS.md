# RACP Agent Guidelines & Version Management

This document defines repository instructions, operational principles, and semantic versioning policies for AI agents (Codex, Antigravity, Gemini, etc.) and contributors working on the **Remote-Desktop-Commander (RACP)** codebase.

---

## 1. Project Overview

- **Name**: Remote-Desktop-Commander (RACP - Remote Access and Control Protocol)
- **Architecture**:
  - Python workspace (`uv` workspace): `apps/agent`, `apps/cli`, `apps/gateway`, `packages/domain`, `packages/protocol`, `packages/policy`, `packages/observability`, `packages/sdk`
  - Node / Web / Desktop workspace (`pnpm` workspace): `apps/client` (Electron + React), `apps/console` (React + Vite)
- **Current Baseline Version**: `0.1.8`

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
