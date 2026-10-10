# RACP 구현 작업 및 검증 현황 (Implementation Status)

> **Document ID**: `DOC-QA-STATUS`\
> **Status**: Active · **Target Version**: v0.1.11\
> **Last Updated**: 2026-10-10 · **Classification**: Quality Assurance & Implementation Status (SSOT)

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
- [9. Client·Agent Rust 전환 설계 검토](#client-rust-design)

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

---

## 9. Client·Agent Rust 전환 설계·구현 계획 <a id="client-rust-design"></a>

- **2026-10-10 상태**: 사용자 “진행해”로 [Rust 전환 설계](../spec/client-rust-migration.md) 승인. [구현 계획](../spec/client-rust-implementation-plan.md) 10개 task·50개 단계 작성 및 자체 검토 완료. 초기 설계·계획 단계 기록이며, 이후 사용자 실행 승인과 Rust 구현 결과는 아래에 기록했다.
- **조사 기준선**: v0.1.10, Electron/React UI와 Python Agent. 승인 범위는 Tauri/Rust 호스트와 Agent 전환 및 기존 React 화면 유지다. Gateway·CLI는 별도 앱으로 유지한다.
- **문서 검증**: `git diff --check` 통과. 설계안과 문서 인덱스 2개의 상대 파일 링크 38개, 설계 메타데이터·목차·TODO/TBD 부재 확인 통과.
- **계획 검증**: 설계·계획·인덱스 4개 문서의 상대 파일 링크 46개, metadata·TODO/TBD 부재, task 10개·checkbox 50개 확인 통과. diff whitespace 검사 통과.
- **기존 동작 기준선 검사**: `.venv/bin/python -m pytest tests/unit/test_version.py tests/contract/test_schema_drift.py -q` — 7 passed. `node --test apps/client/tests/*.test.cjs` — 11 passed, 실패·skip 0. Rust 이식 결과 검증이 아닌 기존 구현 기준선이다.
- **실행 검증**: 초기 조사 당시 Rust 구현/테스트/빌드 및 Windows native 패키징은 수행하지 않았다. 실행 단계에서 Linux 환경에 고정 Rust toolchain을 설치했다. macOS/Linux native 빌드는 기존 정책에 따라 미실행이다.

### Rust 실행 작업 시작 (2026-10-10)

- 사용자 “권장 방향으로 끝까지 구현해”로 구현 계획과 Native 실행 승인. 추가 설계/계획 승인 대기는 해제했다.
- 배치 버전 0.1.11로 동기화하고 uv.lock을 갱신했다. Cargo workspace/toolchain/lock과 기존 계약 검증기를 구현했다.
- Rust 계약 6개 검사에서 78개 operation의 Python `validate_payload` 기준 fixture 정규화가 일치했다. 엄격한 숫자 타입·bridge 한도·관계 검증·터미널 바이트 한도를 포함한다.
- Rust 로컬 상태 8개 검사에서 기존 credential JSON, 0600·링크 차단·atomic write, 설정 revision/backup/Agent lock, 연결 파일 digest/만료/주소 한도를 확인했다.
- Rust 등록 4개 검사에서 실제 loopback HTTP를 사용해 등록 성공·토큰 비노출·로컬 preflight·4 KiB 응답 한도·403 거부를 확인했다.
- 로컬 `cargo test --workspace`: 18 passed; `cargo clippy --workspace --all-targets -- -D warnings`: PASS. Python 버전/schema 8 passed, Ruff PASS, Mypy 135 sources PASS.
- Windows Rust CI [37979015221](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/37979015221)는 fmt/clippy와 16개 native 검사를 통과했다. DPAPI·파일 잠금의 Windows 구현 증거다.
- 검토 및 Windows CI용 [Draft PR #1](https://github.com/tlsdbcjs/Remote-Desktop-Commander/pull/1)을 생성했다. merge/publish는 수행하지 않았다.
- Task 3에서는 기존 schema의 저널·UNKNOWN/late result·retention clock·출력 예약과 영속 첨부, HTTPS/WSS handshake/epoch/reconcile/heartbeat/lease, 취소·deadline·job progress, 인증된 loopback background 제어를 구현했다.
- Rust workspace 28개 검사 PASS: 계약 6, 경로/상태 8, 저널 5, 출력 2, dispatch 3, 등록 4. 동일 key 100회 실행 gate=1과 정책·lease 차단, deadline 정리 검사를 포함한다.
- 실제 Gateway와 별도 Rust executable의 HTTPS 통합 5개 검사 PASS: 기존 Python credential 읽기·중복 시작/PID 유지·완전 종료, stale instance 거부, 잘못된 CA에서 연결 차단, Python journal 성공 결과 복구, 100 MiB 업로드/다운로드 재개 및 잘못된 SHA-256 거부.
- protocol union validator를 캐시하여 프레임별 schema 재컴파일을 제거했고 기존 78개 operation 정규화 fixture를 그대로 통과했다.
- Task 3의 native Windows 확장 검사는 새 CI 실행 대기다. Tauri UI·provider·패키징 전환 및 기존 Python 삭제는 아직 미완료다.

- Task 4의 파일 provider 10개 operation을 Rust로 이식했다. 이름별 workspace·링크/경로 탈출·root identity 경계, 원자적 쓰기/CAS/append offset, 인코딩·BOM·개행 보존, revision에 연결된 cursor, copy/move/delete·부분 보고서 및 Artifact 입출력을 포함한다. 셸·프로세스·터미널 provider는 진행 중이며 Task 4 전체 완료는 아니다.
- 이번 로컬 검사: Rust workspace 35 passed, clippy all-targets 경고 0, 실제 HTTPS Gateway/Rust Agent 통합 9 passed (54.92초). 새 검사에는 read-only 정책과 100 MiB 파일의 입출력 SHA-256 일치가 포함된다. Ruff 및 305개 파일 format 검사 PASS.
- Windows [37983540140](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/37983540140)에서 시작 실패를 현재 process의 `sysinfo` user ID 부재로 재현했다. 동일 Windows process handle에서 토큰 SID·생성 시각을 검사하도록 수정했으며 새 native 검증은 대기 중이다. 실제 Windows compile·실행 전 통과 처리하지 않는다.
- 기존 quality [37983540124](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/37983540124)의 Console E2E는 PASS. 전체 Python 회귀는 FAIL이며 기존 browser/plugin/terminal 시험과 링크 검사 실패가 남아 있다. 전체 회귀 완료로 판정하지 않는다.

- Task 4 셸·프로세스 checkpoint: Rust 셸의 안전한 환경/CWD, 64 MiB 출력 제한과 채널별 raw spool, deadline/cancel 후 소유 tree 정리, 프로세스 snapshot cursor·생성 시각/SID·소유자 경계·32개 실행/64개 history·종료/대기를 구현했다. Windows 실행은 native suspended process + restricted handle inheritance + Job Object를 사용한다. 터미널/stream은 아직 구현 중이다.
- 로컬 Rust workspace 42 passed (테스트용 자식 fixture entry 2개 ignored), clippy PASS. 실제 HTTPS Gateway 파일·셸·프로세스 통합 5 passed (32.41초), 한글 CWD/출력과 stale PID 거부·owned tree 종료를 확인했다. Windows native shell/process/파일 확장 검사는 새 CI 대기다.

- Task 4 터미널·stream checkpoint: POSIX PTY 및 Windows ConPTY, 4 MiB raw history·UTF-8 바이트 cursor/eviction, owner·workspace·boot 경계, 입력/resize/keepalive/close, 8개 활성/64개 history, 8시간 TTL을 이식했다. 연결 epoch별 stream은 기존 4개 미확인 프레임 및 256 KiB byte window를 보존하고 ACK chunk boundary·gap·구독 정리·lease를 검사한다.
- 이번 로컬 Rust workspace 45 passed (소유 자식용 fixture entry 3개 ignored), clippy PASS. 실제 HTTPS/WSS Gateway/Rust executable 통합 11 passed (65.65초). 스트림 RED는 기존 미구현 메시지에서 `stream_end`로 재현했고 GREEN에서는 credit 대기·재개, 한글 입력과 idempotent replay·종료를 확인했다.
- Windows [37986271170](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/37986271170)의 native compile/clippy/unit 단계는 PASS이나 background 시작 통합 9개는 pipe EOF 대기 timeout, 독립 Artifact 1개는 PASS다. Rust 1.90 Windows `Command::spawn`의 기본 CreateProcess가 전체 inheritable handle을 넘기는 것을 확인했고, detached Agent를 NUL handle 하나의 명시적 HANDLE_LIST로 실행하도록 수정했다. Native launcher·ConPTY와 통합 결과는 새 CI 대기다. Task 3·4 native GREEN 및 전환 전체 완료는 아직 아니다.

- Windows [37988463095](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/37988463095) 최종 PASS: fmt·clippy·native workspace, Rust Agent/example build 및 실제 HTTPS/WSS Gateway 통합 11개 모두 통과했다. background NUL-only handle 상속, 기존 Python DPAPI 등록·journal 읽기, start/status/동일 PID 재시작/완전 종료, Windows 파일·프로세스 tree·ConPTY·credit 및 100 MiB 입출력을 포함한다. Task 3·4는 이 native 증거로 GREEN 판정했다. 브라우저·Windows desktop·plugin·Tauri·패키징·Python 삭제는 후속 task다.

- Task 5 CDP foundation checkpoint: Rust의 직접 Chromium 실행·소유 Job/프로세스 tree·context/page/frame 격리, loopback discovery와 origin fence, native 입력·스냅샷/관찰값·PNG SHA-256, 시작 중 취소 정리 및 ACK/overflow outbox를 구현했다. 실제 Chromium을 포함한 browser 검사 9개와 workspace clippy PASS. Task 5의 전체 18개 operation·Gateway event 전달·업로드/다운로드·외부 attach는 아직 진행 중이며 기존 Python browser 경로를 제거하지 않았다. Windows CI는 동일 pinned Chromium을 준비하여 native provider 검사를 실행한다.

- Task 5 event checkpoint: 14개 구현된 browser operation을 NativeProviders에 등록하고 BrowserState ACK/replay·overflow refresh 및 수동 navigation/frame/dialog/page event를 Gateway로 전달한다. 실제 HTTPS Gateway의 입력·PNG Artifact(media type/SHA-256)·자동 팝업 종료/감사 개인정보 제거 검사 PASS; Rust Agent 통합 전체 12 passed (75.40초), Rust workspace 55 passed (fixture entry 3 ignored), clippy/새 Python Ruff PASS. HTTP origin route만으로 WebSocket handshake를 막지 못함을 RED로 재현하여 페이지/worker 실행 전 protected constructor guard를 추가했고 해당 network-effect 검사도 PASS다.
- Windows [37991231735](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/37991231735)는 fmt/clippy와 pinned Chromium 준비 PASS, browser 검사 8/9 PASS이나 cross-site iframe 검사는 timeout으로 FAIL이다. 실행 context의 이벤트 도착을 기다리도록 보완하고 cross-site test host를 실제 loopback에 연결되는 localhost로 바꾸었으며 새 native 검증은 대기 중이다. Task 5 전체 완료나 Python 경로 삭제로 판정하지 않는다.

- Task 5 iframe 경합: event metadata 정리 후 로컬 browser 검사가 9/10으로 간헐 실패하여 native GREEN 처리를 보류했다. 이전 parent session의 늦은 frame swap이 새 OOPIF 실행 context를 지우는 경합을 별도 결정적 RED 검사로 재현했다. 교체된 session의 context를 보존하도록 수정했으며 해당 unit 1개·실제 Chromium browser 10개·workspace clippy PASS를 확인했다. Native 재검증 대기이며 업로드/다운로드 Gateway 검사는 미구현 422로 RED다.

- Task 5 files/CDP checkpoint: Cookie·blob 다운로드, GUID 경로 격리·Artifact SHA-256·중복 실행 방지, 검증된 업로드와 browser 수명 동안의 파일 보관을 Rust로 구현했다. 100 MiB 다운로드의 Gateway Artifact 해시와 한도 초과 후 profile 정리도 확인했다. 로컬 Browser 14개, 공용 저장 한도 3개 및 clippy PASS; 기존 Rust Agent 통합 13개 PASS(83.89초), 확장 browser Gateway 3개 PASS(46.88초). Native Windows 재검증 전이다.
- 외부 CDP는 로컬 opt-in(`--enable-cdp`)과 loopback discovery를 요구하며 격리 context 또는 지정된 target에만 연결한다. 외부 process를 종료하지 않고 borrowed 페이지를 보존하는 검사 PASS다. 병렬 검사에서 연결 종료가 한 번 발생했으며 단독 5회 및 후속 전체 Browser 14개는 PASS였다. 다른 context의 target은 종료하지 않고 detach하도록 보완했으며 native 반복 증거는 대기다. `--browser-allow-origin`으로 origin을 제한한다.
- 실패한 page script/native 입력의 실행 상태를 `unknown`으로 기록하고 회수 불명확 상태는 UNKNOWN으로 보존한다. Windows profile 삭제 재시도·종료 직렬화, browser PID 및 자식 보호, retained upload/profile/download 공용 10 GiB admission·monitor를 추가했다. 조합 키·Unicode·function key와 취소 후 key-up도 구현했다. 18개 browser operation이 구현되어 있으나 Task 5 전체 완료 판정은 native 및 잔여 수명주기 검증 이후다.
- Windows [37993028079](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/37993028079)는 iframe 수정 후 native Rust 단계 PASS, Gateway 통합 11/12 PASS였다. 브라우저 종료 정리 한 건은 FAIL이므로 최신 변경의 Windows GREEN을 대신하지 않는다.

- Windows [37996110857](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/37996110857)에서 c84af14 checkpoint 전체 PASS: native Rust 60개(소유 자식 fixture entry 3 ignored), clippy/fmt/build 및 HTTPS/WSS Gateway 14개(158.69초). 이전 브라우저 종료 정리 실패는 이 checkpoint에서 통과했다. 이후 추가한 context proxy·외부 popup·borrowed 새 페이지 ownership/응답 field·idle download 변경은 별도 재검증 대상이다.

- Task 5 수명주기 확장: context별 Rust HTTP/CONNECT proxy로 팝업의 첫 요청에도 origin 검사를 적용하고, 허용한 context의 popup만 추적한다. borrowed 외부 페이지와 새 owned 페이지의 권한을 분리했다. 요청 없는 idle download 취소, 보호된 외부 context/target 정리 기록 및 owned Chromium PID/생성 시각·Windows named Job 기록을 통한 재시작 정리를 구현했다. 살아 있거나 다른 device의 기록은 보존하고 cleanup/health를 실패로 보고한다.
- 이 확장 checkpoint의 로컬 Rust workspace 68 passed (자식 fixture entry 3 ignored), 실제 Chromium browser 20 passed, fmt/clippy 및 Ruff PASS. HTTPS/WSS Rust Agent 통합 15 passed (98.55초). Windows Agent OS 강제 종료 후 Job 자식 종료·profile 복구 검사를 추가했으며 최신 native 재검증은 대기다. Task 5 최종 GREEN과 전체 Rust 전환 완료를 아직 선언하지 않는다.

- Windows [38012930280](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38012930280) 및 [38012989133](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38012989133)는 browser 19/20으로 FAIL, Gateway 단계에 도달하지 않았다. 전체 discovery에 섞인 소유하지 않은 임시 worker를 설정하다 CDP를 닫는 문제를 로컬에서도 재현했다. 모든 attached target에 context/선택한 page subtree 경계를 적용하고, 이미 사라진 target과 종료 중의 event 처리를 분리했다. 외부 CDP 검사 연속 5회와 전체 browser 20개 PASS. 허용한 dedicated worker 실행은 Fetch domain 미지원으로 RED를 재현한 뒤 Network 정책·context proxy·실행 전 socket guard 경로로 GREEN을 확인했다. 최신 Windows 재검증 전이다.
- Task 6 memory RED: Windows [38013452875](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38013452875)에서 native fixture를 대상으로 regions/bytes/보호 PID 검사 2개가 미구현 CAPABILITY_UNAVAILABLE로 실패했다. 이후 Windows x64 읽기 전용 VM 핸들·동일 핸들의 SID/생성 시각/생존 검증, 4 KiB inline·16 MiB Artifact·부분 파일 삭제, 보호된 Agent/browser PID 및 managed owner 경계를 구현했다. 새로운 Gateway byte/SHA-256/재실행/정책 검사는 Windows native 실행 대기다.
- Task 6 foundation: 입력 lease·관찰값·window generation·물리 입력/foreground 중단과 독립 해제 대기 상태, 소유한 key/mouse down만 보관하는 Guardian ledger, bounded pairing·OS peer identity 비교·Python 호환 role/nonce HMAC 검사를 구현했다. 순수 Rust foundation 9개와 clippy PASS. 실제 Windows Named Pipe/Broker/Guardian/UIA/capture/input/service 연결은 아직 구현 전이며 desktop capability를 등록하지 않았다.
- 현재 로컬 workspace 74 passed(자식 fixture 3 ignored)에 추가 pairing 검사 3개 PASS, 실제 Gateway 통합 15 passed·Windows 전용 3 skipped(101.58초), fmt/clippy/Ruff PASS. skipped 검사를 native GREEN으로 계산하지 않는다. 전체 전환 및 Python 삭제는 아직 미완료다.
- Windows [38014198092](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38014198092)의 39cf4c2 checkpoint는 native memory·workspace·fmt/clippy/build PASS, 실제 Gateway 17/18 PASS(206.43초)다. 남은 Gateway 실패는 메모리 검사 내 process.inspect 요청 키 충돌(IDEMPOTENCY_CONFLICT)이다. 이후 peer identity acceptance의 미구현 module 참조로 최신 CI가 컴파일 실패했다.
- 사용자 지시 변경: 테스트 실행·추가를 보류하고 Agent Rust 이식과 실제 Windows 실행 파일 빌드를 우선한다. Native peer identity를 OS token/SID/session/integrity/생성 시각 및 pinned process handle로 구현했다. standalone info/settings/start/status/stop/activity 명령을 추가하고 `rust-agent` 기본 CI를 테스트 없는 Windows production release+Chromium ZIP 빌드로 분리했다. 기존 테스트는 삭제하지 않고 수동 `run_tests=true`에 남겨 뒀으며 일반 quality CI도 이 migration 브랜치에서 보류한다. 새 구현의 acceptance는 미실행이다. [Rust Agent 실행 가이드](../guides/rust-agent-guide.md)에 실행·빌드 절차를 기록한다.
- Windows [38016644333](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38016644333)에서 9c3989e native release Agent와 pinned Chromium ZIP 빌드·artifact 업로드 PASS. 테스트 job은 SKIPPED다. Python/Node 없는 배포 payload와 파일별 SHA-256 manifest를 생성했다. 이후 추가한 Rust desktop 읽기 Broker(GDI PNG/preview, WTS logon/lock·desktop 확인, OS peer-pinned Named Pipe, 보호된 Job과 Artifact spool)는 별도 빌드 대상이며 acceptance는 사용자 요청으로 보류한다. 입력·UIA·Guardian·서비스와 리버싱·Tauri 전환은 아직 미완료다.
