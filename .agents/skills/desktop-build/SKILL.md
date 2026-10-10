---
name: desktop-build
description: >-
  Build and package the native RACP Tauri/Rust client and Rust Agent,
  define Windows/macOS/Linux setup and portable artifacts, or maintain desktop build CI.
---

# RACP Desktop Build

> **Document ID**: `DOC-SKILL-DESKTOP-BUILD`\
> **Status**: Active · **Target Version**: v0.1.21\
> **Last Updated**: 2026-10-10 · **Classification**: Build Operations

## Platform policy

Build Windows x64 by default. macOS DMG/ZIP and Linux deb/AppImage definitions are deferred until explicitly requested. Do not launch their native builds, Docker containers or additional CI runners for routine Windows builds. Use `--dry-run` to inspect their proposed artifacts.

## Build procedure

1. Inspect the working tree and SSOT version. Bump PATCH once per change/build batch; reuse that version for every retry and artifact format. Python is used for the repository version tool and Gateway only.
2. Use native Windows x64, Rust 1.90.0, Node 22.23.0 and pnpm 11.19.0.
3. From the repository root:

```powershell
pnpm install --frozen-lockfile
node scripts/build-client.mjs --platform win --arch x64
```

The entry point builds both native Agent and portable launcher, downloads build-only Playwright 1.63.0 to obtain Chromium revision 1243, validates fixed WebView2 against `scripts/webview2-runtime-lock.json`, builds Tauri/React, and packages NSIS setup, self-extracting portable EXE and ZIP. The installed payload requires no Python, Node or Electron.

Configuration is maintained in [tauri.conf.json](../../../apps/client/src-tauri/tauri.conf.json) and [installer.nsh](../../../apps/client/src-tauri/installer.nsh). [build-client.mjs](../../../scripts/build-client.mjs) owns staging, exact payload inventory and artifact evidence. The Agent and browser must match the build host's OS and architecture.

Output batches are `dist/client-desktop/<version>/win-x64/<build-id>/` and `dist/client-agent/<version>/win-x64/<build-id>/`. The manifest records source revision, locked dependency digests, runtime versions, file hashes, artifact sizes and deferred tests. Do not bypass inventory verification.

## Verification and retries

Automated tests, native acceptance and GUI smoke are explicitly deferred by the user for this migration. Run production builds and package integrity checks; do not describe a successful build as tested feature parity. A later acceptance session must use a fresh isolated profile and must not stop or reconfigure an existing registered client.

Preserve all previous outputs and user data. Existing batch paths stop the build. A failed attempt quarantines only its output batch; inspect the cause and retry under the same version with a new build ID. Do not clear unrelated `dist/`, staging or caches.

Record actual build results and checksums in [Implementation Status](../../../docs/quality/implementation-status.md). Development packages are unsigned. Signing, publishing, notarization and clean-PC installation/upgrade/uninstall acceptance are separate work.

## Deferred platforms

```bash
node scripts/build-client.mjs --platform mac --arch arm64 --dry-run
node scripts/build-client.mjs --platform linux --arch x64 --dry-run
```

See the [Desktop Client Guide](../../../docs/guides/desktop-client-guide.md#6-개발-빌드-및-패키징-절차).
