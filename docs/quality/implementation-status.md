# RACP 구현 작업 및 검증 현황 (Implementation Status)

> **Document ID**: `DOC-QA-STATUS`\
> **Status**: Active · **Target Version**: v0.1.10\
> **Last Updated**: 2026-10-07 · **Classification**: Quality Assurance & Implementation Status (SSOT)

---

## 개요 (Overview)

본 문서는 **RACP (Remote Access and Control Protocol)** 프로젝트의 전체 단계별(Phase 0~9 및 확장 마일스톤) 구현 현황, 자동화 테스트 검증 수치, 물리 2-PC 실증 증거, 그리고 배포 패키징 상태를 총망라한 **단일 진실 공급원(Single Source of Truth, SSOT)**입니다.

과거 각 페이즈마다 작성되었던 개별 임시 결과 파일들을 본 문서로 완전히 통합 집대성하여, 개발 진행 상황과 검증 매트릭스를 일관되게 추적할 수 있도록 제공합니다.

---

## 목차 (Table of Contents)

- [1. 전체 단계별 구현 및 검증 요약표](#1-전체-단계별-구현-및-검증-요약표)
- [2. 단계별 상세 구현 및 검증 결과](#2-단계별-상세-구현-및-검증-결과)
  - [2.1 Phase 0~2: 기반 아키텍처 및 원격 RPC/보안](#21-phase-02-기반-아키텍처-및-원격-rpc보안)
  - [2.2 Phase 3: 파일 시스템 및 바이너리 아티팩트 전송](#22-phase-3-파일-시스템-및-바이너리-아티팩트-전송)
  - [2.3 Phase 4~5: ConPTY 지속 터미널 및 백그라운드 Job](#23-phase-45-conpty-지속-터미널-및-백그라운드-job)
  - [2.4 Phase 6: Gateway Web Console 및 실시간 이벤트 스트림](#24-phase-6-gateway-web-console-및-실시간-이벤트-스트림)
  - [2.5 Phase 7: 격리 브라우저 자동화 (Playwright)](#25-phase-7-격리-브라우저-자동화-playwright)
  - [2.6 Phase 8: 로컬 데스크톱 세션 Broker 및 입력 Guardian](#26-phase-8-로컬-데스크톱-세션-broker-및-입력-guardian)
  - [2.7 Phase 9: 리버싱 플러그인 (GDB / Ghidra)](#27-phase-9-리버싱-플러그인-gdb--ghidra)
  - [2.8 Phase 11~12 및 확장: 데스크톱 클라이언트 (0.1.0 ~ 0.1.8)](#28-phase-1112-및-확장-데스크톱-클라이언트-010--018)
- [3. 물리 2-PC 및 실제 AI 호스트(Codex) 실증 증거](#3-물리-2-pc-및-실제-ai-호스트codex-실증-증거)
- [4. 요구사항 추적 매트릭스 (Requirements Traceability)](#4-요구사항-추적-매트릭스-requirements-traceability)
- [5. 시스템 한도 및 제약 사항 (System Constraints)](#5-시스템-한도-및-제약-사항-system-constraints)
- [6. 관련 문서](#6-관련-문서)
- [7. Windows 작업 계획서 기준선 증거](#windows-plan-baseline)
- [8. v0.1.10 설치형·포터블 빌드 정의 및 검증](#desktop-build-0110)

---

## 1. 전체 단계별 구현 및 검증 요약표

| 단계 | 구현 내용 | 현재 검증 상태 | 합격 및 미완료 게이트 |
| :--- | :--- | :---: | :--- |
| **Phase 0** | uv/pnpm 모노레포, 공통 도메인/프로토콜 스키마, CI 워크플로, ADR 수립 | **PASS** | Windows 10 로컬 품질 및 Node 22 빌드 통과. Windows 11/Ubuntu 참조 OS 검증 대기 |
| **Phase 1** | Owner/Device 분리 인증, 외부 OAuth MCP Resource Server, WSS 연결/Epoch/Revoke | **PASS** | HTTPS 등록, WSS 재연결, Keycloak OIDC 실증 통과. 공개 OAuth 운영 배포 대기 |
| **Phase 2** | 명시적 Shell/Argv, cwd/env, 타임아웃/취소, 저널 멱등성(Deduplication), 최소 Job/Artifact | **PASS** | 동일 key 100회 동시 요청 side-effect=1 통과. 프로세스 트리 완전 회수 검증 |
| **Phase 3** | 파일 시스템 도구, 네임드 워크스페이스 격리, 범위 지정 100 MiB 재개 전송 | **PASS** | 네임드 워크스페이스 13개 테스트 및 100 MiB 끊김 재개(Resume) 통과 |
| **Phase 4** | ConPTY 지속 터미널, WebSocket 스트림 크레딧(Flow Control), 백프레셔 제어 | **PASS** | 대화형 셸 스트리밍, 윈도우 크레딧 제어, 세션 재접속 통과 |
| **Phase 5** | 영속 Job 큐, 마감시간(Deadline), 대용량 출력 스풀링 및 사후 첨부 복구 | **PASS** | 장기 실행 Job 폴링, 취소, 만료 출력 아티팩트 승격 통과 |
| **Phase 6** | Gateway Web Console (React + Vite), SSE 이벤트 피드, 터미널 뷰어 | **PASS** | 실제 Chromium 기반 E2E 14개 흐름 통과, OpenAPI 클라이언트 동기화 완료 |
| **Phase 7** | 격리 Playwright 브라우저 워커, 폼 자동화, 스크린샷 아티팩트, CDP opt-in | **PASS** | Chromium HTTP/CLI/CDP 프레임 및 스크린샷 아티팩트 통과 |
| **Phase 8** | 대화형 세션 Broker, Win32 UIA 관측, 사용자 물리 입력 감지 및 Guardian 해제 | **PASS** | 데스크톱 Broker IPC, 자체 GUI 10개 통과, 물리 입력 감지 시 원격 입력 즉시 차단 |
| **Phase 9** | 엄격한 플러그인 수퍼바이저, GNU GDB/MI 어댑터, Ghidra Headless 분석 연동 | **PASS** | 실제 GDB 중단점/메모리 덤프 10개 및 Ghidra 8개 테스트 통과 |
| **Phase 11~12**| Electron 44 데스크톱 클라이언트, 시스템 트레이, NSIS 설치 및 포터블 패키징 | **PASS** | Windows 10 x64 패키지 smoke 통과, 0.1.0 ~ 0.1.8 누적 릴리스 완료 |

---

## 2. 단계별 상세 구현 및 검증 결과

### 2.1 Phase 0~2: 기반 아키텍처 및 원격 RPC/보안
- **워크스페이스 구성**: `uv` 기반 8개 Python 패키지(`apps/agent`, `apps/cli`, `apps/gateway`, `packages/*`) 및 `pnpm` 기반 2개 프론트엔드 패키지(`apps/client`, `apps/console`).
- **상태 머신 및 멱등성**: SQLite 선행 저널 트랜잭션을 통해 클라이언트가 제공한 `idempotency_key`를 1회만 실행하고, 재시작 시 미확정 상태는 `UNKNOWN`으로 보존하여 자동 오실행을 방지.
- **인증 분리**: 소유자(Owner)와 기기(Device) 자격 증명을 물리적으로 분리하고 DPAPI/0600으로 암호화 보존.

### 2.2 Phase 3: 파일 시스템 및 바이너리 아티팩트 전송
- **네임드 워크스페이스**: 기본 `default` 외 최대 15개 폴더 인가. 인가 폴더 상위 탈출(`..`, Symlink, Junction, UNC) 원천 차단.
- **아티팩트 전송**: 100 MiB 대용량 바이너리 파일을 청크 분할하여 전송하고, 네트워크 단절 시 Range 요청을 통해 안전 재개(Resume) 성공 확인 (최종 SHA-256 일치).

### 2.3 Phase 4~5: ConPTY 지속 터미널 및 백그라운드 Job
- **ConPTY 지속 터미널**: Windows 가상 터미널(ConPTY)을 바인딩하여 네트워크 단절 시에도 세션을 보존하며, 재접속 시 버퍼 재생(Replay) 제공.
- **스트림 크레딧**: 슬라이딩 윈도우 크레딧 메커니즘을 적용하여 수신자 버퍼 오버플로 방지.
- **비동기 Job**: 장기 작업에 대해 Job ID를 발급하고 비동기 폴링, 타임아웃, 취소 영수증 수집 지원.

### 2.4 Phase 6: Gateway Web Console 및 실시간 이벤트 스트림
- React 19 + Vite 기반 SPA 콘솔.
- Server-Sent Events (SSE) 기반 실시간 장비 상태 피드, 토큰 발급 폼, 웹 소켓 터미널 스트리밍 뷰어 지원.
- 실제 Chromium E2E 14개 자동화 검증 완료.

### 2.5 Phase 7: 격리 브라우저 자동화 (Playwright)
- Playwright Chromium 인스턴스를 격리된 자식 프로세스로 실행.
- 웹 페이지 관측, 폼 요소 자동 입력, Full-page PNG 스크린샷 캡처 및 아티팩트 저장 검증.

### 2.6 Phase 8: 로컬 데스크톱 세션 Broker 및 입력 Guardian
- 세션 0 서비스 격리 우회를 위한 대화형 데스크톱 Broker 및 보안 IPC 파이프.
- UI Automation (UIA) 트리 탐색 및 화면 요소 상호작용.
- **입력 안전 가디언 (Guardian)**: 원격 입력 중 사용자가 물리 마우스/키보드를 조작하면 원격 입력을 즉시 인터럽트하고, Broker 비정상 종료 시 입력 키 고착을 즉시 해제.

### 2.7 Phase 9: 리버싱 플러그인 (GDB / Ghidra)
- 엄격한 화이트리스트 JSON-RPC 플러그인 수퍼바이저.
- GNU GDB 17.1 연동: 프로그램 시작, 중단점 설정, 레지스터 조회, 메모리 덤프 수집 검증.
- Ghidra 12.1.4 Headless 연동: 바이너리 자동 분석, 디컴파일 정보 조회 및 프로세스 크래시 복구 검증.

### 2.8 Phase 11~12 및 확장: 데스크톱 클라이언트 (0.1.0 ~ 0.1.8)
- **v0.1.0**: Electron 44 기반 최초 GUI 및 포터블 런타임 번들링.
- **v0.1.1**: Windows 사용자 로그인 시 자동 시작(HKCU Run) 옵션 추가.
- **v0.1.2**: 시스템 트레이(Tray) 최소화, 실시간 활동 대시보드(최근 40개 이벤트), 안전한 완전 종료 추가.
- **v0.1.3**: NSIS 설치/업그레이드/제거 수명주기 관리 및 상태 파일 SHA-256 자동 백업 추가.
- **v0.1.4**: 최초 온보딩 진단 강화 (오류 코드별 안전 분기 및 손상 설정 보존).
- **v0.1.5**: 단일 `.racp` 연결 파일 드롭을 통한 원클릭 온보딩 추가.
- **v0.1.6**: 등록 정보 수정 및 자동 복구 백엔드 구현.
- **v0.1.7**: 설정 오류 발생 시 [등록 정보 편집] 폼으로 즉시 진입하는 다이렉트 UX 개편.
- **v0.1.8**: 현재 Windows 로그인 세션의 화면 캡처 및 관측, 일반 프로세스 메모리 안전 읽기 추가.

---

## 3. 물리 2-PC 및 실제 AI 호스트(Codex) 실증 증거

### 3.1 물리 2-PC LAN 환경 실증
- **호스트 PC (Gateway)**: `192.168.29.140:8765`
- **원격 대상 PC (Agent)**: `192.168.29.141`
- **실증 내역**:
  - 원격 141 PC에서 포터블 클라이언트 실행 후 아웃바운드 WSS 연결 수립.
  - 원격 파일 시스템 한글 파일 읽기/쓰기 및 해시 검증 통과.
  - 원격 ConPTY 대화형 셸 실행 및 출력 스트리밍 통과.
  - 동일 `idempotency_key` 100회 중복 요청 시 부작용 카운터 1회 유지(counter=1) 확인.
  - 100 MiB 대용량 아티팩트 전송 중단 및 재개 통과.

### 3.2 실제 AI 호스트 (OpenAI Codex) 연동 실증
- **연동 방식**: Loopback HTTPS + OAuth MCP Resource Server (PKCE S256).
- **실증 내역**:
  - Codex 클라이언트에서 RACP 84개 이상의 MCP 도구 카탈로그 자동 인식.
  - 실제 원격 141 PC를 타깃으로 `fs_read`, `fs_write`, `shell_exec`, `job_poll`, `conpty` 호출 및 성공 응답 수신.
  - 격리 브라우저 조작 및 MCP 인라인 PNG 화면 미리보기 반환 확인.
  - Windows 화면 관측 기능(3840×2160 해상도 화면 캡처) 실제 수신 확인.
  - 증거 로그: `dist/codex-chat-141-20261006.json`.

---

## 4. 요구사항 추적 매트릭스 (Requirements Traceability)

| 요구 ID | 검증 대상 테스트 및 실증 증거 | 검증 상태 |
| :--- | :--- | :---: |
| **AUTH-01** | `test_auth_01_device_token_cannot_control_owner_api` (등록 재사용 차단) | **PASS** |
| **AUTH-02** | Keycloak OAuth PKCE 실연동, CSRF/Cookie/Origin 방어 테스트 | **PASS** |
| **AUTH-03** | Lease Watchdog 만료 및 토큰 취소(Revoke) 시 프로세스 정리 테스트 | **PASS** |
| **RPC-01** | `test_rpc_01_one_hundred_concurrent_mutations_execute_once` (100 동시 호출 1회 실행) | **PASS** |
| **RPC-02** | Lost-result 재연결 테스트, Agent 저널 재시작 UNKNOWN 보존 테스트 | **PASS** |
| **RPC-03** | 엄격한 Discriminated Schema, 프레임 초과 차단 단위/통합 테스트 | **PASS** |
| **SHELL-01**| 한글/공백 argv, stdout/stderr 분리, nonzero exit 수집 테스트 | **PASS** |
| **LIFE-01** | 자식/손자 프로세스 타임아웃, Job 취소, Job Object 완전 회수 테스트 | **PASS** |
| **POLICY-01**| Default deny, 승인 토큰 만료/재사용 방지 테스트 | **PASS** |
| **FS-01**   | Junction/Symlink 차단, Atomic Write, Unicode/BOM 보존 테스트 | **PASS** |
| **ART-01**  | 100 MiB HTTP 끊김 재개(Range Resume), 최종 SHA-256 일치 테스트 | **PASS** |
| **PTY-01**  | ConPTY 지속 세션, 슬라이딩 윈도우 크레딧 백프레셔 테스트 | **PASS** |
| **UI-01**   | Chromium E2E 콘솔 로그인, 승인, 취소, SSE 스트림 갭 복구 테스트 | **PASS** |
| **RE-01**   | GDB/MI launch 10개, Ghidra Headless 8개 단위/통합 테스트 | **PASS** |

---

## 5. 시스템 한도 및 제약 사항 (System Constraints)

- **출력 인라인 텍스트 한도**: 기본 64 KiB (초과 시 아티팩트로 자동 스풀링).
- **셸 원본 수집 상한**: stdout + stderr 합계 최대 64 MiB.
- **바이너리 파일 아티팩트 스트리밍**: 1 GiB 상한.
- **에이전트 스풀 스토리지 예약**: 최대 10 GiB / 64건, 기기당 동시 활성 전송 최대 2건.
- **동시 인가 워크스페이스**: 기본 `default` 외 최대 15개.

---

## 6. 관련 문서

- [기술 문서 포털](../README.md)
- [시스템 개발정의서 v1.1](../spec/racp-specification-v1.1.md)
- [런타임 및 플랫폼 호환성 매트릭스](compatibility.md)
- [Windows 릴리스 인수 게이트](windows-release-gates.md)
- [데스크톱 클라이언트 가이드](../guides/desktop-client-guide.md)
- [2-PC 실증 랩 가이드](../guides/two-pc-lab-guide.md)
- [Windows 작업 및 테스트 계획서](../spec/windows-engineering-plan.md)

<a id="windows-plan-baseline"></a>

## 7. Windows 작업 계획서 기준선 증거

> [!NOTE]
> 아래는 2026-10-06 계획 작성 시 기록한 기준선 증거를 2026-10-07 공식 계획서 재발행 과정에서 이관한 것이다. 이번 문서 작업 중 기능 시험을 다시 실행한 결과가 아니며, v0.1.9의 전체 인수 통과를 의미하지 않는다.

| 기능/검사 | 당시 확인된 결과와 출처 | 남은 확인 |
|---|---|---|
| Windows 기본 검사 | 당시 `dist/test-results.xml`: 총 359, 341 passed, 18 skipped, failure/error 0 | 변경 후보 재실행, skipped 사유·후속 결과 분리 |
| Console E2E | 당시 `dist/console-test-results.xml`: 14 passed | 최종 후보 사용자 흐름 재검 |
| 로컬 Windows GUI | 사용자 첨부 실행 기록: 선택형 11개 통과 | 실제 141의 입력·표시·session 조합 |
| Client E2E/Node | 사용자 첨부 기록: Client E2E 및 Node 10개 통과 | 최종 packaged 후보와 clean PC 수명 |
| 실제 두 PC 기본 작업 | 파일·명령·ConPTY·Job 취소·Artifact·재연결 확인 | 동일 후보의 묶음 workflow 회귀 |
| 실제 141 desktop 읽기 | session/window/monitor 조회, 3840×2160·150% 캡처 확인 | 입력·다중 monitor/DPI·잠금/RDP |
| 실제 141 process/memory | 최신 사용자 첨부 기록: spawn 성공, 자체 fixture 메모리 32바이트 예상값 일치, 정리 완료 | 큰 읽기·Artifact·취소·identity·권한 경계 |
| 실제 141 desktop 입력 | 호스트 차단으로 click/type 인수시험 미완료 | 실제 Codex 도구 전달·입력·독립 결과 검증 |
| Linux 컨테이너 | 사용자 첨부 기록: 293 passed / 40 skipped / 21 failed / 5 errors | 원인별 backlog 유지, Windows 이후 검증 |

첨부 기록의 원본은 당시 사용자가 제공한 `붙여넣은 텍스트.txt`이며, 로컬 보관 위치는 `C:/Users/GhostShell/.codex/attachments/21687dc6-d281-497e-abcb-5cd3473e3b2c/붙여넣은 텍스트.txt`다. 로컬 XML 수치는 당시 계획 작성 시 집계한 값이고, 다른 수치는 사용자 첨부 실행 기록에 따른 것이다. 원본 보관 경로는 각 환경에서 달라질 수 있다.

메모리 읽기 실행 전 차단 기록 뒤에 위의 제한된 후속 성공이 추가됐으므로, 이전 `NOT_RUN`과 현재 확인 범위를 구분한다. 32바이트 성공으로 큰 결과/Artifact·전체 dump·debugger 연결을 통과 처리하지 않는다. 로컬 GUI 통과와 실제 원격 input 통과도 구분한다.

> [!IMPORTANT]
> 본 문서 위쪽의 개별 Phase/검사 PASS는 해당 구현·시험 범위의 증거다. 전체 Windows 핵심 인수 또는 정식 릴리스 완료는 [작업 계획의 종료 기준](../spec/windows-engineering-plan.md#section-11)에 따라 별도로 판정한다.

공식 계획서는 `docs/spec/windows-engineering-plan.md`에 재발행했다. 과거 한글 경로의 동일 본문을 중복 생성하지 않는다. 이번 수정의 검증 범위는 문서 구조·링크·계획 항목 보존이며, 실제 기능 검사는 W01부터 후보를 고정해 진행한다.

2026-10-07 문서 재발행 검증: 변경 문서 4개의 메타데이터·목차·코드 fence와 상대 파일/anchor 링크 81개를 확인해 오류 0건이었다. 기존 계획의 작업 ID 12개, 시험 ID 54개, display/session 조합 ID 8개를 변경·중복 없이 보존했다. 명령에 참조한 script/test 파일도 존재함을 확인했고 `docs/protocol/`은 변경하지 않았다. `scripts/version.py show` 결과는 0.1.9이며 제품 버전은 변경하지 않았다.

<a id="desktop-build-0110"></a>

## 8. v0.1.10 설치형·포터블 빌드 정의 및 검증

2026-10-07 변경에서는 [AGENTS.md](../../AGENTS.md#8-desktop-build-definition-desktop-build-skill), [Desktop Build 스킬](../../.agents/skills/desktop-build/SKILL.md), [운용 가이드](../guides/desktop-client-guide.md#6-개발-빌드-및-패키징-절차)에 플랫폼별 빌드 정의를 반영했다. PATCH는 `0.1.9 → 0.1.10`으로 한 번 증가했고 `uv.lock`의 workspace 버전을 동기화했다.

| 플랫폼 | 설치형 | 포터블 | 이번 실행 범위 |
| :--- | :--- | :--- | :--- |
| Windows x64 | NSIS setup EXE | 단일 portable EXE 및 ZIP | 네이티브 개발 빌드 |
| macOS x64 / arm64 | DMG | `.app` ZIP | 구조 정의; mac-arm64 dry-run만 수행 |
| Linux x64 / arm64 | deb | AppImage | 구조 정의; linux-x64 dry-run만 수행 |

공통 진입점은 `scripts/build_client.py`이며 기본 대상은 Windows x64다. 출력은 `dist/client-desktop/<version>/<target>-<arch>/`, Agent 스테이징은 `dist/client-agent/<version>/<target>-<arch>/`로 분리했다. `stage_client_agent.py`의 `0.1.0` wheel 고정 참조와 이전 `client-agent-v8` 경로를 제거했다. pre-pack hook은 OS·아키텍처·현재 버전·lock digest·파일 해시를 검사한다.

Desktop CI의 push/PR/기본 수동 실행은 Windows만 선택한다. 수동 실행의 `include_deferred_platforms=true`는 이후 사용자가 요청한 macOS arm64/Linux x64 빌드를 활성화할 때 사용한다. 이번 작업에서는 해당 runner나 Linux 컨테이너를 실행하지 않았다.

| 검사 | 실제 결과 |
| :--- | :--- |
| Ruff (저장소 필수 범위 + 변경 Python 빌드 스크립트) | PASS |
| Mypy | 135 source files, 오류 0 |
| 버전 및 Desktop Control Python 테스트 | 16 passed (버전 6, Desktop Control 10) |
| 프론트엔드 TypeScript/Vite 빌드 | PASS |
| Client Node 테스트 | 11 passed, 실패 0; 플랫폼/버전/lock/변조/경로 탈출 거절 포함 |
| 독립 Agent import | `native Agent imports OK` |
| HTTPS/WSS Client E2E | PASS: 연결 파일, 만료/변경 거절, 토큰 재시도, 등록 복구, 실행 중 설정 수정 거절, 트레이·종료·Job 정리 |
| Packaged Windows smoke | PASS: 새로운 `--user-data-dir` 격리 확인 후 패키지 버전·Agent bridge·연결 파일 preview·토큰 비노출·CA 진단·종료 |
| 단일 portable EXE smoke | PASS: 런처 추출·실행, 프로필 인자 격리, v0.1.10 표시, native Agent bridge 및 완전 종료 |
| Desktop Build / Version Manager 스킬 validator | 두 스킬 PASS (`python -X utf8`) |
| 문서 메타데이터 및 상대 파일 링크 | 최종 AGENTS/두 스킬/가이드/구현 현황 5개 문서, 상대 파일 링크 31개 정상 |
| Desktop CI YAML | 구문 파싱 PASS; 원격 CI 실행은 수행하지 않음 |
| macOS/Linux | `build_executed=false` dry-run; 네이티브 빌드 및 인수 미실행 |
| Windows ZIP | CRC PASS; 포함된 Agent 파일 4,766개의 SHA-256이 manifest와 일치 |

Windows x64 실제 산출물은 `dist/client-desktop/0.1.10/win-x64/`에 생성했다. `build-manifest.json`에도 아래 크기와 SHA-256을 저장했다.

| 산출물 | 크기 (bytes) | SHA-256 |
| :--- | ---: | :--- |
| `RACP-Client-0.1.10-win-x64-setup.exe` | 398774108 | `36ac1ab5dcc47e37cdd76a2131387f19a706f670be8be4603e1253ade9871c8c` |
| `RACP-Client-0.1.10-win-x64-portable.exe` | 398561456 | `d35eeea164873681de9e0e86cbcccc6819df8e6657d02ff46a7eac2b07c96f56` |
| `RACP-Client-0.1.10-win-x64.zip` | 542667560 | `b3588846d6eec40e636595545c5b1475a9828b824adde1cb962493273044e8b3` |

> [!NOTE]
> 패키지 smoke는 기존 사용자 등록 상태에 접근하지 않는 새 프로필로 실행했다. 첫 시험의 숨긴 창에서 click/screenshot timeout이 발생해, 격리 프로필을 유지한 채 창을 표시하는 시험 방식으로 수정하고 최종 PASS를 확인했다. 단일 EXE는 `_electron.launch`의 inspector pipe를 런처가 전달하지 않아 직접 연결 timeout이 발생했으므로, 전용 `portable-smoke.cjs`의 loopback Chromium 연결로 추출·실행·Agent IPC·종료를 확인했다. 실패한 시험의 소유 프로세스가 남지 않았음을 확인했다. 제품 연결 오류로 판정하지 않았다.

> [!IMPORTANT]
> 이번 증거는 unsigned 개발 빌드 및 해당 시험 범위다. clean PC 설치/업그레이드/제거, 실제 두 PC 전체 인수, 서명/notarization 또는 macOS/Linux 실행 통과를 의미하지 않는다.
