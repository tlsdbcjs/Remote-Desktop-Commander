---
name: desktop-build
description: >-
  Build and package the native RACP Iced/Rust client and Rust Agent,
  define Windows/macOS/Linux setup and portable artifacts, or maintain desktop build CI.
---

# RACP Desktop Build

> **Document ID**: `DOC-SKILL-DESKTOP-BUILD`\
> **Status**: Active · **Target Version**: v0.1.27 native development\
> **Last Updated**: 2026-10-13 · **Classification**: Build Operations

## Platform policy

Build Windows x64 by default. macOS DMG/ZIP and Linux deb/AppImage definitions are deferred until explicitly requested. Do not launch their native builds, Docker containers or additional CI runners for routine Windows builds. Use `--dry-run` to inspect their proposed artifacts.

## Build procedure

### Native migration development path

Rust xtask is the Client entry point. Former Tauri/React source and Node packaging paths have been retired. Retained Console tooling is separate.

```powershell
cargo run --locked -p xtask -- package --platform win --arch x64 --dry-run
cargo run --locked -p xtask -- stage --platform win --arch x64 --build-id <unique-build-id>
cargo run --locked -p xtask -- verify-staging --platform win --arch x64 --build-id <unique-build-id>
cargo run --locked -p xtask -- stage-agent --platform win --arch x64 --build-id <unique-build-id>
cargo run --locked -p xtask -- verify-agent --platform win --arch x64 --build-id <unique-build-id>
cargo run --locked -p xtask -- stage-agent-service --build-id <unique-build-id>
cargo run --locked -p xtask -- verify-agent-service --build-id <unique-build-id>
```

Native staging uses Rust 1.90.0, MSVC/Windows SDK and a separate static CRT target. It admits only native Client/Agent, LICENSE and a Cargo SBOM plus their payload manifest. No Node/Python/Electron/React/WebView2 or implicit Chromium download is part of this path. Runtime admission is separate. Stage creates development files, not installers or installed state. `RACP_NATIVE_TARGET_DIR` may select a reusable compiler cache at an absolute normal directory; existing path/reparse-point validation remains mandatory. Source inputs, dirty commit, file hashes, target, features and lock digest are recorded. Preserve prior batches and quarantine only the newly failed batch.

When the user explicitly requests product artifacts, use `cargo run --locked -p xtask -- package --platform win --arch x64 --build-id <verified-build-id>`. Client `package` supports `--install-scope user` only. For an explicitly requested Agent-only service setup, use `package-agent-service --build-id <verified-service-build-id>` with a service staging batch; this command never executes the installer or changes SCM. Build-only `makensis.exe` compiles the maintained [user installer](../../../xtask/native-installer.nsi) or [machine service installer](../../../xtask/native-machine-installer.nsi). The Rust writer creates native portable EXE and ZIP. Earlier limited user-scope acceptance is recorded in Implementation Status; current no-install instructions forbid repeating it. Machine helper protection/detach and recipe installation remain unverified. Updater/native restore and partial provisioning recovery are incomplete. Compilation alone does not establish ZIP/portable execution or clean-machine acceptance.

The 2026-10-12 Windows installer request authorizes setup artifact creation. The user's latest instruction **do not actually install** prohibits further installer execution and install/uninstall tests on this machine. Earlier isolated acceptance completed before that instruction is historical evidence, not permission to repeat it. Machine service installation, signing, clean-machine acceptance and macOS/Linux builds remain deferred. Reuse the current version for retries/formats.

## Verification and retries

Preserve existing registered clients and limit work to the requested artifact. Perform static package/hash/source checks under the latest no-install instruction. Do not run another installer, installation test or uninstaller without new explicit user authorization. Signing, machine service installation and macOS/Linux native acceptance remain deferred. Do not describe earlier limited installer/GUI checks as full feature parity.

Use the component/version/target/build-ID layout in [AGENTS §9](../../../AGENTS.md#9-build-output-layout): Client `staging/` and `packages/`, standalone Agent `build/`, Gateway `packages/`, and Console `build/`. Verification belongs under `dist/verification/`. Preserve previous outputs by default; the user's later explicit request to delete all old `dist/` outputs overrides that default. Validate the exact absolute root, stop output processes and unlink junctions without following their external targets before deletion. Preserve source, registered state and reusable compiler caches. Existing batch paths stop the build. A failed attempt quarantines only its output phase; inspect the cause and retry under the same version with a new build ID.

Record actual build results and checksums in [Implementation Status](../../../docs/quality/implementation-status.md). Development packages are unsigned. Signing, publishing, notarization and clean-PC installation/upgrade/uninstall acceptance are separate work.

xtask records `not_run_by_xtask` for automated tests/native acceptance because staging/packaging do not execute them. Associate separate test evidence by exact artifact hash; do not infer a user-requested deferral from a build-only command. GUI probes must use an explicitly verified current staging payload (`scripts/probe_native_gui.py --payload-source <payload-directory>`); pywinauto/Pillow are test-only dependencies supplied through `uv run --with`, never bundled into the product.

`staging/racp-native-portable.exe` is the unassembled packaging stub. A user must run the verified `packages/*-setup.exe`, or the assembled `packages/*-portable.exe`; development GUI launch is `staging/payload/racp-client.exe` with its sibling payload files intact. Setup uses Korean UI, a versioned per-user directory, a Client shortcut and a Windows Apps uninstall entry. Verify actual install files against the staging manifest, then test GUI launch, running-file removal guard and uninstall/state preservation. NSIS source is UTF-8 and compilation uses `/INPUTCHARSET UTF8 /WX`. Admit pinned NSIS 3.11 by archive SHA-256 from the workflow; it remains build-only.

## Deferred platforms

```bash
cargo run --locked -p xtask -- package --platform mac --arch arm64 --dry-run
cargo run --locked -p xtask -- package --platform linux --arch x64 --dry-run
```

See the [Desktop Client Guide](../../../docs/guides/desktop-client-guide.md#6-개발-빌드-및-패키징-절차).
