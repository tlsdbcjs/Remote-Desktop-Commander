# Client·Agent Rust 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Document ID**: `DOC-PLAN-CLIENT-RUST`\
> **Status**: Active · **Target Version**: v0.1.11 예정 / 기준선 v0.1.10\
> **Last Updated**: 2026-10-10 · **Classification**: Architecture Implementation Plan

**Goal:** 기존 client와 내장 Agent를 Rust로 전환하고 기능·등록 상태를 보존한 뒤 client 전용 Python 및 Electron 런타임을 제거한다.

**Architecture:** React 화면은 유지하고 Tauri 2의 제한된 명령 adapter를 연결한다. UI와 분리된 Rust background Agent가 기존 Python Gateway에 HTTPS/WSS로 연결한다. 계약, 로컬 상태, 실행 엔진, Windows native 코드는 역할별 crate/module로 분리한다.

**Tech Stack:** Rust 1.90.0, Cargo workspace, Tauri 2, Tokio 1, Serde 1, reqwest/rustls, tokio-tungstenite, rusqlite, Windows API bindings, React/TypeScript/Vite, NSIS.

**Spec:** [승인된 전환 설계](client-rust-migration.md). 2026-10-10 사용자 “진행해”는 이 설계 승인으로 기록한다. 2026-10-10 사용자 “권장 방향으로 끝까지 구현해”로 계획과 Native 실행을 승인했다.

## 목차

- [Global Constraints](#global-constraints)
- [Review Focus](#review-focus)
- [파일과 공통 인터페이스](#파일과-공통-인터페이스)
- [Task 1: 계약과 테스트 기반](#task-1-계약과-테스트-기반)
- [Task 2: 보호된 등록·설정·로컬 상태](#task-2-보호된-등록설정로컬-상태)
- [Task 3: 저널과 WSS 실행 수명주기](#task-3-저널과-wss-실행-수명주기)
- [Task 4: 파일·프로세스·셸·터미널](#task-4-파일프로세스셸터미널)
- [Task 5: Python 없는 브라우저](#task-5-python-없는-브라우저)
- [Task 6: Windows Broker·Guardian·서비스](#task-6-windows-brokerguardian서비스)
- [Task 7: 리버싱 플러그인](#task-7-리버싱-플러그인)
- [Task 8: Tauri UI 호스트](#task-8-tauri-ui-호스트)
- [Task 9: 설치·포터블·유지보수](#task-9-설치포터블유지보수)
- [Task 10: Python 제거와 회귀 검증](#task-10-python-제거와-회귀-검증)
- [실행과 검토](#실행과-검토)

## Global Constraints

- 추천 설계인 Tauri/Rust + 기존 React UI를 구현한다. Gateway·CLI·Web Console의 기능은 유지한다.
- `docs/protocol` 스키마 경로와 wire protocol을 보존한다. 서드파티 라이브러리가 허용하는 입력과 RACP가 허용하는 입력을 구분한다.
- 기존 16 KiB bridge 입력 한도, 안전한 오류 코드, DPAPI/0600, 워크스페이스 link/reparse 차단을 보존한다.
- Agent 수명은 UI와 분리한다. 창 닫기는 숨김, 완전 종료는 Agent cleanup 확인 후 종료다.
- Windows x64 설치 EXE·포터블 EXE·ZIP이 기본 대상이다. macOS/Linux native 빌드·컨테이너는 명시 요청 전 수행하지 않는다.
- Windows native 테스트는 Windows host/runner에서 실행한다. Linux의 Rust 단위·계약 검사는 native 패키지 빌드와 구분한다.
- 구현 배치의 PATCH를 한 번 올리고 재시도마다 버전을 올리지 않는다. 기존 dist 및 사용자 데이터를 보존한다.
- 전환 중 구 Python은 비교 기준으로 유지할 수 있지만 최종 client 코드·배포물에는 fallback과 Python runtime을 남기지 않는다.
- Cargo.lock, rust-toolchain.toml, pnpm-lock.yaml을 유지한다. 의존성 버전은 Task 1과 해당 기능 최초 구현에서 컴파일 검증 후 고정한다.
- 실제 결과는 [Implementation Status](../quality/implementation-status.md)에 기록한다. 미실행 native 검증을 PASS로 표시하지 않는다.

## Review Focus

1. 이전 등록 경로·DPAPI·한글/공백 경로를 읽지 못해 재등록되는 문제: Task 2와 8에서 기존 상태 fixture로 검증한다.
2. 네트워크 응답 유실 후 같은 요청을 다시 실행하는 문제: Task 3에서 side effect 횟수와 UNKNOWN 복구를 검증한다.
3. PID 재사용·다른 설치의 자동 시작·실행 중 업그레이드로 다른 프로세스를 종료하는 문제: Task 6과 9에서 소유권 불일치를 거부한다.
4. 잘못된 custom CA·외부 HTTP·redirect·만료/변경된 `.racp` 파일을 받아들이는 문제: Task 2와 3에서 거부와 토큰 비노출을 검증한다.
5. cancellation/crash로 프로세스·브라우저·입력 키가 남는 문제: Task 4~7에서 자원 회수와 cleanup 불명확 상태를 검증한다.

## 파일과 공통 인터페이스

| 경로 | 책임 |
| :--- | :--- |
| `Cargo.toml`, `rust-toolchain.toml`, `Cargo.lock` | workspace 및 Rust 버전·의존성 고정 |
| `crates/racp-contract/src/{lib,error,bridge,wire,operations}.rs` | 검증된 UI/wire 입력과 제한된 오류 코드 |
| `crates/racp-core/src/{lib,paths,settings,secrets,journal,policy}.rs` | 보호된 로컬 상태와 실행 정책 |
| `crates/racp-runtime/src/{lib,enrollment,control,transport,dispatch,outputs,streams}.rs` | Agent 수명주기 및 실행 조합 |
| `crates/racp-runtime/src/providers/*.rs` | filesystem/process/shell/terminal/browser/desktop/reversing provider |
| `apps/agent-rust/src/{main,background,maintenance}.rs`, `src/windows/*.rs` | 실행 모드, Agent 제어, Windows native 실행 파일 역할 |
| `apps/client/src-tauri/src/{main,commands,controller,login}.rs` | 제한된 IPC, 트레이·로그인·창 수명주기 |
| `apps/client/src/{client-api,tauri-adapter}.ts` | 기존 화면 API를 Tauri로 연결 |
| `tests/rust_agent/*.py` | 실제 Rust 실행 파일과 Python Gateway 연동; client 제품 코드가 아닌 개발 검증 |
| `scripts/{build-client,stage-client-agent,client-manifest}.mjs` | Python 없는 client 빌드·staging·해시 검증 |

공통 타입은 `racp-contract`가 정의한다: `BridgeRequest`(11개 action의 strict enum), `Request`/`Message`(기존 wire 필드), `Profile`, `AgentSettings`, `ClientInfo`, `AgentStatus`, `Overview`, `OperationResult`, `ErrorCode`.
`RacpError { code: ErrorCode }`는 사용자에 노출할 오류이며 원시 HTTP/OS/parse 오류 문자열을 포함하지 않는다.
모든 공통 함수의 오류 반환형은 `Result<T, RacpError>`다. 외부 JSON은 검증 전 `serde_json::Value`, 검증 후 이 타입들을 사용한다.

## Task 1: 계약과 테스트 기반

**Files:** 위 contract crate, Cargo 설정, `crates/racp-contract/tests/{bridge,wire,registry}.rs`, `tests/fixtures/rust-contract/*.json`, `scripts/version.py`, `tests/unit/test_version.py`.

**Interfaces:** `decode_bridge(raw: &[u8]) -> Result<BridgeRequest, RacpError>`; `decode_message(raw: &[u8]) -> Result<Message, RacpError>`; `validate_operation(name: &str, value: Value) -> Result<Value, RacpError>`. 모델은 기존 protocol JSON schema와 `desktop_control.py`를 기준으로 한다.

- [x] **RED:** `bridge_bounds_and_unknown_fields`는 16,385-byte 입력·알 수 없는 action/field를 거부한다. `shell_mode_and_bounds`는 argv/shell 혼합, NUL, 257개 argv를 거부한다. `wire_limits`는 1 MiB 초과와 bool을 정수 필드에 넣은 입력을 거부한다. `registry_coverage`는 `registry-v1.json`의 모든 operation을 검증기에 매핑한다.
- [x] **Verify RED:** `cargo test -p racp-contract --tests`; 초기에는 crate/함수 부재로 실패해야 한다.
- [x] **Implement:** Cargo workspace와 검증 모델을 작성한다. 입력 수용 여부·기본값·정규화에 Python 모델과 동일한 fixture를 사용하며 schema 검증만으로 끝내지 않는다. 버전 도구에 Cargo/Tauri 동기화를 추가하고 PATCH를 0.1.11로 한 번 올린다.
- [x] **Verify GREEN:** 위 Rust 검사와 `uv run pytest tests/unit/test_version.py tests/contract/test_schema_drift.py -q`, AGENTS의 Ruff/Mypy 버전 게이트를 실행한다. contract failure가 0이어야 한다.
- [x] **Commit:** `feat(rust): establish validated client and agent contracts`.

## Task 2: 보호된 등록·설정·로컬 상태

**Files:** core paths/settings/secrets, runtime enrollment, `crates/racp-core/tests/{state,paths}.rs`, `crates/racp-runtime/tests/enrollment.rs`.

**Interfaces:** `SecretStore::new(path: PathBuf) -> Self`; `load(&self) -> Result<BTreeMap<String,String>, RacpError>`; `save(&self, value: &BTreeMap<String,String>, overwrite: bool) -> Result<(), RacpError>`; `validate_local_path(path: &Path) -> Result<PathBuf, RacpError>`; `enroll(request: Enrollment, state: &Path) -> Result<ClientInfo, RacpError>` (async); `update_settings(state: &Path, request: SettingsUpdate) -> Result<ClientInfo, RacpError>`.

- [ ] **RED:** `old_credentials_roundtrip`는 기존 JSON/Windows DPAPI fixture로 device identity가 유지됨을 확인한다. `state_guard`는 symlink/UNC/상대 경로/공개 credential 권한을 거부한다. `enrollment_preflight`는 잘못된 workspace/CA에서 서버 토큰을 소비하지 않는다. `connection_changed_or_expired`와 `settings_revision_conflict`는 원본 파일을 보존하며 거부한다. `enrollment_response_bounds`는 4,097-byte 응답을 거부하고 token을 오류에 포함하지 않는다.
- [ ] **Verify RED:** `cargo test -p racp-core --test state --test paths`; `cargo test -p racp-runtime --test enrollment`.
- [ ] **Implement:** DPAPI/0600 저장, atomic write와 overwrite=false, 32 KiB plaintext/64 KiB storage 한도, 16 KiB settings, 등록 reservation을 이식한다. custom CA 검증·TLS 필수·loopback HTTP 예외·redirect 금지를 보존한다.
- [ ] **Verify GREEN:** 동일 명령 및 Windows DPAPI native 검사를 통과한다. Python fixture가 Rust 결과를 읽고 Rust가 Python fixture를 읽는 양방향 호환성을 확인한다.
- [ ] **Commit:** `feat(rust): port protected enrollment and local settings`.

## Task 3: 저널과 WSS 실행 수명주기

**Files:** core journal/policy, runtime control/transport/dispatch/outputs, Agent main/background, `crates/racp-core/tests/journal.rs`, `tests/rust_agent/{conftest,test_lifecycle,test_tls}.py`.

**Interfaces:** `Journal::open(path: &Path) -> Result<Self, RacpError>`; `accept(&mut self, request: &Request) -> Result<Acceptance, RacpError>`; `recover_agent(&mut self) -> Result<(), RacpError>`. `Acceptance`는 `New`, `Replay(OperationResult)`, `Pending`을 구분한다. `Agent::new(settings: AgentSettings, credential: String) -> Result<Self, RacpError>`; `run(&mut self, shutdown: CancellationToken) -> Result<(), RacpError>` (async). `ControlClient::status/stop(&self) -> Result<AgentStatus, RacpError>` (async).

- [ ] **RED:** `dedup_100`는 동시 100회 동일 key 요청의 실행 횟수=1을 확인한다. `payload_conflict`는 같은 key/다른 payload를 거부한다. `recover_unknown`은 crash 이후 미확정 작업을 UNKNOWN으로 보존한다. `tls_and_lease`는 잘못된 CA/외부 HTTP를 거부하고 lease 만료 뒤 새 실행을 막는다. `stale_instance`는 PID/생성 시각/nonce 불일치 제어 응답을 거부한다.
- [ ] **Verify RED:** `cargo test -p racp-core --test journal`; `uv run pytest tests/rust_agent/test_lifecycle.py tests/rust_agent/test_tls.py -q`.
- [ ] **Implement:** 기존 SQLite schema와 retention/tombstone/reconcile 의미, Hello/Welcome/heartbeat/epoch/revoke/reconnect, job·deadline·취소·결과 복구를 이식한다. loopback 제어는 secret/nonce 및 process identity 검증을 사용한다. 기능별 capability는 구현된 provider만 광고한다.
- [ ] **Verify GREEN:** Python Gateway를 실제 loopback HTTPS/WSS로 실행하고 Rust binary를 별도 프로세스로 연결한다. 빈 journal 대체와 임의 재실행이 발생하지 않아야 한다.
- [ ] **Commit:** `feat(rust): implement journaled agent transport and lifecycle`.

## Task 4: 파일·프로세스·셸·터미널

**Files:** runtime providers filesystem/process/shell/terminal, streams/outputs, `crates/racp-runtime/tests/providers.rs`, `tests/rust_agent/test_execution.py`.

**Interfaces:** `Providers::execute(&self, request: &Request, cancel: CancellationToken) -> Result<OperationResult, RacpError>` (async). 동일 인터페이스를 Tasks 5~7의 provider에도 사용한다. `Providers::capabilities(&self) -> Vec<Capability>`는 정확한 supported/enabled/healthy를 반환한다.

- [ ] **RED:** `workspace_escape`는 `..`, link, 다른 named workspace 탈출을 거부한다. `transfer_resume_100mib`는 재개 뒤 SHA-256 일치를 확인한다. `shell_cancel_tree`는 자식·손자 프로세스가 남지 않음을 확인한다. `process_identity_fence`는 재사용 PID를 거부한다. `terminal_credit_replay`는 credit 초과 전송을 막고 재접속 버퍼를 복구한다.
- [ ] **Verify RED:** `cargo test -p racp-runtime --test providers`; `uv run pytest tests/rust_agent/test_execution.py -q`.
- [ ] **Implement:** registry의 해당 operation 전체를 이식한다. explicit argv/shell, encoding/env/cwd, Windows Job Object/POSIX process group, ConPTY/PTY, 출력 한도·artifact 승격과 resource ownership을 보존한다.
- [ ] **Verify GREEN:** 동일 명령과 Windows ConPTY/process tree native 검사를 통과한다. 읽기 전용 profile에서 mutation이 실행되지 않아야 한다.
- [ ] **Commit:** `feat(rust): port file process shell and terminal providers`.

## Task 5: Python 없는 브라우저

**Files:** runtime providers browser의 CDP/worker/workspace/event modules, `crates/racp-runtime/tests/browser.rs`, `tests/rust_agent/test_browser.py`.

**Interfaces:** Task 4의 provider 인터페이스. `BrowserProvider::new(config: BrowserConfig) -> Result<Self, RacpError>`; `inventory(&self) -> Vec<ResourceHandle>`; `shutdown(&self) -> Result<(), RacpError>` (async). `BrowserConfig`는 기존 origin/CDP opt-in과 resource limit을 담는다.

- [ ] **RED:** `isolated_contexts`는 context 간 cookie 공유가 없음을 확인한다. `origin_and_cdp_fences`는 허용되지 않은 origin과 opt-in 없는 attach를 거부한다. `browser_inventory_replay`는 frame/page/event sequence 복구를 확인한다. `browser_cancel_cleanup`은 crash/cancel 뒤 소유한 프로세스·다운로드가 남지 않음을 확인한다.
- [ ] **Verify RED:** `cargo test -p racp-runtime --test browser`; Windows에서 `uv run pytest tests/rust_agent/test_browser.py -q`.
- [ ] **Implement:** Rust CDP worker로 기존 browser operation, screenshot, upload/download, evaluate 한도, dialog, expiry, artifact를 이식한다. context=4/page=8 및 기존 sandbox/containment를 보존한다. Python Playwright 실행은 새 구현에 두지 않는다.
- [ ] **Verify GREEN:** 기존 `test_browser_*` 계약을 Rust Agent fixture에 연결하여 검증한다. screenshot/대용량 다운로드의 artifact digest를 확인한다.
- [ ] **Commit:** `feat(rust): replace Python browser workers with CDP runtime`.

## Task 6: Windows Broker·Guardian·서비스

**Files:** Agent windows identity/pipe/job/broker/capture/automation/input/guardian/service modules, runtime desktop provider, `apps/agent-rust/tests/windows_native.rs`, `tests/rust_agent/test_desktop.py`.

**Interfaces:** Task 4의 provider 인터페이스. `BrokerSupervisor::register(session_id: u32) -> Result<Self, RacpError>`; `shutdown(&mut self) -> Result<(), RacpError>` (async). `InputLease::acquire(identity: &SessionIdentity) -> Result<Self, RacpError>`; `release(&mut self) -> Result<(), RacpError>`. 서비스/Broker/Guardian 모드는 같은 Agent binary의 고정된 subcommand로 실행한다.

- [ ] **RED:** `pipe_peer_identity`는 다른 SID/session/process를 거부한다. `physical_input_interrupt`는 실제 입력 후 lease가 해제됨을 확인한다. `guardian_crash_release`는 Broker/Agent crash 뒤 키·마우스 눌림을 해제한다. `memory_read_fence`는 보호 PID·부정확한 create time을 거부한다. `service_stop`은 소유한 자원 회수를 확인한다.
- [ ] **Verify RED:** Windows에서 `cargo test -p racp-agent --test windows_native`; `uv run pytest tests/rust_agent/test_desktop.py -q`.
- [ ] **Implement:** 기존 ACL/Named Pipe/Job Object/session fencing/UIA/capture/input watch·lease·release와 Win32 memory API를 이식한다. 실제 native 검증 전에 capability를 healthy로 광고하지 않는다.
- [ ] **Verify GREEN:** synthetic owned GUI와 Windows release gate 시나리오를 실행한다. 환경 부족으로 skip된 항목은 미완료로 기록한다.
- [ ] **Commit:** `feat(rust): port Windows desktop broker guardian and service`.

## Task 7: 리버싱 플러그인

**Files:** runtime providers reversing, Agent plugin supervisor/gdb/ghidra modules, Java bridge resource, `crates/racp-runtime/tests/plugins.rs`, `tests/rust_agent/test_reversing.py`.

**Interfaces:** Task 4의 provider 인터페이스. `PluginSupervisor::launch(manifest: PluginManifest) -> Result<Self, RacpError>` (async); `request(&mut self, value: PluginMessage) -> Result<PluginMessage, RacpError>` (async); `shutdown(&mut self) -> Result<(), RacpError>` (async). contract crate가 plugin 모델을 소유한다.

- [ ] **RED:** `plugin_stdio_bounds`는 잘못된 메시지·초과 출력·다른 resource owner를 거부한다. `gdb_mi_stop_sequence`는 중단점·메모리·detach 상태와 순서를 확인한다. `ghidra_database_isolation`은 분석 database/target hash 경계를 확인한다. `plugin_crash_cleanup`은 소유 process tree와 handle을 정리한다.
- [ ] **Verify RED:** `cargo test -p racp-runtime --test plugins`; Windows native 도구가 설치된 runner에서 `uv run pytest tests/rust_agent/test_reversing.py -q`.
- [ ] **Implement:** strict plugin protocol·manifest 검증, 설치 hash·whitelist, GDB/MI parser, Ghidra Headless를 이식한다. 필요한 Java bridge를 Rust resource 위치로 옮기고 Python adapter는 대체한다.
- [ ] **Verify GREEN:** 기존 synthetic plugin 계약과 native GDB/Ghidra fixture를 Rust Agent로 검증한다.
- [ ] **Commit:** `feat(rust): port debugger and analysis plugin adapters`.

## Task 8: Tauri UI 호스트

**Files:** client src-tauri Cargo/config/capabilities/commands/controller/login, TS adapter/API/main, `apps/client/tests/adapter.test.mjs`, `apps/client/src-tauri/tests/controller.rs`.

**Interfaces:** `createClientApi(invoke: Invoke) -> ClientApi`는 현재 `window.racpClient`의 모든 메서드와 반환 shape를 보존한다. Rust `client_request(request: BridgeRequest, state: State<ClientState>) -> Result<Value, ErrorCode>` (async), `client_overview`, `client_refresh`, `client_exit`, `client_folder`, `client_ca`, `client_connection`, `client_enroll_connection`, `client_login_settings`, `client_set_login`은 main window 전용 command다.

- [ ] **RED:** `adapter_contract`는 기존 API 메서드와 안전한 오류 메시지를 확인한다. `close_hides_full_exit_stops`는 창 닫기와 완전 종료를 구분한다. `failed_cleanup_keeps_window`는 STOPPED/cleanup 확인 실패 때 앱을 유지한다. `connection_selection_owned_by_host`는 renderer의 path/digest 조작을 거부한다. `legacy_user_data`는 기존 Electron userData Agent 경로로 등록 정보를 읽는다.
- [ ] **Verify RED:** `node --test apps/client/tests/adapter.test.mjs`; Windows에서 `cargo test -p racp-client --test controller`.
- [ ] **Implement:** 3초 status/activity 갱신, busy/sampling 직렬화, tray 이미지·메뉴, single instance·startup 인자, native dialogs와 host-owned connection selection을 이식한다. CSP와 Tauri capability를 local main window로 제한한다.
- [ ] **Verify GREEN:** adapter 검사, `pnpm --dir apps/client build`, Windows UI E2E에서 기존 enrollment/settings/activity/login/tray/exit 시나리오를 검증한다.
- [ ] **Commit:** `feat(client): migrate desktop host from Electron to Tauri`.

## Task 9: 설치·포터블·유지보수

**Files:** Node build/stage/manifest scripts, client installer.nsh/portable launcher, Agent maintenance, `.github/workflows/client.yml`, client packaging/native smoke tests, AGENTS 및 desktop build skill·guide.

**Interfaces:** Node CLI `build-client.mjs --platform win|mac|linux --arch x64|arm64 [--dry-run]`; Agent CLI `maintenance --install-dir PATH --state-dir PATH --executable-name NAME --login-name NAME --mode upgrade|uninstall`. manifest는 기존 version/platform/architecture와 file hash 정보를 보존한다.

- [ ] **RED:** `manifest_rejects_wrong_target_and_hash`는 잘못된 target/변조/경로 탈출을 거부한다. `maintenance_ownership`은 다른 install/startup/PID 소유권을 거부한다. `backup_preserves_state`는 backup hash와 사용자 데이터 보존을 확인한다. `portable_without_python_or_webview_install`은 Python/Node/WebView2가 없는 Windows fixture에서 bundle로 시작한다. `failed_cleanup_aborts_upgrade`는 cleanup 불명확 상태에서 설치 변경을 막는다.
- [ ] **Verify RED:** `node --test scripts/tests/client-packaging.test.mjs`; Windows에서 `cargo test -p racp-agent --test maintenance` 및 새 패키지 smoke.
- [ ] **Implement:** CPython/wheel/Playwright/Electron staging 대신 Rust binary·fixed WebView2·Chromium을 hash 검증해 staging한다. NSIS setup·self-extracting portable EXE·ZIP, Rust maintenance hook와 실패 batch quarantine를 구성한다. CI는 Windows 기본, 다른 플랫폼은 explicit dispatch만 허용한다.
- [ ] **Verify GREEN:** Windows 세 형식의 smoke, native architecture/lock/version/hash 검사, 새 프로필 격리, 설치·업그레이드·제거 데이터 보존을 확인한다. 결과와 checksums를 Implementation Status에 기록한다.
- [ ] **Commit:** `build(client): package native Rust setup and portable artifacts`.

## Task 10: Python 제거와 회귀 검증

**Files:** 구 `apps/agent` Python 및 client Electron 파일 제거, root pyproject/uv.lock/version tool, `tests/conftest.py`와 Agent import 테스트, schema drift 검사, Python client helper 대체·삭제, README/ADR/guide/quality.

**Interfaces:** 기존 Python live fixture 대신 Rust Agent subprocess lifecycle를 사용한다. Gateway·CLI fixture의 public API는 유지한다. contract drift 검사에서 Agent-owned Python 모델 import를 제거하고 published schema와 Rust fixture 검증을 연결한다.

- [ ] **RED:** `client_has_no_python_runtime`는 client bundle의 python 실행 파일·Python module/wheel과 Python subprocess launch를 거부한다. `gateway_cli_after_agent_removal`은 Agent package 없이 Gateway/CLI가 import되고 연동됨을 확인한다. `operation_parity_complete`는 registry의 모든 operation에 성공/거부·취소 증거가 있음을 확인한다.
- [ ] **Verify RED:** packaging audit와 `uv run pytest tests/rust_agent -q` 및 기존 테스트를 Rust fixture로 실행한다. 아직 Python launch/Agent import가 있으면 실패해야 한다.
- [ ] **Implement:** Tasks 1~9의 동작·native 증거가 확보된 뒤 구 코드와 uv dependency·test import를 삭제/대체한다. 공통 서버 Python 패키지는 유지하고 새 client build에 Python fallback을 넣지 않는다. ADR-0023을 새 결정으로 supersede하고 build instructions를 실제 구현에 맞춘다.
- [ ] **Verify GREEN:** `cargo fmt --all --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace`, frontend build/test, Ruff/Mypy/version/schema/서버·CLI 회귀, Windows artifact/upgrade/cleanup 검사. host 제한으로 미실행이면 전환 완료로 표시하지 않는다.
- [ ] **Commit:** `refactor(client): remove legacy Python and Electron client runtime`.

## 실행과 검토

Task 1부터 순서대로 구현한다. 순수 contract/core 단위검사는 Linux에서 실행할 수 있으나,
Windows UI·Broker·packaging task는 실제 Windows host/runner의 검증을 통과해야 한다.
native host가 없으면 가능한 구현·정적/단위 검증을 수행하고 그 이후 native 검증 단계에서
정확한 미완료 항목을 기록한다. 아직 검증되지 않은 Rust 기능 대신 Python을 삭제하지 않는다.

실행 방식 추천은 **Native**다. 작성자가 같은 세션에서 구현하고 마지막에 독립 리뷰를 받는다.
이는 공통 타입·프로세스 수명·상태 호환성에 의존하는 단계가 많기 때문이다.
사용자가 **Subagent-driven**을 선택하면 task별 구현자/리뷰어로 진행한다.
두 방식 모두 plan/spec을 먼저 읽고 RED → 구현 → GREEN → 검토 → commit 순서를 따른다.

사용자 계획 검토와 실행 방식 선택 후 해당 실행 스킬을 적용한다.
진행 및 검증 증거는 [구현 현황](../quality/implementation-status.md), 설계 기준은
[전환 설계](client-rust-migration.md), 문서 위치는 [명세 인덱스](README.md)를 따른다.
