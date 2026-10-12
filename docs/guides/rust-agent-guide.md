# Rust Agent 실행 가이드

> **Document ID**: `DOC-GUIDE-RUST-AGENT`\
> **Status**: Active · **Target Version**: v0.1.23 native development\
> **Last Updated**: 2026-10-11 · **Classification**: User Guide

## 개요

> [!IMPORTANT]
> 현재 Agent 서비스의 후속 구현·미완성·시험 계획은 [Agent 서비스 전용 계획](../spec/agent-service-implementation-and-test-plan.md)을 따른다. GUI는 재구성 예정으로 해당 계획에서 제외한다. 아래 user-mode/Client 연동 설명과 과거 보류 기록은 서비스 계정의 실제 SCM 인수 증거가 아니며, 최신 실제 설치 금지 지시를 유지한다.

Rust Agent는 Iced native Client의 원격 실행과 로컬 권한·승인·작업 추적을 담당한다. 현재는 테스트 전 구현·생산 컴파일 단계이며 실제 GUI·원격 기능·설치·서비스 인수는 보류 상태다. 생산 빌드 성공을 실제 기능 성공으로 판단하지 않는다.

기본 payload는 Agent·Client·LICENSE·SBOM과 파일 manifest다. 브라우저/분석 도구는 별도 admission된 선택 runtime이며 기본 패키지에서 자동 검색하거나 다운로드하지 않는다. 구 Tauri/React Client와 TCP bridge·구 credential 설정 변환·이전 서비스 로그인 명령은 폐기했다. 과거 v0.1.21 실행 증거는 [구현 현황](../quality/implementation-status.md)에 이력으로 보존한다.

## 목차

- [1. 실행 파일과 새 상태](#1-실행-파일과-새-상태)
- [2. 등록과 명시적 권한](#2-등록과-명시적-권한)
- [3. 실행·제어·종료](#3-실행제어종료)
- [4. 선택 runtime과 서비스 범위](#4-선택-runtime과-서비스-범위)
- [5. 생산 빌드와 보류된 검증](#5-생산-빌드와-보류된-검증)
- [6. 관련 문서](#6-관련-문서)

## 1. 실행 파일과 새 상태

기본 Windows user state는 `%LOCALAPPDATA%/RACP/native-client`다. native 포터블 state는 launcher 위치별 별도 경로다. 명시한 다른 상태를 사용하려면 GUI의 `--state-dir <absolute-path>`를 사용한다. private SID/SYSTEM ACL을 확인하며 credential은 해당 계정의 DPAPI로 별도 보존한다. 기존 credential이나 Device ID를 가져오지 않는다.

GUI 사용·포터블 상태·배포 형식은 [Desktop Client Guide](desktop-client-guide.md)에 정의한다. 실행 중인 기존 설치·Device·사용자 데이터를 변경하는 교체 절차는 별도 인수 대상이다.

## 2. 등록과 명시적 권한

GUI에서 새 연결 파일의 Gateway·만료·파일 SHA-256을 확인하고 작업 영역·실행 프로필·권한을 명시한 뒤 새 등록을 요청한다. 초기 권한은 모두 거부다. 미확정 등록 marker가 남으면 자동 재등록하지 않는다. enrollment token은 argv·GUI 응답·로그에 표시하지 않는다.

자동화용 `native-enroll`은 bounded JSON을 stdin으로 받는다. GUI와 같은 native enrollment 형식이며 권한 policy가 필수다. Agent 내부 설정은 version 2와 명시한 permissions를 검사하고 version 1·누락된 policy를 변환하거나 넓은 기본 허용 목록으로 대체하지 않는다. 서버의 실행 프로필은 로컬 권한을 좁힐 수 있다.

## 3. 실행·제어·종료

등록된 새 상태만 foreground로 실행한다. 명령 인수에는 token을 넣지 않는다.

```powershell
.\racp-agent.exe native-run --state-dir 'C:\RACP\native-state'
```

백그라운드 시작·정책·승인·작업 취소·Stop은 native GUI와 인증 Named Pipe를 사용한다. foreground는 Ctrl+C를 통해 정리를 요청한다. 실행 시작 전의 durable 기록과 최종 자원 회수 기록, Agent 잠금 해제를 함께 확인하며 강제 종료나 정리 미확정을 정상 종료로 간주하지 않는다.

| 로컬 명령 | 입력/역할 |
|---|---|
| `native-settings-configure` | stdin JSON, Agent 정지 상태의 workspace/profile revision CAS |
| `native-login-status` / `native-login-configure` | 현재 사용자의 고정 native 로그인 항목 조회/명시 변경 |
| `native-runtime-status` / `native-runtime-admit` / `native-runtime-configure` | 선택 runtime 조회·명시 admission·선택 CAS |
| `native-maintenance-status` | packaged user 설치의 정리·저장 형식·로그인 참조 검사, 실제 교체/삭제 없음 |

명령마다 `--state-dir <absolute-path>`가 필요하다. helper 응답은 `ok/result` 또는 `ok=false/error.code`, 실패 종료 코드는 4다. Broker/Guardian/Provider worker의 stdout은 해당 framed protocol 전용이다.

## 4. 선택 runtime과 서비스 범위

Chromium/CDB/GDB/별도 native 분석 host는 manifest hash·전체 파일 hash/size·OS/architecture/version을 확인하고 명시적으로 admission해야 한다. 자동 PATH 탐색·옆의 chromium 폴더·plugins.json·native-runtime.json을 사용하는 fallback이 없다. runtime의 admission과 첫 authorized 실행의 READY 상태는 구분한다. 기본 Client는 프로젝트 Java bridge를 실행하지 않으며 분석 엔진은 별도 [native host](../../apps/analysis-host/Cargo.toml)에 둔다.

사용자 Agent의 desktop 기능은 같은 사용자 세션의 Rust Broker/Guardian을 사용한다. 서비스는 고정 RACPNativeAgent 계정·private state·observer/controller IPC를 사용하는 별도 구현 경로다.

> [!IMPORTANT]
> native service provisioning·[현재 사용자 session 연결](desktop-client-guide.md#69-service의-현재-사용자-화면-연결-source-구현-실기-보류)·[SYSTEM handoff/세션 복구](desktop-client-guide.md#610-관리자-system-handoff와-세션-복구-source-구현-실행-보류)를 source로 연결했다. 실제 서비스/입력 인수·불확실한 partial 상태의 강제 복구·per-machine packaging은 미완료다. 이전 service-login.json, Task Scheduler Guardian 또는 기존 RACPAgent 설정을 native fallback으로 사용하지 않는다. source 경계를 실제 설치 절차나 동작 인수로 해석하지 않는다.

## 5. 생산 빌드와 보류된 검증

Rust 1.90.0·Windows x64 SDK/MSVC와 xtask를 사용한다. 기본 Client 빌드에는 Node/Python/Chromium 다운로드 단계가 없다.

```powershell
cargo run --locked -p xtask -- stage --platform win --arch x64 --build-id <unique-build-id>
cargo run --locked -p xtask -- verify-staging --platform win --arch x64 --build-id <unique-build-id>
```

출력은 `dist/client-native/<version>/win-x64/<build-id>/`다. 기존 batch를 보존하고 static CRT/Win64/import/hash/manifest를 확인한다. 제품 installer/portable archive 생성·실행, 테스트·fixture 추가·GUI smoke·native acceptance·macOS/Linux 빌드는 현재 보류다. 이전 bridge/credential 전용 Python acceptance harness는 폐기했으며 새 native 인수는 테스트 재개 후 준비해야 한다. 서버/Console 공통 계약·회귀 소스는 유지한다.

## 6. 관련 문서

- [Agent 서비스 구현 및 검증 계획](../spec/agent-service-implementation-and-test-plan.md)
- [Desktop Client Guide](desktop-client-guide.md)
- [전체 Rust 전환 계획](../spec/rust-client-migration-plan.md)
- [구현 현황](../quality/implementation-status.md)
- [Native Client ADR](../adr/ADR-0031-native-rust-client-and-isolated-state.md)
