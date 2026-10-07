---
name: desktop-build
description: >-
  Build and package the RACP Electron desktop client with its native Agent runtime,
  define Windows/macOS/Linux setup and portable artifacts, or maintain desktop build CI.
---

# RACP Desktop Build

> **Document ID**: `DOC-SKILL-DESKTOP-BUILD`\
> **Status**: Active · **Target Version**: v0.1.10\
> **Last Updated**: 2026-10-07 · **Classification**: Build Operations

## Table of Contents

- [Platform policy](#platform-policy)
- [Build procedure](#build-procedure)
- [Verification and retries](#verification-and-retries)
- [Deferred platforms](#deferred-platforms)

## Platform policy

Build Windows x64 by default. Keep macOS/Linux definitions but defer their builds
until the user explicitly requests them. Do not use Linux Docker or enable the
additional CI runners as part of an ordinary Windows build.

| Target | Setup | Portable |
| :--- | :--- | :--- |
| `win-x64` | NSIS setup EXE | Single portable EXE; ZIP with executable/resources |
| `mac-x64`, `mac-arm64` | DMG with `.app` | ZIP with `.app` |
| `linux-x64`, `linux-arm64` | deb | AppImage |

> [!IMPORTANT]
> Agent Python, native modules, and Chromium must match the build host's OS and
> architecture. Never reuse a Windows Agent on macOS/Linux. EXE is Windows-only.

## Build procedure

1. Inspect the working tree and SSOT version. Apply the [Version Manager](../version-manager/SKILL.md)
   policy: bump PATCH once for the change/build batch, then `uv sync --all-packages`
   to refresh local workspace versions in `uv.lock`. Reuse the version for retries.
2. Use Python 3.12.11, Node 22.23.0 and pnpm 11.19.0. On Windows, the existing
   `.tools/node-v22.23.0-win-x64/node.exe` and its Corepack provide the pinned Node/pnpm.
   If absent, acquire the checksum-verified workspace runtime with
   `uv run python scripts/bootstrap_node.py`.
3. Run the entry point from the repository root:

```powershell
uv run python scripts/build_client.py --platform win --arch x64 --node .tools/node-v22.23.0-win-x64/node.exe
```

The script synchronizes frozen dependencies, builds current wheels, installs pinned
Chromium, stages the Agent, builds/tests the frontend, packages all formats, runs
Windows unpacked/portable smoke checks and writes SHA-256/size evidence to
`build-manifest.json`. It never publishes artifacts.

Configuration SSOT is [electron-builder.json](../../../apps/client/electron-builder.json).
Load it through [electron-builder.cjs](../../../apps/client/electron-builder.cjs), which
resolves `dist/client-agent/<version>/<target>-<arch>/` and
`dist/client-desktop/<version>/<target>-<arch>/`. The pre-pack hook checks the Agent's
version, platform, architecture, lock digest and file hashes. Do not bypass it.

## Verification and retries

Run the repository's Ruff/Mypy/version gates and the affected desktop tests. Run
`scripts/client_e2e.py --node <pinned-node>` against the newly staged runtime. A packaged
smoke must use a fresh `--user-data-dir` and verify isolation before making any IPC calls.
Use `apps/client/tests/packaged-smoke.cjs` for the unpacked executable and
`apps/client/tests/portable-smoke.cjs` for the NSIS portable launcher; pass the artifact
path as the first argument to pinned Node. The launcher needs a loopback Chromium
debugging connection because it does not forward Playwright's Electron inspector pipe.
Do not stop or reconfigure an existing registered client as a build test.

Existing output paths stop the build. For a failed attempt, inspect and move only that
batch's staging/output directories to unique sibling quarantine names, then retry using
the same version. Preserve other releases and user state.

Record actual checks and artifact hashes in [Implementation Status](../../../docs/quality/implementation-status.md).
Mark macOS/Linux as defined/deferred, and distinguish packaging from clean-PC
installation, upgrade/uninstall and native platform acceptance.

> [!NOTE]
> These are unsigned development artifacts. Signing/notarization and publishing need
> their own explicitly requested release work.

## Deferred platforms

Use `--platform mac|linux --arch x64|arm64 --dry-run` on any host to inspect the plan.
After an explicit request, remove `--dry-run` on a matching native host. For CI,
`include_deferred_platforms=true` explicitly enables macOS arm64 and Linux x64 runners;
normal push/PR/manual runs build Windows only. See the
[Desktop Client Guide](../../../docs/guides/desktop-client-guide.md#6-개발-빌드-및-패키징-절차).
