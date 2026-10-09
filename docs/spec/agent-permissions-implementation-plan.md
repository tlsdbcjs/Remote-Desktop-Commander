# Agent 세부 권한 구현 계획

> **Document ID**: `DOC-PLAN-AGENT-PERMISSIONS`\
> **Status**: Superseded · **Target Version**: v0.1.20 Python 기준선 / v0.1.19 초기 기준선\
> **Last Updated**: 2026-10-10 · **Classification**: Implementation Plan

> [!IMPORTANT]
> 사용자 요청에 따라 Client 영역을 Rust로 전체 전환하고 레거시를 유지하지 않는다. **후속 실행 기준은 [Rust Client 전환 계획](rust-client-migration-plan.md)**이다. 이 문서는 Python/Electron 구현의 과거 진행과 요구사항 참조로 보존하며 추가 Python 기능 확장의 작업 지시로 사용하지 않는다. 기능·권한·검증 요구는 새 계획으로 이어받는다.

> **작업 원칙:** 아래 작업을 순서대로 구현한다. 공유 dirty worktree의 다른 변경을 보존하고 전체 변경을 임의 commit하지 않는다.

**Goal:** Client의 세부 권한 설정을 Agent가 실제 검사하도록 구현하고, 기존 host MCP 분석과 원격 OS 기능을 검증 가능한 Provider로 확장한다.

**Architecture:** 공통 permission catalog와 strict local settings를 immutable snapshot으로 compile한다. Agent/Broker의 dispatch·stream·출력 guard와 Client 등록/설정 UI가 같은 catalog를 사용한다. 현재는 로컬 권한만 적용하고 미래 조직 정책의 제약 공급 경계만 유지한다.

**Tech Stack:** Python 3.12/Pydantic, 기존 RACP Registry·Provider·protected settings, Electron IPC·React·TypeScript, pytest·Node·Playwright.

**Spec:** [Agent 세부 권한 및 원격 OS 기능 아키텍처](agent-permissions-and-capabilities.md)

## 목차

- [1. 범위와 불변 조건](#1-범위와-불변-조건)
- [2. 검토 초점](#2-검토-초점)
- [3. 구현 작업](#3-구현-작업)
- [4. 완료 증거](#4-완료-증거)

## 1. 범위와 불변 조건

- 제품 build·패키징은 사용자 요청까지 보류한다. 테스트·타입 검사·source 실행은 진행한다.
- Gateway→Client 정책 배포·관리 설정 push/pull·Enterprise worker를 구현하지 않는다.
- permission catalog의 18개 category/143개 후보를 실제 지원·CLI·미구현으로 구분한다. 계획된 항목을 지원한다고 광고하지 않는다.
- 소유 Device/credential/workspace/profile·기존 desktop opt-in을 보존하고 새 permission은 자동 부여하지 않는다.
- 현재 OS token 권한을 넘어서는 보호 대상·secure desktop·키 없는 TLS 평문을 제공한다고 주장하지 않는다.
- 임의 script/argv/terminal 실행의 OS 행위를 checkbox만으로 격리한다고 설명하지 않는다.
- 실행 profile·로컬 ceiling·대상/예산·승인·실제 availability를 모두 검사하고 deny 우선으로 적용한다.
- 새 native helper는 Client에 전체 분석 IDE를 사전 설치하는 구조를 기본 요구하지 않는다.
- PATCH는 이번 배치에서 한 번만 증가한다. 재시도마다 증가하지 않는다.
- 모든 실행·회귀 결과와 결정 기록은 [구현 현황](../quality/implementation-status.md)에 누적한다.

## 2. 검토 초점

1. 파일 편집 off인데 다른 실행 진입점으로 실행 가능한 경우: 각 진입점의 실행 권한과 실제 시행 수준을 일치시킨다.
2. binary read/Artifact·process cmdline·terminal stream 같은 간접 출력: 민감한 자료 전달도 검사한다.
3. v1 설정·unknown ID·future field·손상 파일: 기존 identity를 유지하고 permission 확대 없이 거부/이전한다.
4. 권한 변경과 await/Handle renewal 경합: 오래된 snapshot으로 새 입력/출력을 수행하지 않는다.
5. 없는 runtime·비관리자 token·잠긴 session·미구현 기능: deny와 unavailable을 구분하고 다른 대상으로 fallback하지 않는다.

## 3. 구현 작업

### Task 1 — 공통 catalog·binding·local permission model

**Files:** `packages/protocol/src/racp_protocol/permissions.py`, `permission_catalog.json`, `packages/policy/src/racp_policy/permissions.py`, `scripts/permission_catalog.py`, `tests/unit/test_permission_catalog.py`, `tests/unit/test_local_permissions.py`.

**Interfaces:** `permission_catalog()`은 stable ID/category/label/implementation/operation metadata를 반환한다. `required_permissions(operation, payload)`는 payload별 필수 ID를 반환하고 unknown operation은 거부한다. `LocalPermissions`는 strict versioned grants/constraints이며 compiler는 불변 snapshot과 revision을 만든다.

- [x] RED: 143 ID·18 category, 현재 Registry의 모든 operation mapping, create/replace/copy·desktop·exec 경계, unknown ID/field·미구현 권한·constraint 거부 시험 작성.
- [x] `pytest tests/unit/test_permission_catalog.py tests/unit/test_local_permissions.py`로 실제 실패 확인.
- [x] catalog와 mapping, strict model·compile/decision 구현. JSON asset은 Python 및 Client가 같은 SSOT를 사용한다.
- [x] 동일 시험 GREEN과 Ruff/mypy 확인. descriptor metadata와 authorization binding을 혼동하지 않는다.

### Task 2 — 보호 설정 v2·등록/편집·이전

**Files:** Agent `settings.py`, `settings_edit.py`, `connect.py`, `desktop_control.py`, `main.py`, `background.py`; Client `backend.cjs`, `main.cjs`; 기존 설정/등록 tests 및 신규 permission settings tests.

**Interfaces:** `AgentSettings.permissions`는 로컬 저장 모델이다. v1→v2 변환은 현재 catalog의 알려진 기존 범위만 보존한다. 신규 등록은 명시한 local permissions를 저장한다. 편집은 credential identity와 revision/lock/backup 절차를 유지한다.

- [x] RED: v1 true/false opt-in 이전, v2 restart 유지, 손상/unknown schema, 새 권한 default off, IPC 입력·저장 크기 경계 시험.
- [x] strict 저장·이전 및 등록/설정 IPC를 구현한다. 서버 정책 payload는 수용하지 않는다.
- [x] 동일 시험 GREEN; CLI/foreground/background/service 시작 경로가 같은 모델을 읽는지 확인한다. source open_agent/settings 검증이며 신규 설치 binary·121 인수는 별도다.

### Task 3 — Agent/Broker/stream 권한 검사

**Files:** Agent `runtime.py`, 신규 `authorization.py`, `terminal_streams.py`, Provider 출력/민감 항목 처리, Broker guard; 필요한 최소 operation 승인 metadata 및 Gateway operation 승인 경로.

**Interfaces:** immutable snapshot을 `authorize(operation,payload,context)`와 전달 전 guard에 적용한다. config는 local source이며 Gateway가 local settings를 바꾸지 않는다. 승인 metadata를 추가한다면 operation/target/revision에 결합하고 caller 입력을 승인 사실로 신뢰하지 않는다.

**현재 진행:** 기존 Agent/Broker·출력/stream·권한 폐기 guard에 더해 filesystem worker의 budget check에도 실행 시점의 권한/revision 검사를 연결했다. patch의 교체 직전 폐기 시 원본과 임시 파일 정리를 확인했다. 실행 중 설정 변경 UI나 로컬 승인 authority의 구현 완료로 간주하지 않는다.

- [ ] RED: local off의 direct RPC 거부, profile 확대·ask 우회·다른 target/workspace 거부, cmdline/Artifact/stream 비노출, await 이후 재검 시험.
- [ ] 접수·dispatch·await 이후·입력·renew·출력 검사를 구현한다. 내부 소유 cleanup은 권한 축소 뒤에도 수행한다.
- [ ] 권한 축소 시 소유 session/lease를 제한 시간 내 폐기하고 이전 승인·snapshot을 재사용하지 않는다.
- [ ] 기존 전체 회귀와 실제 own source Agent에서 거부·허용·정리를 확인한다.

### Task 4 — 등록/설정 공통 category UI와 제약 편집

**Files:** `apps/client/src/PermissionsEditor.tsx`, `permissions.ts`, 기존 `main.tsx`·styles, Client Node/Playwright source tests.

- [ ] RED: 초기 등록 두 경로와 설정 편집의 payload 일치, master off/복원·mixed 상태·새 leaf off, profile/availability·광역 실행 경고·저장 실패 보존 시험.
- [ ] 공통 catalog에서 category/leaf를 렌더링하고 제약을 편집한다. 미구현 항목은 상태와 원인을 표시한다.
- [ ] pinned Node 테스트·TypeScript --noEmit·제품 build 없는 source Playwright로 검증한다.

### Task 5 — 구조화된 OS Provider 확장

**Files:** 신규 network/system/clipboard/storage/config Provider 및 해당 protocol model/Registry/capability·policy·budget·수명주기 tests.

**현재 진행:** system info/resources/locale/safe environment, network interfaces/connections, storage volumes, scoped IPv4 capture, mini/full process dump 및 session Broker의 text clipboard state/read/write를 추가했다. Windows 서비스 조회·설치 소프트웨어 registry metadata 조회도 별도 typed RPC와 권한에 연결했다. 실제 121 source Agent의 권한·재시작, capture→기존 Wireshark MCP, dump→기존 WinDbg MCP, Win32 clipboard 및 Hyper-V W11의 live debugger/proxy 경로 증거는 [구현 현황 §27](../quality/implementation-status.md#agent-permissions-v020)에 기록했다. 새 inventory의 W11 원격 인수는 기존 Agent OFFLINE으로 대기 중이며 나머지 typed OS 기능은 미완료다.

파일 내용 검색과 해시 전제를 갖는 여러 파일 exact patch/dry-run/diff 및 partial batch 실패 계약도 소스에 추가했다. 실제 host 파일 API와 RACP MCP 경로를 검증했으며 W11은 연결 복구 뒤 신규 기능의 최소 인수를 수행한다. 여러 파일 전체의 OS 원자성은 지원한다고 표시하지 않는다.

- [ ] network 관측·수집, memory/dump·clipboard·system 기능을 기존 generic shell 우회 없이 typed operation으로 추가한다.
- [ ] catalog의 각 후보를 실제 OS 지원·권한·backend에 연결하고 없는 기능은 unavailable로 유지한다.
- [ ] 파괴적 storage·계정·전원 기능은 명시한 검증 환경에서만 실제 실행 인수한다. fixture/실제 결과를 분리한다.
- [ ] 기능별 RED→GREEN, 실제 Client OS·hash·budget·취소·소유 자원 정리를 검증한다.

### Task 6 — 기존 native MCP의 live debugger/proxy 연결

**현재 진행:** CDB reverse TCP의 복수 채널 누락을 해결하고 공통 Agent·SDK 전송 코어·strict frame 계약, native session RPC·Gateway owner 인증 WSS carrier와 해시로 pin한 관리 CDB runtime을 연결했다. 사용자가 대상을 Hyper-V W11로 변경했으며 기존 native WinDbg MCP에서 실제 VM own target의 read/step/resume/break/detach 및 확장/PDB를 적용한 PEB 조회를 검증했다. 양방향 process-bound proxy carrier도 추가해 기존 native mitmproxy MCP로 W11 HTTP 수집·특정 URL의 header 인터셉트·POST 재전송을 검증했다. 이미 닫힌 session의 반복 close 오류를 실제 인수에서 발견해 수정하고 새 source Agent로 정리까지 성공했다. 원본 MCP와 기존 Client binary를 교체하지 않았다. 나머지 backend·TLS별 조건·배포·설치 인수는 미완료다. [구현 현황 §27](../quality/implementation-status.md#agent-permissions-v020)의 실패/성공·정리 범위를 따른다.

- [x] host-tool / agent-native / managed helper 경로를 구분하고 native debugger/proxy의 요구 transport를 확인한다.
- [x] owner/boot/lease/peer/bytes에 결합한 outbound duplex 경로와 필요한 관리 helper를 구현·검증한다. Client 인바운드 포트를 기본 열지 않는다.
- [x] 실제 own target attach/break/step/continue/detach, remote HTTP proxy/replay와 cleanup을 기존 MCP로 검증한다.
- [ ] native runtime/OS 권한·지원 환경의 진짜 한계를 개별 기록한다. 미실행을 불가나 PASS로 바꾸지 않는다.

## 4. 완료 증거

각 Task의 실제 테스트 결과, UI 화면, source hash, 원격 Device/boot/epoch·target·Artifact hash·cleanup은 [구현 현황](../quality/implementation-status.md)에 기록한다. source-only 검증은 새 binary 설치 인수가 아니다. Native OS/display/session·장시간 부하·package 인수의 미검증 항목은 유지하고, Enterprise 정책 배포 제외 조건도 유지한다. 모든 필수 항목의 현재 증거가 갖춰지기 전 전체 goal을 완료로 표시하지 않는다.
