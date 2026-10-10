# RACP 구현 작업 및 검증 현황 (Implementation Status)

> **Document ID**: `DOC-QA-STATUS`\
> **Status**: Active · **Target Version**: v0.1.21\
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
  - [8.1 현재 141 호스트의 올바른 빌드](#desktop-build-correct-20261007)
- [9. 141 호스트→121 대상 시험 준비](#windows-141-121-build)
- [10. v0.1.11 호스트 연결 파일 간편 생성](#host-connection-helper)
- [12. v0.1.13 Windows 계획 실행·초기 등록 화면 허용](#windows-plan-resume-0113)
- [14. 테스트 우선 재개: Windows 회귀와 실제 121 관측](#windows-test-first-20261007)
- [15. 실패 분류와 기존 Codex 리버싱 MCP 원격 인수](#racp-native-re-mcp-20261007)
- [17. v0.1.16 시험 창 x64 메시지 처리 수정과 121 입력 완주](#windows-fixture-input-0116)
- [18. 실제 121 실행 덤프와 Codex WinDbg MCP 분석](#racp-windbg-dump-pass)
- [19. 호스트 자원 기준선 재확인과 Python 전체 회귀](#windows-host-resource-regression)
- [11. 141 관리 Console 접속 복구](#console-access-20261007)
- [13. v0.1.14 Windows Gateway 배포 ZIP](#gateway-build-0114)
- [14. v0.1.15 호스트 PowerShell 진입점 통합](#host-powershell-0115)
- [16. Gateway 웹 관리 서버 계획서 작성](#gateway-web-plan-20261007)
- [21. v0.1.17 Gateway 웹 관리 G00~G12 구현·인수](#gateway-web-management-0117)
- [22. v0.1.18 추가 시험과 실제 제품 수정](#windows-oct08-test-fixes)
- [23. v0.1.18 Gateway G11/G12·Portable 최종 검증](#gateway-management-final-0118)
- [26. v0.1.19 Gateway 웹 관리 최종 구현·인수 판정](#gateway-management-final-0119)
- [27. v0.1.20 Client 세부 권한 구현 진행](#agent-permissions-v020)
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

<a id="desktop-build-correct-20261007"></a>

### 8.1 현재 141 호스트에서 올바른 데스크톱 빌드 재실행 (2026-10-07)

> [!IMPORTANT]
> 앞서 전달했던 `Start-Client.cmd` 기반 Agent ZIP은 이 데스크톱 배포 요구에 맞는 산출물이 아니었다. 새 AGENTS/Desktop Build 스킬에 따라 **NSIS 설치 EXE·단일 포터블 EXE·실행 파일/리소스 ZIP**을 생성했다. 같은 배치의 정정·재시도이므로 v0.1.10을 유지했다. §8 위쪽의 기존 검사·해시는 다른 환경에서 GitHub에 기록한 과거 제작 증거이며, 현재 다운로드 파일의 해시는 아래 표와 manifest를 사용한다.

호스트는 `DESKTOP-06NU139` (192.168.29.141), Windows 11 x64다. 실행 환경은 `.tools/desktop-build-venv`이며, checksum 검증한 Node 22.23.0/Corepack pnpm 11.19.0, Python 3.12.11과 frozen lock을 사용했다. [실행 문맥](../../dist/acceptance/RUN-20261007-desktop-correct/build-context.json)과 [source/lock hash](../../dist/acceptance/RUN-20261007-desktop-correct/source-manifest.json)를 보관했다.

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.tools/desktop-build-venv'
uv run --frozen python scripts/build_client.py --platform win --arch x64 `
    --node .tools/node-v22.23.0-win-x64/node.exe `
    --browser-cache "$env:LOCALAPPDATA/ms-playwright"
```

| 이번 실행 검사 | 결과 / 증거 |
| :--- | :--- |
| 필수 Ruff + 빌드 Python 스크립트 lint | PASS; [lint 로그](../../dist/acceptance/RUN-20261007-desktop-correct/lint.log) |
| Mypy | 135 source 파일 PASS; [타입 로그](../../dist/acceptance/RUN-20261007-desktop-correct/mypy.log) |
| Python 버전·Desktop Control | **16 passed**, failure/error/skip 0; [JUnit](../../dist/acceptance/RUN-20261007-desktop-correct/version-desktop.xml) |
| TypeScript/Vite frontend | PASS; [통합 빌드 로그](../../dist/acceptance/RUN-20261007-desktop-correct/build.log) |
| Client Node | **11 passed**, failure 0; manifest의 버전/platform/arch/lock/변조·경로 경계 검사 포함 |
| 스테이징 Agent | `native Agent imports OK`, 실제 내장 runtime에서 `racp-agent 0.1.10` 확인 |
| HTTPS/WSS Client E2E | PASS; [격리 E2E 로그](../../dist/acceptance/RUN-20261007-desktop-correct/client-e2e.log): 연결 파일·token/CA 오류·설정 보존·재연결·설정 편집 경계·desktop opt-in·트레이·Job·완전 종료 |
| 압축 해제형 EXE smoke | PASS: 새 user-data-dir를 확인한 후 내장 Agent/연결 파일 preview/비밀 비출력/CA 오류/완전 종료 |
| 단일 포터블 EXE smoke | PASS: NSIS 추출·loopback Chromium readiness·격리 profile·v0.1.10·native Agent bridge·완전 종료 |
| ZIP/manifest | 전체 **4,861개 파일 CRC**, Agent **4,788개 SHA-256** PASS; [ZIP 검증](../../dist/acceptance/RUN-20261007-desktop-correct/zip-integrity.json) |
| 최종 산출물 | EXE 두 파일의 PE 표식, 세 파일의 크기/SHA-256, source/lock 일치 PASS; [최종 검증](../../dist/acceptance/RUN-20261007-desktop-correct/verification.json) |
| macOS/Linux | 정의 유지·빌드 DEFERRED; native runner/컨테이너 실행 없음 |

첫 실행은 Node 의존성 다운로드 이후 중단되어 [초기 로그](../../dist/acceptance/RUN-20261007-desktop-correct/build-attempt-1-interrupted.log)를 보존했고, staging/output 경로가 아직 생성되지 않은 상태에서 같은 버전으로 통합 진입점을 재실행했다. 기본 pytest 임시 루트의 WinError 5는 [초기 XML](../../dist/acceptance/RUN-20261007-desktop-correct/version-desktop-attempt-1.xml)에 보관했다. 사용자 임시 폴더 ACL을 수정하지 않고 저장소 내 새 전용 basetemp로 검사해 16 passed를 확인했다. JUnit 호스트 metadata 수집 중 Windows WMI 진단 `0x8007000e`가 로그에 출력되었으나 최종 프로세스 exit 0, XML failure/error 0이었다.

최종 현황 문서의 메타데이터·상대 파일/anchor 링크 49개를 검사해 오류 0건이었고 `git diff --check`도 통과했다.

산출물 경로는 **`dist/client-desktop/0.1.10/win-x64/`**이며, [build-manifest.json](../../dist/client-desktop/0.1.10/win-x64/build-manifest.json)과 [SHA256SUMS.txt](../../dist/client-desktop/0.1.10/win-x64/SHA256SUMS.txt)에 현재 파일을 기록했다.

| 산출물 | 크기 (bytes) | SHA-256 |
| :--- | ---: | :--- |
| [RACP-Client-0.1.10-win-x64-setup.exe](../../dist/client-desktop/0.1.10/win-x64/RACP-Client-0.1.10-win-x64-setup.exe) | 396322765 | `2c8a788ff55745b5a7bb1a3b6a3b30f0e371a6b2984649b4b49876f2d759f0f5` |
| [RACP-Client-0.1.10-win-x64-portable.exe](../../dist/client-desktop/0.1.10/win-x64/RACP-Client-0.1.10-win-x64-portable.exe) | 396110081 | `a5cbad23f456b325d2f04bb05b79ca63cb34a5f6df62f9a25fc0621748c0e654` |
| [RACP-Client-0.1.10-win-x64.zip](../../dist/client-desktop/0.1.10/win-x64/RACP-Client-0.1.10-win-x64.zip) | 543272105 | `31df2b078bff9cc11a0d5f03a23f20dc13046ee75810c1191e45b89f17f0049a` |

Electron 44.5.1, CPython 3.12.11, 현재 workspace wheel과 고정 Playwright Chromium을 포함한다. `before-pack.cjs`의 OS/arch/version/lock/file hash 검증을 우회하지 않았으며 `--publish never`를 적용했다. Gateway 설정·credential·등록 token·서버 private key는 넣지 않았다. 격리 시험 화면의 v0.1.10 표시와 연결 폼도 [screenshot](../../dist/acceptance/RUN-20261007-desktop-correct/packaged-connection-setup.png)으로 확인했다.

> [!NOTE]
> unsigned 개발 빌드다. clean PC 설치·upgrade/uninstall, 서명, 실제 Codex→121 원격 입력 인수는 NOT_RUN이며 이번 로컬 E2E/패키징 PASS로 승격하지 않는다. 121에서는 설치 EXE 또는 포터블 EXE를 실행하거나 ZIP을 풀어 `RACP Client.exe`를 사용한다. Gateway의 새 `.racp` 연결 파일로 등록하고 화면 제어 허용을 선택한 뒤 Agent를 시작한다.

---

<a id="windows-141-121-build"></a>

## 9. 141 호스트→121 대상 시험 준비

> [!IMPORTANT]
> 2026-10-07 사용자 정정에 따라 **호스트 A는 192.168.29.141**, **원격 대상 B는 192.168.29.121**이다. §7의 과거 140→141 결과는 보존하며 새 대상의 PASS로 이관하지 않는다. 설치형·포터블 빌드 정의와 산출물은 GitHub 기준인 §8을 따른다. 이 절은 로컬의 시험 준비 기록만 보존한다.

준비 실행 ID는 `RUN-20261007-141-121`이다. 당시 141은 `DESKTOP-06NU139`, Windows 11 build 26100, Python 3.12.11이었으며 검사는 기존 `.venv`와 분리한 `.venv-acceptance`에서 수행했다. 당시 source/lock hash, dirty 상태와 환경은 실행 manifest 경로 `dist/acceptance/RUN-20261007-141-121/manifest.json`에 보관한다. GitHub 변경 적용 후 후보 source hash와 실제 서비스 상태는 시험 시작 시 다시 확인한다.

### 9.1 작업 상태와 연결 선행 조건

| 작업 | 준비 당시 판정 | 다음 조건 |
|---|---|---|
| W01 후보·호스트 기준선 | 부분 완료: 141 IP/OS, 121 ping 응답 1 ms, 새 Gateway TLS 인증 조회 HTTP 200 확인 | B Device/boot/epoch/workspace/Client·Agent 버전은 새 등록 후 확인 |
| W02 독립 GUI fixture | 구현 및 종료 검사 완료; 입력 관측은 미검증 | [전용 native script](../../scripts/desktop_acceptance_fixture.py)의 PID/생성 시각/session/HWND를 실제 MCP 관측과 대조 |
| W03 실제 Codex→121 입력 | BLOCKED_ENV: 준비 당시 이 대화에 RACP MCP 도구가 노출되지 않음 | 121 Client·Agent 등록, OAuth MCP 연결과 새 identity/session 관측 후 재개 |
| W05/W06 연결·메모리 회귀 | 로컬 fixture 경로 PASS | 실제 121 인수 결과와 구분 |
| W07/W09/W10/W11 | NOT_RUN | 실제 장애·복원·부하·8시간 soak·최종 인수 실행 필요 |

141의 `.racp/two-pc-141-121`에 새 전용 CA/Gateway 인증서와 DPAPI 보호 owner 상태를 준비했다. 당시 Gateway 주소는 `https://192.168.29.141:8765`이고 등록 Device는 **0개**였다. 기존 credential/Device ID를 복사하거나 재사용하지 않았다. 랩 CA로 TLS를 검증했고 OS 신뢰 저장소 등록은 하지 않았다. OAuth MCP 구성·Codex 연결은 미완료였으며 방화벽 규칙은 추가하지 않았다. 현재 listener·등록 상태와 121→141 연결은 시험 재개 시 확인한다. 필요하면 [2-PC 가이드](../guides/two-pc-lab-guide.md)의 121 하나로 제한한 규칙을 사용한다.

### 9.2 보존한 시험 증거와 재개 절차

- 준비 당시 대상 회귀는 **48 passed / 1 skipped / failure·error 0**이었다. JUnit 경로 `dist/acceptance/RUN-20261007-141-121/automation/regression.xml`은 version, desktop guard/UIA/control/capture, native memory, HTTPS/WSS 및 loopback MCP 범위다. 현재 병합 상태의 새 검사 결과로 간주하지 않는다. 원본 manifest·JUnit·시험 matrix 파일은 현재 작업 폴더에서 찾을 수 없어 위 기록을 다시 검증하지 못했다.
- native 입력 시험은 Windows가 자체 subprocess 창의 foreground 활성화를 거부하여 **SKIP**했다. 실제 mouse/key/scroll/drag/UIA counter 성공으로 집계하지 않는다. fixture의 timeout과 정상 종료 검사는 준비 당시 통과했다.
- 상세 시험 54개, display/session 조합 8개와 묶음 시나리오 3개는 시험 matrix 경로 `dist/acceptance/RUN-20261007-141-121/test-matrix.json`에 `NOT_RUN` 또는 `BLOCKED_ENV`로 기록했다. 로컬 회귀로 실제 121 시험 상태를 바꾸지 않았다.
- 시험 창은 121의 허용 폴더에 별도로 복사한다. 결과 경로를 새로 만들고 PID/생성 시각/session/HWND를 대조한 뒤 lease를 얻어 해당 창에만 입력한다. 사용·결과 판정은 [2-PC 가이드](../guides/two-pc-lab-guide.md#31-독립-결과를-기록하는-화면-입력-fixture)를 따른다.
- 121에서 사용할 Client는 §8의 설치형·포터블 후보를 따른다. 새 일회용 token으로 등록하고, Windows 화면 제어 설정을 허용한 뒤 Agent를 시작한다. 별도의 로컬 Agent ZIP 빌드 변경은 이번 GitHub 우선 적용에서 유지하지 않았다.


### 9.3 GitHub 우선 동기화 검증

2026-10-07 GitHub `a8506d0`를 fast-forward 적용했다. 로컬의 141→121 가이드·작업 계획·독립 GUI fixture와 테스트·검사 venv 제외 규칙만 보존하고, 별도 Agent ZIP 빌드/실행 스크립트 수정은 GitHub 내용으로 되돌렸다. GitHub의 §8 빌드 정의와 기록은 유지했다. 동기화는 기존 v0.1.10 작업의 반영이며 새 제품 빌드나 버전 증가는 수행하지 않았다.

검증은 `.venv-acceptance`의 Python으로 수행했다. 필수 Ruff lint와 fixture 두 파일 format 검사 PASS, Mypy 135 source 파일 PASS, 버전 테스트 및 fixture timeout·결과 보존·기존 결과 덮어쓰기 거절 검사 **7 passed**, `scripts/version.py show`는 **0.1.10**이다. 변경 문서 3개의 상대 파일/anchor 링크 75개와 메타데이터 검사 오류 0건, `git diff --check` PASS, 로컬 HEAD와 origin/master 일치 및 보존 대상 외 변경 없음도 확인했다. 실제 Codex→121 입력과 전체 인수 시험은 이번 동기화에서 실행하지 않았다.

덮어쓰기 전 추적·미추적 변경은 Git stash `86c6e4d02070d5e5af717a235d920538d0854f8b` (`backup-before-github-priority-141-121-20261007`)에 보관했다. 현재 수정은 아직 커밋·push하지 않았다.


---

<a id="host-connection-helper"></a>

## 10. v0.1.11 호스트 연결 파일 간편 생성

2026-10-07 사용자 요청에 따라 저장소 루트에 `Create-Connection-File.cmd`(현재 [PowerShell 진입점](../../scripts/host/Create-Connection-File.ps1)으로 대체)를 추가했다. 호스트에서 더블클릭하면 기존 Python 환경과 단일 Gateway owner/CA 설정을 자동으로 찾아 TLS·인증·readiness를 확인하고, 공개 CA가 포함된 `.racp` 하나를 `connection-files/`에 생성한다. 탐색기에서 생성 파일을 선택하며 성공 시 명령 창은 자동으로 닫는다. 실패 시 안전한 원인/조치 안내를 남긴다.

[생성 helper](../../scripts/create_connection_file.py)는 매 실행마다 날짜·고유 번호로 새 파일을 생성한다. 기존 파일과 owner credential을 덮어쓰지 않고, 토큰을 콘솔·별도 txt에 복제하지 않는다. 출력 폴더는 `.gitignore`에 추가했다. 기존 [발급 script](../../scripts/issue_desktop_lab_ticket.py)의 인증 발급 함수를 공유하고, Gateway가 연결 파일에 CA를 넣지 않는 경우 호스트 공개 CA를 검증해 포함한다. 설정이 여러 개면 임의의 Gateway에 발급하지 않고 중단한다. [2-PC 가이드의 생성 절차](../guides/two-pc-lab-guide.md#21-호스트에서-연결-파일-생성)를 갱신했다.

새 변경 배치에서 버전 도구로 **0.1.10→0.1.11**을 한 번 증가하고 별도 `.tools/desktop-build-venv`에서 `uv sync --all-packages`를 실행했다. `uv.lock`은 workspace 버전 9개만 변경되었으며 외부 dependency는 변경하지 않았다. §8.1의 이미 생성된 v0.1.10 Client 파일은 이전 빌드의 산출물이다. 이번 변경은 호스트 발급 script와 version synchronization이며 데스크톱 패키지를 다시 빌드한 결과가 아니다.

| 검사 | 결과 / 증거 |
| :--- | :--- |
| 필수 Ruff + 발급 helper/tests | PASS; [lint](../../dist/acceptance/RUN-20261007-connection-helper/lint.log) |
| Mypy | 135 source 파일 PASS; [타입 검사](../../dist/acceptance/RUN-20261007-connection-helper/mypy.log) |
| 버전·새 발급 검사 | **9 passed**, failure/error/skip 0; [JUnit](../../dist/acceptance/RUN-20261007-connection-helper/tests.xml) |
| 실제 TLS 일회용 등록 | 자체 Gateway에서 문서 생성·공개 CA 포함·등록 성공·token 재사용 401·기존 파일 보존 PASS |
| 안전한 실패 | Gateway 접속 실패 시 비밀 출력·빈 결과 파일 없음, 모호한 host 설정 선택 거부 PASS |
| 실제 141 CMD 실행 | exit 0, 다른 시작 폴더에서도 성공, `.racp` 한 파일 생성 및 token 비출력; [실행 기록](../../dist/acceptance/RUN-20261007-connection-helper/live-generation.json) |

실제 141 Gateway `https://192.168.29.141:8765`에서 2026-10-07 15:59:03 KST에 연결 파일을 발급했고 만료 시각은 **16:09:03 KST**였다. 원문 token은 보고서에 보관하지 않는다. 파일은 현재 사용자용 `connection-files/`에 있고 한 번 등록되거나 10분이 지나면 재발급해야 한다. 이 실증은 연결 파일 생성이며 121 등록이나 원격 입력 성공을 뜻하지 않는다.

문서 검증: 변경 guide/status 2개의 메타데이터·상대 파일/anchor 링크 76개 오류 0건, `git diff --check` PASS.


---

<a id="console-access-20261007"></a>

## 11. 141 관리 Console 접속 복구

2026-10-07 관리 Console URL이 404인 원인은 `apps/console/dist`가 없고, 실행 중인 Gateway에 정적 화면이 mount되지 않은 상태였다. Gateway readiness와 등록 PC 한 대의 ONLINE은 정상이며 웹 화면 배포가 빠져 있었다. pinned Node 22.23.0으로 `scripts/console_build.py`를 실행해 Prettier/TypeScript/OpenAPI client drift/Vite 생산 빌드를 통과시켰다. 최초 검사에서 Windows checkout의 16개 파일 formatting 오류를 확인하고 같은 formatter로 정리한 후 재검했다. [빌드 로그](../../dist/acceptance/RUN-20261007-console-access/build.log)를 보관했다.

재시작 전에 실행 대기열이 비었고 모든 terminal 기록이 CLOSED임을 확인했다. 처음에는 CLOSED 기록을 활성 handle로 잘못 분류해 중단했으며, 상태를 확인해 실제 활성 작업이 없음을 확인한 뒤 진행했다. PID/생성 시각/명령행을 검증한 이 작업 소유 Gateway만 종료했다. SQLite backup을 보관하고 기존 실행 인자·data-dir·TLS/CA/owner credential을 유지해 재시작했다. [복구 기록](../../dist/acceptance/RUN-20261007-console-access/gateway-after.json)의 페이지/readyz HTTP 200, 동일 Device `dev_b12478e45ab646e5838c31218ab85fb1` ONLINE, credential/인증서 원본 hash 일치를 확인했다. 현재 URL은 **https://192.168.29.141:8765/console/** 이다.

`Create-Console-Login.cmd`(현재 [PowerShell 진입점](../../scripts/host/Create-Console-Login.ps1)으로 대체)와 [로그인 코드 helper](../../scripts/create_console_login.py)를 추가했다. 호스트 owner로 5분짜리 일회용 setup secret을 발급하고, 현재 사용자만 접근하는 DACL을 적용한 로컬 txt에 보관한다. 명령행·로그에 token이나 owner secret을 출력하지 않는다. 웹 화면이 준비되지 않으면 secret을 발급하지 않는다. 이용 절차는 [2-PC 가이드](../guides/two-pc-lab-guide.md#22-관리-console-접속)에 기록했다.

| 검사 | 결과 / 증거 |
| :--- | :--- |
| Console formatter/type/generated client/build | PASS; [빌드](../../dist/acceptance/RUN-20261007-console-access/build.log) |
| 필수 Ruff + 새 helper/tests | PASS; [lint](../../dist/acceptance/RUN-20261007-console-access/lint.log) |
| Mypy | 135 source 파일 PASS; [타입](../../dist/acceptance/RUN-20261007-console-access/mypy.log) |
| 버전·로그인 helper | **8 passed**, failure/error/skip 0; [JUnit](../../dist/acceptance/RUN-20261007-console-access/python-tests.xml) |
| 실제 HTTPS Console API | 페이지 200·assets 2개 200·로그인 200·cookie session 200·장비 조회 200·logout 200·setup secret 재사용 401; [검증](../../dist/acceptance/RUN-20261007-console-access/login-api-verification.json) |
| owner 보호·파일 ACL | helper txt에 owner secret 없음, Windows DACL의 ACE 1개(현재 사용자), 미배포 페이지에서 발급 차단 PASS |
| 내장 브라우저 자동 확인 | BLOCKED_HOST: URL 보안 정책으로 해당 탭의 자동 관측이 거부됨. 다른 브라우저/CDP/자동화로 우회하지 않았고 사용자 브라우저의 실제 표시·로그인은 미검증 |

이번 복구 배치에서 PATCH 0.1.11→0.1.12를 증가했다. 병행된 다른 작업의 변경으로 현재 SSOT/workspace는 **0.1.13**이며 그 버전을 되돌리지 않았다. 기존 remote Client·권한/UI 관련 병행 변경은 보존했고, 새 버전의 데스크톱 EXE를 빌드한 것으로 보고하지 않는다. TLS 검증과 owner 인증·승인 정책은 유지했고 browser-generated security warning을 우회하지 않았다. 현재 보고는 서버·API 접속 복구이며 실제 Codex MCP OAuth 연동 완료를 의미하지 않는다.

추가 확인: public CA thumbprint `00E90D44C74B4EB3CD4EF708D7ADDF10698EA346`는 Windows CurrentUser/LocalMachine Root 둘 다 미등록이었다. public DER `RACP-Host-CA.crt`만 기존 랩 폴더에 export했고 private key를 포함하거나 신뢰 저장소를 자동 변경하지 않았다. 문서 2개 메타데이터·상대 링크 89개 오류 0건을 확인했다.

---

<a id="windows-plan-resume-0113"></a>

## 12. v0.1.13 Windows 계획 실행·초기 등록 화면 허용

2026-10-07 [Windows 계획](../spec/windows-engineering-plan.md)의 W01/W02/W04/W05를 재개했다. 실행 ID는 `RUN-20261007-170434-windows-plan`이다. [source·환경 manifest](../../dist/acceptance/RUN-20261007-170434-windows-plan/manifest.json), [경로별 시험 matrix](../../dist/acceptance/RUN-20261007-170434-windows-plan/test-matrix.json)를 보관한다. 병행 Console 변경의 v0.1.12를 보존한 뒤 이번 수정 배치에서 버전 도구로 **0.1.12→0.1.13**을 한 번 증가하고 별도 `.tools/desktop-build-venv`에서 lock/workspace를 동기화했다. live Gateway 환경은 동기화하거나 재시작하지 않았다.

### 12.1 초기 등록과 설정 편집의 화면 제어 선택 통일

사용자가 초기 등록 화면에 화면 제어 체크박스가 없음을 확인했다. Client의 연결 파일 등록·직접 입력 등록과 기존 설정 편집 화면에 동일한 `DesktopPermission` 컴포넌트를 사용한다. 기본값은 false이고 등록 중에는 비활성화한다. Electron IPC→strict Python 등록 모델→`connect.enroll/prepare`→보호된 Agent 설정까지 `desktop_enabled`를 전달한다. 선택값은 재실행에도 유지되며 다음 Agent 시작부터 적용된다. 기존 요청에서 필드가 빠지면 false를 유지하고 실행 profile/승인 정책을 변경하지 않는다.

[초기 등록 화면](../../dist/acceptance/RUN-20261007-170434-windows-plan/client-initial-registration.png)에서 문구·체크박스·기본값을 확인했다. 연결 파일/수동 등록 각각의 true/false 저장·비밀 비노출·재실행 정보 확인을 검사에 포함했다. Hello의 `agent_version="0.1.0"` 고정값도 SSOT `VERSION` 참조로 수정했고 실제 fixture Agent가 Gateway에 현재 버전을 보고하는 회귀 검사를 추가했다.

| 검사 | 실제 결과 / 증거 |
| :--- | :--- |
| 필수 Ruff + 변경 Python 검사 | PASS; [lint](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/lint.log) |
| Ruff format | 초기 AGENTS의 Python 예제 빈 줄 차이 보정 후 302 files PASS; [최종 format](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/format-final.log) |
| Mypy | 135 source files PASS; [타입](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/mypy.log) |
| 버전·등록·연결 파일·인증/실행 회귀 | **34 passed**, failure/error/skip 0; [JUnit](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/targeted.xml) |
| 자체 GUI timeout·이전 결과 덮어쓰기 거부 | **1 passed**; [JUnit](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/fixture-timeout.xml) |
| Client TypeScript/Vite·Node | 빌드 PASS·Node **11 passed**; [빌드 로그](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/build.log) |
| 전체 Python suite | BLOCKED_ENV: Windows `platform._wmi_query` native 예외 `0x8007000e` 후 5분 이상 로그 진행이 없어 해당 run의 pytest 프로세스만 종료. PASS로 집계하지 않음; [로그](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/full.log), [종료 기록](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/full-interruption.json) |

GUI E2E의 첫 hidden 시도는 연결 파일 재선택 후 버튼 대기에서 timeout했다. visible 재시도에서는 등록값 true 저장을 확인했으나 설정 편집의 로딩 화면을 기존 5초 assertion으로 판정해 실패했다. [실패 화면](../../dist/acceptance/RUN-20261007-170434-windows-plan/client-e2e-failure-1791361322165.png)을 보존하고 checkbox의 실제 로딩을 기다리도록 시험을 수정했다. 격리 프로필의 visible 실행을 선택 가능하게 하고 실패 screenshot을 수집한다. 총 fixture 실행 예산은 600초로 조정했으며 제품 IPC의 45초 제한과 기능 합격 기준은 유지했다. 최종 GUI 및 패키지 검증 결과는 아래 후속 기록을 따른다.

### 12.2 실제 141→121 owner API 증거와 남은 인수

Gateway TLS/owner readiness HTTP 200과 Device `dev_b12478e45ab646e5838c31218ab85fb1` ONLINE을 확인했다. 사용자가 화면 제어를 허용하고 재시작한 뒤 boot `boot_ca6bab53eab544a7ba21beec7e57c7bd`·epoch 2·session 1의 desktop Broker가 enabled/healthy로 보고됐다. hostname은 `VM-WIN10-64-VPN`, Windows 10 build 19045, workspace는 `C:/Users/GHOSTSHELL/Documents`, profile은 standard다. [preflight](../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/preflight.json)를 보관한다.

기존 Hello는 0.1.0으로 보고했지만 원격 내장 Python에서 SSOT와 설치 metadata를 독립 확인한 실제 Agent 버전은 **0.1.10**이다. Python executable과 ASAR hash도 [설치 후보 증거](../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/installed-candidate.json)에 기록했다. 아래 원격 시험은 v0.1.10 대상의 owner API 경로이며 v0.1.13 또는 actual Codex 인수로 확대하지 않는다.

| 작업 | 실제 결과 / 증거 |
| :--- | :--- |
| W05 일부: 파일·실행·자료 회수·Job·ConPTY | 한글 write/read/copy·SHA-256·인증 Artifact 회수, 같은 key counter=1, 장기 Job CANCELLED/cleanup complete·PID 부재, ConPTY marker·close PASS; [core](../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/core.json) |
| W05 시험 폴더 정리 | own 폴더 revision 확인 후 삭제, terminal handle CLOSED; [before](../../dist/acceptance/RUN-20261007-170434-windows-plan/resources-before.json), [after](../../dist/acceptance/RUN-20261007-170434-windows-plan/resources-after.json) |
| W02 실제 B의 독립 GUI fixture | own script/process 생성 후 즉시 읽기가 startup과 경합해 PATH_NOT_FOUND. console signal 정리는 native GUI에서 명시적으로 거부됐다. 15초 자체 timeout으로 정상 종료 후 결과 JSON·PID/생성 시각·CLOSED handle·창 부재·폴더 삭제 확인; [복구/정리](../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/gui-first-attempt.json). 살아 있는 창 inspect 및 input은 미검증 |
| W03 실제 Codex→121 입력 | BLOCKED_ENV: 이 대화에 RACP MCP 도구 없음, Gateway는 owner_bearer이고 OAuth/loopback MCP 미준비. mouse/key/scroll/drag/UIA 입력 요청을 보내지 않음 |
| W07–W11 최종 인수 | 새 후보 B 설치, actual Codex workflow, 장애·display/session·clean 설치/rollback/restore·부하·8시간 soak가 남음 |

> [!IMPORTANT]
> 실제 시험 54개·표시/세션 조합 8개·E2E 3개의 matrix는 actual Codex 경로 기준으로 BLOCKED_ENV/NOT_RUN을 유지했다. 구현·부분 owner API 증거만으로 Windows 핵심 인수 또는 배포 검증 완료를 선언하지 않는다. macOS/Linux 빌드는 실행하지 않았다.

---

<a id="gateway-build-0114"></a>

## 13. v0.1.14 Windows Gateway 배포 ZIP

2026-10-07 사용자 요청으로 Windows x64용 자체 Gateway 런타임 배포를 추가했다. [빌드 스크립트](../../scripts/build_gateway.py)는 pinned CPython 3.12.11, frozen Gateway dependency 40개와 workspace wheel 6개, production Console을 포함한다. Electron Client와 Chromium을 별도 서버 ZIP에 넣지 않는다. version tool로 PATCH **0.1.13→0.1.14**를 이 배치에서 한 번 증가하고 별도 build venv를 sync했다. 기존 Client/Agent 및 병행 변경과 과거 배포본은 보존했다. MCP serverInfo의 version도 SSOT를 참조하도록 수정했다.

[운영 가이드](../guides/gateway-deployment-guide.md)의 `Start-Gateway.cmd`, `Status-Gateway.cmd`, `Create-Connection-File.cmd`, `Create-Console-Login.cmd`를 배포본에 넣었다. 첫 시작은 사용할 로컬 IPv4를 선택하고 TLS 인증서·DB·DPAPI owner credential을 현재 사용자 전용 상태 폴더에 생성한다. 재시작은 identity를 보존하며, 설정 덮어쓰기와 점유 포트 시작을 거절한다. Console/PC 등록용 단기 코드를 MCP OAuth credential로 취급하지 않는다. Windows Service/부팅 자동 시작, 신뢰 저장소/방화벽 자동 변경과 IdP 설치는 제공하지 않는다.

| 항목 | 결과 / 증거 |
| :--- | :--- |
| Gateway ZIP | [RACP-Gateway-0.1.14-win-x64.zip](../../dist/gateway/0.1.14/win-x64/RACP-Gateway-0.1.14-win-x64.zip), **34,303,542 bytes**, SHA-256 `502416c46f1e3d74e1809db1ca773500527ae6273f5099b0819297cf6769ca44`; [manifest](../../dist/gateway/0.1.14/win-x64/build-manifest.json) |
| Console 생산 빌드·native import | PASS; version bump가 바꾼 package.json formatting을 보정한 후 같은 버전으로 재시도. 최초 실패에서 배포 output은 생성되지 않았다; [빌드](../../dist/acceptance/RUN-20261007-gateway-build/build.log) |
| Ruff·Mypy | PASS; Mypy 135 source files; [lint](../../dist/acceptance/RUN-20261007-gateway-build/lint-final.log), [type](../../dist/acceptance/RUN-20261007-gateway-build/mypy-final.log) |
| 버전·host identity/ACL·점유 포트·TLS·OAuth listener·발급 회귀 | **30 passed**, failure/error/skip 0; [JUnit](../../dist/acceptance/RUN-20261007-gateway-build/tests-final.xml). 초기 ACL 검사에서 Windows가 동일 사용자 ACE를 적용/상속 두 항목으로 나누는 것을 확인해 모든 ACE의 실제 SID를 검사하도록 수정했다 |
| 새 폴더에 ZIP 해제 후 CMD 실행 | PASS; 공백 포함 경로, 자체 Python 실행, TLS readiness, Console/assets, 연결 파일·PC 1회 등록·재사용 401, cookie 로그인·logout·코드 재사용 401, 상태 CMD 확인; [smoke](../../dist/acceptance/RUN-20261007-gateway-build/smoke.log) |
| 무결성·배포 경계 | ZIP CRC·manifest **4,324 files** SHA-256 PASS. ZIP에 `.racp`/owner.bin/server.key 없음. smoke에서 만든 사용자 상태는 acceptance 폴더에만 있고 배포 ZIP에는 포함되지 않음 |
| MCP | 격리 loopback owner-auth MCP에서 serverInfo 0.1.14·**86 tools**·device_list 확인. TLS OAuth listener 회귀도 PASS. 외부 Codex OAuth 로그인/실제 Agent 실행은 이번 배포 smoke에서 NOT_RUN |
| 기존 141 호스트 | 기존 8765 서버를 교체/재시작하지 않음. protected owner/CA를 사용한 doctor 조회 healthy, 동일 Device ONLINE, MCP owner_bearer; [상태](../../dist/acceptance/RUN-20261007-gateway-build/live-host-status.json) |

> [!IMPORTANT]
> unsigned 개발 배포본이며 별도 clean PC 설치·운영 인증서 갱신·Windows Service·실제 Codex OAuth 연동을 완료했다고 선언하지 않는다. 현재 141 서버의 외부 `/mcp`는 OAuth 공급자 설정이 추가로 필요하다. 기본 운영 순서는 Gateway 시작 → OAuth/Codex MCP 연결 및 Client 등록(상호 독립) → 대상 Agent ONLINE/권한 확인 → 작업이다. macOS/Linux 빌드와 container는 실행하지 않았다.

최종 재확인: 버전 테스트 6개 재실행 PASS, 배포 ZIP size/SHA-256/CRC 및 credential 미포함 확인, smoke 종료 후 해당 추출 runtime 프로세스 0개, 변경 문서 3개의 상대 파일 링크 118개 오류 0건. [최종 검증](../../dist/acceptance/RUN-20261007-gateway-build/final-verification.json)에 기록했다.

---

<a id="host-powershell-0115"></a>

## 14. v0.1.15 호스트 PowerShell 진입점 통합

2026-10-07 사용자가 승인한 구성으로 Gateway 운영 진입점을 PowerShell로 통합했다. 저장소 루트의 두 CMD를 제거하고 [scripts/host](../../scripts/host/)에 `Start-Gateway.ps1`, `Status-Gateway.ps1`, `Create-Connection-File.ps1`, `Create-Console-Login.ps1`과 공통 `Invoke-RacpHost.ps1`을 추가했다. Gateway 빌드가 같은 원본을 배포 폴더 최상위에 복사한다. 서버·발급 기능은 기존 Python helper를 호출하며, 배열로 native 인자를 전달하고 Python 종료 코드를 그대로 반환한다. 저장소에서는 기존 단일 랩 탐색을 유지하며 배포본에서는 별도 `.racp/host`를 사용한다. [운영 가이드](../guides/gateway-deployment-guide.md)와 [2-PC 가이드](../guides/two-pc-lab-guide.md)를 새 경로·명령으로 수정했다.

version tool로 PATCH **0.1.14→0.1.15**를 한 번 증가하고 별도 build venv를 sync했다. 과거 v0.1.14 CMD 배포 ZIP과 다른 Client/Agent 작업은 보존했다. 이 배치는 Gateway ZIP만 새로 빌드했으며 v0.1.15 Client 설치 EXE를 빌드했다는 의미는 아니다.

| 검사 | 결과 / 증거 |
| :--- | :--- |
| TDD 초기 실패 | PowerShell 원본이 없는 상태에서 새 검사 3개가 의도한 누락 사유로 실패; [RED](../../dist/acceptance/RUN-20261007-host-powershell/red.log) |
| PowerShell 5.1·인자·종료 코드 및 관련 회귀 | **17 passed**, failure/error/skip 0; source/package 공백·한글 경로와 `&`가 포함된 이름, NoOpen, native 종료 코드 7 전달 확인; [JUnit](../../dist/acceptance/RUN-20261007-host-powershell/tests-final.xml) |
| 실행 환경 원인 확인 | PS5 기본 Restricted가 스크립트 실행을 막는 것을 확인. 검증 프로세스에만 명시적 RemoteSigned를 사용하고 PC/조직 영구 정책을 변경하지 않았다. 최초 source native 시작의 20초 timeout은 같은 인자로 정상 결과·exit 7을 독립 확인한 후 60초 검증 예산으로 재실행했다 |
| Ruff·Mypy | PASS; Mypy 135 source files; [lint](../../dist/acceptance/RUN-20261007-host-powershell/lint-final.log), [type](../../dist/acceptance/RUN-20261007-host-powershell/mypy.log) |
| 생산 Console·native Gateway·ZIP 빌드 | PASS; [build](../../dist/acceptance/RUN-20261007-host-powershell/build.log) |
| 배포 ZIP | [RACP-Gateway-0.1.15-win-x64.zip](../../dist/gateway/0.1.15/win-x64/RACP-Gateway-0.1.15-win-x64.zip), **34,305,342 bytes**, SHA-256 `1a695c4598125282e1700ce725b891a3c9d4d25d6ef6ea09f4cf3a9678a43d12`; [manifest](../../dist/gateway/0.1.15/win-x64/build-manifest.json) |
| 새 ZIP PowerShell smoke | PASS; 공백 경로에서 PS5 시작·TLS readiness·Console/assets·PC 1회 등록과 token 재사용 401·로그인/logout와 코드 재사용 401·상태 조회; [smoke](../../dist/acceptance/RUN-20261007-host-powershell/smoke.log) |
| 무결성·MCP | ZIP CRC·manifest **4,325 files** hash·CMD 미포함 확인. loopback MCP serverInfo 0.1.15·86 tools·device_list PASS. 실제 외부 Codex OAuth 로그인/원격 입력은 이번 smoke에서 NOT_RUN |
| 기존 141 서버 | 새 source `Status-Gateway.ps1 -StateDir .racp/two-pc-141-121` exit 0, healthy·동일 장비 ONLINE 확인. 기존 서버를 재시작하거나 이관하지 않았다; [상태](../../dist/acceptance/RUN-20261007-host-powershell/live-status.json) |

> [!NOTE]
> `.ps1` 더블클릭을 실행 방법으로 안내하지 않는다. PowerShell에서 실행하며 PS5의 Restricted 환경에는 명시적 process RemoteSigned 명령을 가이드에 제공한다. 배포본은 unsigned 개발 ZIP이며 Windows Service·부팅 자동 시작·운영 OAuth 공급자 설치·clean PC 인수는 별도 범위다.

최종 검증에서 PowerShell 원본 5개와 배포 복사본의 byte 일치, ZIP size/SHA-256/CRC, CMD·credential 미포함, smoke runtime 프로세스 잔존 0개 및 상대 파일 링크 164개 오류 0건을 확인했다. [최종 검증 기록](../../dist/acceptance/RUN-20261007-host-powershell/final-verification.json)을 보관했고 `git diff --check`도 PASS이다.

---

<a id="windows-test-first-20261007"></a>

## 14. 테스트 우선 재개: Windows 회귀와 실제 121 관측

2026-10-07 사용자 지시에 따라 Client 빌드는 보류하고 테스트를 진행했다. 이 절은 §12의 실제 후속 검사이며, §13의 병행 Gateway 작업과 별도로 기록한다. 이번 테스트 중 SSOT는 병행 작업에 의해 0.1.14→0.1.15로 변경됐고 버전을 되돌리거나 별도로 증가시키지 않았다. 로컬 검사 프로세스에는 시작 당시 0.1.14가 로드된 결과도 있어 이 집계를 단일 최종 릴리스 후보의 PASS로 사용하지 않는다. 실제 121 Agent는 v0.1.10이며 owner API 경로로 확인했다. 현재 대화의 RACP MCP 도구는 여전히 없어 actual Codex 인수는 BLOCKED_ENV다.

### 14.1 로컬 검사 결과와 시험 계약 보정

| 검사 묶음 | 실제 결과 / 증거 |
| :--- | :--- |
| 전체 unit | **208 passed**, failure/error/skip 0; [JUnit](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/unit-final.xml) |
| 선택형 Windows GUI·Guardian | **9 passed / 4 skipped**, failure/error 0; [JUnit](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/native-current.xml). skip은 OS가 own fixture foreground 활성화를 거부한 경우이며 actual 121의 PASS로 이관하지 않음 |
| Console cookie/CSRF/origin/session/SSE 회귀 | **6 passed**; [JUnit](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/console-auth-final.xml) |
| Browser·file 회귀 | **5 passed / 7 failed / 1 teardown error**; [JUnit](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/browser-final.xml). timeout/cancel UNKNOWN, native worker·profile cleanup unknown, CLI timeout·Agent shutdown timeout |
| 초기 integration 묶음 | **47 passed / 3 failed**에서 중단; [JUnit](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/integration-current.xml). Browser 정리 실패 2개와 아래 수정 전 Console 시험 경합 1개. 최종 묶음과 중복이 있어 합산하지 않음 |
| 나머지 28 integration 파일 | **77 passed / 16 failed / 19 skipped**; [JUnit](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/integration-remaining.xml). Guardian startup 2, output recovery 1, plugin supervisor 10, terminal/stream 3 실패 |
| 필수 lint·type | Ruff PASS, Mypy **135 source files PASS**; [lint](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/lint-current.log), [type](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/mypy-current.log) |

집계 원문은 [검사별 결과 JSON](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/results-summary.json)에 있다. 동일 시험의 재현·재시도·부분 집계를 단순 합산하지 않는다. 현재 전체 품질 gate는 **FAIL**이다.

실패 원인을 확인한 두 시험 계약만 보정했다. `activity()`의 link/reparse 거부는 `local_path()`가 내는 PermissionError이므로 기존 ValueError 기대를 맞추고 대상 원본 및 링크 보존 assertion을 추가했다. Console의 sleeping ConPTY도 시작 ANSI 프레임을 낼 수 있으므로 로그아웃 전에 인증 상태에서 초기 프레임을 소비·ACK한 뒤 조용한 stream의 SESSION_EXPIRED와 relay 정리를 검사한다. 변경 전 각각 실제 실패를 확인했고 변경 후 위 unit/Console 묶음을 통과했다. 제품의 경로 거부·권한·세션 만료 동작을 완화하지 않았다.

Browser는 [개별 재현](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/browser-timeout-repro.log)에서 Chromium 자식 잔류 assertion 실패를 확인했다. [Job 소속/종료 probe](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/browser-cleanup-probe.json)의 단독 실행은 1 passed였지만, 다시 묶음 검사에서 실패했으므로 해결로 선언하지 않는다. 느려진 integration의 [stack](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/integration-stack.txt)은 executor의 `subprocess.communicate()`/reader thread 대기를 보였다. 이후 전체 묶음은 실제 종료해 위 XML을 생성했다. timeout을 임의로 늘리거나 UNKNOWN을 PASS로 바꾸지 않았다.

### 14.2 실제 121 화면 입력과 자체 GUI 증거

| 실행 | 실제 결과 / 증거 |
| :--- | :--- |
| `RUN-20261007-173925-owner-desktop` | own fixture PID/create_time/session/window 조회 후 desktop.activate **FOCUS_MISMATCH**. 입력 진행 불가, Broker가 lease를 abort한 뒤 release는 PERMISSION_DENIED. 후속 timeout·창 부재·revision 삭제로 정리 확인; [결과](../../dist/acceptance/RUN-20261007-173925-owner-desktop/results.json), [정리](../../dist/acceptance/RUN-20261007-173925-owner-desktop/cleanup.json) |
| `RUN-20261007-175300-owner-desktop` | fixture foreground 확인 뒤 PNG Artifact 회수까지 수행. 시험 script가 capture 응답의 직접 sha256을 잘못 가정해 중단. 해시는 Artifact 메타데이터에서 비교하도록 후속 시험 수정; [결과](../../dist/acceptance/RUN-20261007-175300-owner-desktop/results.json). 정상 timeout·창 부재·폴더 삭제 complete |
| `RUN-20261007-175700-owner-desktop` | 사용자가 own 창의 제목 표시줄을 클릭했고 대상 foreground를 확인했다. PNG/hash 검증 후 desktop.click은 SUCCEEDED/os_dispatch_only이지만 자체 clicks counter가 증가하지 않아 **FAIL**. [결과](../../dist/acceptance/RUN-20261007-175700-owner-desktop/results.json), [시험 창 PNG](../../dist/acceptance/RUN-20261007-175700-owner-desktop/own-window.png). 캡처의 컨트롤 미표시와 own fixture 준비/좌표/dispatch 경로를 추가 진단해야 하며 제품/fixture 원인은 확정하지 않음. 정상 timeout·창 부재·revision 삭제 complete |

실제 관측 display는 **3840×2160 / 150%**, session 1이다. 이전 기록의 값을 재사용하지 않고 새 관측 응답에서 확인했다. [짧은 fixture 진단](../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/gui-diagnostic.json)은 새 own script가 8초 timeout 뒤 exit 0/cleanup complete이며, 5초 thread dump는 message pump와 refresh 대기를 보였다. key/type/scroll/drag/UIA는 실패한 click 뒤 요청하지 않았고 PASS로 집계하지 않는다. 입력 시험은 actual Codex MCP가 아닌 owner API 경로다.

### 14.3 실제 121 메모리·Artifact·정책 경계

`RUN-20261007-180200-owner-memory`는 121에서 시험 소유 process에 `bytes(range(256))*65536`의 16MiB buffer를 만들었다. PID/생성 시각/boot를 확인하고 **32B·4KiB·128KiB·16MiB** 각각을 읽어 A의 기대 bytes와 SHA-256를 비교했다. 128KiB와 16MiB는 인증 Artifact download로 검증했다. stale create_time은 PRECONDITION_FAILED, 16MiB+1은 접수 전 HTTP 400/INVALID_ARGUMENT, read_only는 접수 전 HTTP 403/PERMISSION_DENIED이며 operation ID가 없다. 모든 실제 read는 standard의 요청별 owner 승인으로 실행했다.

[메모리 결과](../../dist/acceptance/RUN-20261007-180200-owner-memory/results.json)와 [한도/정책](../../dist/acceptance/RUN-20261007-180200-owner-memory/limits-policy.json)에 PASS를 기록했다. 정리는 생성 시각으로 확인한 own process에만 force terminate를 보내 PID 부재를 확인하고 own 폴더를 revision 확인 후 삭제해 complete다. 앞선 `RUN-20261007-180100-owner-memory`는 inline 응답 필드를 base64로 잘못 가정한 harness 오류이며 process/folder 정리는 complete로 보존했다. 정상 계약의 `bytes_hex`를 사용한 후속 실행만 PASS다.

> [!IMPORTANT]
> Windows 핵심 인수와 배포 gate는 미완료다. 우선 결함은 actual 121 input의 독립 결과 불일치, Browser cancel/timeout/cleanup, Guardian startup, 완료 출력의 deadline 경합, plugin worker와 ConPTY/stream 종료·출력 경계다. 과거 PASS·부분 재현 성공·이번 메모리 PASS로 이를 대체하지 않는다. actual MCP 연결·새 후보의 실제 B 시험·display/session 조합·장애/restore·부하·8시간 soak도 남아 있다. Client 빌드는 사용자가 마지막 단계에서 요청할 때 재개한다.

실행 종료 후 별도 [process inventory](../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/local-process-cleanup.json)에서 이번 pytest-20/21 test root에 속한 잔류 process가 없는 것을 확인했다. 다른 사용자 process나 병행 채팅의 시험 root는 정리 대상으로 삼지 않았다. 로그의 fixture cookie/CSRF 값은 redaction했고 바이너리 SHA 검증은 원본 bytes로 수행했다.

---

<a id="racp-native-re-mcp-20261007"></a>

## 15. 실패 분류와 기존 Codex 리버싱 MCP 원격 인수

사용자의 후속 요청에 따라 [Windows 계획 E2E-D](../spec/windows-engineering-plan.md#racp-native-re-acceptance)에 기존 IDA/WinDbg MCP의 원격 정적·dump·live debugger 인수를 핵심 항목으로 추가했다. **Client/Agent 제품 빌드·패키징은 실행하지 않았다.** 시험 입력으로 생성한 2048-byte AMD64 PE는 배포용 RACP 빌드가 아니다. 현재 SSOT v0.1.15를 유지했으며, 다음 결과의 B는 설치된 v0.1.10 Agent다.

### 15.1 실패 원인과 수정 범위

터미널 MCP 시험은 첫 `terminal_read`에서 Python `>>>`가 반드시 나온다고 가정했다. 실제 실패는 ConPTY 초기 ANSI 모드 프레임만 반환한 경우였다. `next_cursor`를 따라 최대 10회 읽으며 실제 프롬프트를 모아 검증하도록 시험을 보정했다. 제품의 timeout·출력·인증 기준을 바꾸지 않았다. 변경 전 실패와 [단독 수정 후 1 passed](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/terminal-fixed.xml), [version+terminal 최종 7 passed](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/version-terminal-final.xml)를 확인했다.

| 회귀 범위 | 이번 실제 결과와 판정 |
|---|---|
| Terminal·Browser·Browser file·plugin supervisor | [34개 묶음](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/isolation-regression.xml): **32 passed / 2 failed**. terminal 및 plugin 묶음은 통과; Browser cancel은 UNKNOWN, shutdown은 5초 timeout |
| 위 두 Browser lifecycle 실패 재현 | [개별 재검](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/browser-lifecycle-repro.xml): **2 passed**. 묶음에서의 간헐 실패 원인·해결은 미확정이며 전체 Browser PASS로 승격하지 않음 |
| 필수 lint | [Ruff](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/lint-final.log): scripts/version.py, version unit, packages/apps 및 수정 terminal 시험 PASS |
| 필수 type | [Mypy](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/mypy-final.log): **135 source files PASS** |

묶음 [실행 로그](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/isolation-regression.log)의 uvicorn/platform WMI query는 `0x8007000e` 오류도 기록했다. 원인 후보인 호스트 자원/Windows 조회 경합과 native Job/profile 정리 경로를 분리해야 한다. 이 로그만으로 제품 결함이나 Windows 서비스 결함을 확정하지 않았고 WMI 서비스·stdlib를 변경하지 않았다. §14의 121 GUI click counter 불일치도 미해결이다. 재시도 결과는 기존 묶음과 중복되므로 합산하지 않는다.

### 15.2 RACP → 121 파일 → 기존 IDA MCP: 정적 분석 PASS

`RUN-20261007-racp-re-mcp-1`의 정상 시험 PE를 RACP owner API/standard의 요청별 승인으로 121의 고유 own 폴더에 저장하고, B hash를 조회한 뒤 인증 Artifact로 A에 회수했다. 원본/B/회수 SHA-256은 모두 `1c32769f9aaecab694fe254b3d3b6a9963afe99c331a4cda4dfaa10d24012a17`이다. [원격 전송·정리 증거](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/remote-results.json)와 [입력 manifest](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/probe-manifest.json)를 보관했다.

실제 `mcp__ida_mcp__open_database`로 **B에서 회수한 PE**를 열고 `execute_python`으로 `racp_transform`의 RVA `0x1000`, `imul eax,ecx,7; xor eax,5Ah; retn` 및 `(7*a1)^0x5A` 의사코드를 얻었다. [실제 IDA 응답](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/ida-static-result.json)과 [entry 분석](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/ida-entry-result.json)은 입력 6 → marker 112 저장 → Sleep(120000) → ExitProcess(0)을 확인했다. 기능 코드·정적 분석 결과를 원본 바이트와 대응시켰다.

정적 분석은 **RE-MCP-STATIC PASS**다. RACP 경로는 현재 세션에 없는 RACP MCP 대신 owner API를 사용했으며, IDA 경로는 실제 Codex의 기존 MCP를 호출했다. 이 차이를 숨기거나 전체 actual RACP MCP 인수로 처리하지 않는다. DB를 저장하고 이번 시험의 IDA lease 2개만 닫았다; [DB 보존·정리](../../dist/acceptance/RUN-20261007-racp-re-mcp-1/ida-cleanup.json). B의 own 폴더는 revision을 확인해 삭제했고 이번 PE를 원격 실행하지 않았다.

### 15.3 원격 동적·live debugger: 실행 조건 미충족

121의 실제 원격 PowerShell에서 administrator=False, epoch 3·기존 boot를 확인했다. RE Lab skill의 “모든 실행 셸은 관리자 권한으로 시작하고 각 셸 내부에서 실제 권한을 확인한다” 조건에 따라 **RE-MCP-DUMP는 BLOCKED_ENV**다. 사용자에게 관리자 Client/Agent 재시작을 요청했고, 확인 전 PE 실행·dump capture를 진행하지 않았다. 과거 메모리 16MiB PASS는 이 dump 시험의 대체 증거가 아니다. 별도 `RUN-20261007-racp-re-mcp-2`에 같은 hash 입력과 own PID/create_time 확인·marker read·MiniDumpWithFullMemory 회수·정리 script를 준비했으며 미실행이다.

기존 WinDbg MCP의 `open_cdb_remote`는 접근 가능한 native CDB debug-server 연결 문자열이 필요하다. B의 Windows Kits/Tools 두 지정 경로에서 cdb를 찾지 못했으며 전역 미설치를 단정하지 않는다. 현재 RACP에는 일반 CDB transport tunnel이 없고 B debug-server endpoint도 준비되지 않았다. **RE-MCP-LIVE는 BLOCKED_ENV/지원 경로 미검증**이며 중단점·step·continue 성공을 주장하지 않는다. debugger 설치·원격 endpoint·새 adapter는 이번 시험에서 추가하지 않았다. 기존 MCP의 dump 분석과 live endpoint 연결을 따로 검증한다.

> [!IMPORTANT]
> 원격 정적 분석의 실제 조합은 검증됐다. 동적 dump 분석은 관리자 Agent 재시작 후 WinDbg MCP가 회수 dump를 실제 읽어야 PASS이며, 실시간 원격 디버깅은 별도의 endpoint와 attach/break/step/continue 증거가 필요하다. 전체 Windows 핵심 인수와 Browser/121 입력 결함은 계속 미완료다.

문서·증거 최종 검사에서 계획서/현황의 상대 링크 181개 오류 0, 작업 ID 12개·기존 상세 시험 54개·display/session ID 8개 보존, 원본/회수 PE hash 일치를 확인했다. `git diff --check`는 exit 0이고 SSOT는 0.1.15다. 로컬 검사 환경은 별도 `.tools/desktop-build-venv`를 사용했으며 운영 Gateway 환경과 병행 채팅의 변경을 되돌리지 않았다.

---

<a id="gateway-web-plan-20261007"></a>

## 16. Gateway 웹 관리 서버 계획서 작성

2026-10-07 사용자 요청으로 [Gateway 웹 관리 서버 작업 계획서](../spec/gateway-web-management-plan.md)를 작성했다. 문서 상태는 Draft, 제품 기준선은 **v0.1.15**다. 기존 Python Gateway·React Console·SQLite WAL·Agent outbound WSS를 재사용하고, 단일 realm의 중앙 웹 관리와 Windows 서비스·Setup·독립 updater·backup/restore를 상세 설계했다. 기존 기능과 제안 기능, local-owner와 운영 OIDC, 자원 소유자와 실제 작업자, synthetic 시험과 실제 141→121 인수를 구분했다.

G00–G12 **13개 작업**, GT01–GT29 **29개 시험군**, GA01–GA03 **3개 묶음 인수**를 정의했다. 각 작업에 선행 조건·생성/수정 파일·인터페이스·test-first 절차·검증 명령·합격 기준을 연결했다. 초기 부하/보관/복구 목표와 31–48 인일은 계획 추정이며 실측·지원 보증이 아니다. 문서 포털·명세서 목록·현재 Gateway 가이드에 상호 링크를 추가했다.

이번 작업은 문서 작성이다. 제품 코드·버전·lock·실행 서비스·배포 파일을 이 배치에서 변경하지 않았고, 계획의 신규 테스트·서비스 설치·updater·Setup을 실행하지 않았다. 문서의 지시나 체크박스는 현재 구현 실행에 대한 사용자 승인이 아니다. 실제 후속 구현 결과와 차단 조건은 이 현황 문서에 누적한다.

---

<a id="windows-fixture-input-0116"></a>

## 17. v0.1.16 시험 창 x64 메시지 처리 수정과 121 입력 완주

이 후속 배치는 §14의 원격 click counter 불일치를 원인부터 조사했다. 최초 [입력 없는 진단](../../dist/acceptance/RUN-20261007-gui-preflight/evidence.json)에서 121의 child HWND·가시성·활성화·bounds·WindowFromPoint는 정상이나 Canvas WM_PAINT가 0회, UIA Button name이 `None`, 캡처는 빈 client area였다. fixture의 `GetWindowLong(GWL_WNDPROC)`가 64-bit procedure 주소 대신 0을 반환해 native control의 기본 메시지 처리가 사라졌다. [pywin32의 구현](https://github.com/mhammond/pywin32/blob/main/win32/src/win32gui.i)은 `SetWindowLong`이 pointer-sized 원래 주소를 반환하며 그 값을 CallWindowProc에 사용하도록 정의한다.

원래 주소를 `SetWindowLong` 반환값으로 보존한 [한 변수 비교 시험](../../dist/acceptance/RUN-20261007-gui-preflight-pointer-safe/evidence.json)과 [수정 source 재검](../../dist/acceptance/RUN-20261007-gui-preflight-fixed/evidence.json)은 Canvas paint 1회·Button name `Fixture Invoke` 및 실제 컨트롤 표시를 확인했다. `scripts/desktop_acceptance_fixture.py`의 native control subclass만 수정했고 부모·canvas 메시지 처리나 Broker의 권한/foreground/lease 기준은 변경하지 않았다.

[121의 pointer 직접 비교](../../dist/acceptance/RUN-20261007-gui-preflight-fixed-pointers/evidence.json)에서도 GetWindowLong getter는 0, 같은 HWND의 GetWindowLongPtrW는 유효한 64-bit procedure 주소를 반환했다. 이 검사에도 입력을 주입하지 않았고 own timeout·process/folder 정리를 완료했다.

회귀 시험은 캐시된 타 프로세스 caption 조회에 의존하지 않고 실제 WM_GETTEXT를 own Button에 전달해 원래 native procedure가 동작하는지 검사한다. 수정 전 [native 및 zero-pointer 환경 2 failed](../../dist/acceptance/RUN-20261007-gui-preflight/native-control-red-message.xml)를 확인했고, 수정 후 [3 passed / 1 skipped](../../dist/acceptance/RUN-20261007-gui-preflight/native-control-green.xml)다. skipped는 A의 own fixture foreground 활성화를 OS가 거부한 실제 입력 시험이다. 초기 caption-only 검사는 결함을 놓쳤으므로 최종 회귀 증거로 사용하지 않는다.

### 17.1 실제 121 owner API 입력 및 정리 PASS

`RUN-20261007-190500-owner-desktop`은 설치된 B Agent v0.1.10에서 수정한 fixture를 실행했다. Device/boot/epoch/session/window/PID/create_time을 다시 바인딩했고, 다음 동작의 **자체 counter/text/scroll/drag 결과**를 매 단계 확인했다.

| 동작 | 실제 확인 결과 |
|---|---|
| single/right/double click | 각각 Button click/right/double counter 일치 |
| Unicode·key | `RACP 한글 😀`, Ctrl+A/Backspace 후 빈 문자열, Tab event 확인 |
| scroll·drag | scroll `[0,120]` → `[0,0]`, drag `[140,80]`·event 1·capture 해제 |
| UIA | Value `UIA 한글\r\nsecond line`, Invoke 후 clicks 3 |
| 회수·정리 | PNG Artifact SHA 일치, own 정상 timeout, window 부재, revision 확인 후 own 폴더 삭제 complete |

[전체 실제 결과](../../dist/acceptance/RUN-20261007-190500-owner-desktop/results.json), [실행 로그](../../dist/acceptance/RUN-20261007-190500-owner-desktop/test.log), [정상 컨트롤 PNG](../../dist/acceptance/RUN-20261007-190500-owner-desktop/own-window.png)를 보관했다. 이 실행은 별도 title 클릭 대기 없이 foreground 활성화까지 성공했다. 과거 사용자의 수동 클릭과 새 foreground/observation을 혼동하지 않는다. 실제 Codex RACP MCP/OAuth와 새 배포 후보의 B 설치 인수는 여전히 미검증이다.

### 17.2 Browser 정리 경로의 추가 진단과 잔여 조건

동작과 timeout을 바꾸지 않는 [정리 시간 trace](../../dist/acceptance/RUN-20261007-browser-lifecycle-trace/cleanup-events.json)를 넣은 4개 lifecycle 묶음은 [3 failed / 1 passed](../../dist/acceptance/browser-lifecycle-trace.xml)였다. own worker returncode 1·receiver 종료 뒤에도 전체 정리가 **5.06–5.83초**, cleanup unknown으로 끝났다. 이 묶음은 확정 PASS가 아니며 Job/native termination 확인의 5초 한도 경로를 더 조사해야 한다.

추가 [native Job·process signal trace](../../dist/acceptance/RUN-20261007-browser-native-trace-group/native-events.json)의 같은 4개 묶음은 [4 passed](../../dist/acceptance/browser-native-trace-group.xml)다. 각 종료에서는 Job ActiveProcesses=0 및 retained process handle signal/exit code를 확인했고 timeout을 늘리지 않았다. 앞선 [단독 native trace](../../dist/acceptance/browser-native-trace.xml)도 1 passed다. 두 결과의 차이는 간헐 실패를 해결했다는 증거가 아니며, failure 실행의 마지막 native handle 상태를 확보해야 한다. 당시 [A 호스트 관측](../../dist/acceptance/RUN-20261007-gui-preflight/host-resource-observation.json)은 CPU 100%, 가용 메모리 약 6GB였고 전역 서비스나 타 사용자 process를 변경하지 않았다.

후속 Browser+file 묶음은 [native trace](../../dist/acceptance/RUN-20261007-browser-native-trace-combined/native-events.json)에서 실패 순간을 확보했다. 첫 profile/recovery 시험의 close는 ActiveProcesses=0·exit code 1인데 own child PID 11188의 handle wait가 **WAIT_TIMEOUT(258)** 상태로 5.031초 한도에 닿아 UNKNOWN이 됐다. Windows의 [비동기 종료 계약](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)에 따라 pending I/O가 완료/취소되기 전에는 종료 확인이 끝나지 않을 수 있다. 관측은 native 종료 확인 지연을 증명하지만 특정 driver/I/O 원인은 미확정이다. exit code만 보고 cleanup을 complete로 바꾸거나 timeout을 늘리지 않았다. [최종 JUnit](../../dist/acceptance/browser-native-trace-combined.xml)은 **6 passed / 6 failed**, failure/error/skip 집계에서 error/skip은 0이다. Cancel·shutdown, download/upload/oversize 및 controller 강제 종료 뒤 child lifetime 실패가 남았으며 전체 Browser gate는 FAIL이다.

### 17.3 버전·검증과 동적 분석 실행 조건

저장소 규칙에 따라 수정 배치의 PATCH를 `scripts/version.py`로 **0.1.15→0.1.16** 한 번 증가시키고 `uv lock --offline`로 lock을 동기화했다. 제품 빌드·패키징이나 운영 `.venv` 재설치를 실행하지 않았다. [version·초기 등록 opt-in 27 passed](../../dist/acceptance/RUN-20261007-gui-preflight/version-optin-final.xml), [Ruff PASS](../../dist/acceptance/RUN-20261007-gui-preflight/lint-final.log), [Mypy 135 files PASS](../../dist/acceptance/RUN-20261007-gui-preflight/mypy-final.log)를 확인했다. 각 재현/비교 묶음은 중복이 있어 합산하지 않는다.

RE-MCP-STATIC의 원격 파일 회수→실제 IDA 분석 PASS는 §15의 증거를 유지한다. 이번 재확인에도 121은 administrator=False·epoch 3·동일 boot였으므로 RE Lab의 실제 관리자 셸 조건에 따라 원격 PE 실행·dump/WinDbg 분석은 미실행이다. 관리자 Agent 재시작 확인과 live CDB endpoint 준비가 필요하다. Windows 핵심 인수 전체·배포 gate·Browser 간헐 실패는 계속 미완료이며 goal을 완료로 표시하지 않는다.

작성 중 병행 작업으로 현재 SSOT는 **0.1.16**이 됐다. 계획서의 Gateway ZIP/코드 기준선 0.1.15와 구분했고 해당 변경을 되돌리지 않았다. 신규 계획의 기능이 v0.1.16에 구현됐다는 의미는 아니다.

계획서 자체 리뷰에서 권한 승인 주체, actor/owner 구분, UNKNOWN 작업의 update blocker, 공유 Journal과 Gateway 전용 migration의 경계, Ed25519 manifest 서명, revision·경로·queue 한도를 보완했다. 최종 문서 5개의 메타데이터·code fence와 상대 파일 링크 243개·anchor 55개를 검사해 오류 0건을 확인했다. [문서 검증 기록](../../dist/acceptance/gateway-web-plan-document-verification.json)을 보관했다.

---

<a id="racp-windbg-dump-pass"></a>

## 18. 실제 121 실행 덤프와 Codex WinDbg MCP 분석

사용자는 121 Client를 관리자 권한으로 재실행하고 Agent를 시작했다고 답했다. 실제 원격 셸에서도 administrator=True를 확인했고, 새 boot `boot_853e8130c7b24a27abf4ee2767d9cdfb`·epoch 4에 시험을 바인딩했다. 일반 권한 예외는 사용하지 않았다. B 설치 Agent는 v0.1.10이고 A source는 v0.1.16이며 제품 빌드·배포는 계속 보류했다.

### 18.1 RACP 실행·메모리·dump 회수 PASS

`RUN-20261007-racp-re-mcp-2`는 §15에서 IDA로 확인한 같은 SHA-256의 정상 AMD64 PE를 B의 새로운 own 폴더에 저장했다. A/B/회수 파일 hash를 비교한 뒤 PID **13164**, create_time **1791369021.4998455**로 실행 identity를 확인했다. `process.memory_read`의 `0x140003000` 값은 `70000000` = **112**로 정적 분석의 `(7*6)^0x5A`와 일치했다.

같은 own PID/생성 시각을 다시 검증한 관리자 Python 셸에서 DbgHelp `MiniDumpWriteDump(MiniDumpWithFullMemory)`를 호출했다. **26,155,842 bytes**의 dump를 인증 Artifact로 회수했고 SHA-256 **`0f4c54b9ec29a57b1d4398ee7ca346b535c26005cb3bf898fdc49d318f5802bc`**를 비교했다. [원격 실제 결과](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-results.json), [실행 로그](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-test.log)를 보관했다. own process를 생성 시각/boot로 한정해 종료하고 own 폴더를 revision 확인 후 삭제했다.

### 18.2 기존 WinDbg MCP에서 원격 실행 상태 분석 PASS

최초 `open_cdb_dump`의 자동 `!analyze -v`는 [60초 timeout](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/cdb-open-timeout.json)이었다. 실제 CDB는 살아 있었으나 upstream은 등록된 session_id를 오류 응답에 반환하지 않아 해당 세션을 도구로 이어갈 수 없었다. 프로세스의 own dump 경로/PID/create_time을 확인한 뒤 [해당 CDB만 명시적으로 정리](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/cdb-timeout-cleanup.json)했다. 관측 timeout만으로 process 종료를 가정하거나 기존 사용자 debugger를 재시작하지 않았다.

대신 A의 기존 CDB가 **회수 dump**를 연 local named-pipe engine을 시작하고, Codex의 실제 `open_cdb_remote` MCP를 그 A-local endpoint에 연결했다. 새 MCP adapter나 B debugger 설치·방화벽 변경은 수행하지 않았다. 실제 session_id `cdb-cae8b439`에서 `run_cdb_command`가 다음을 확인했다.

| 확인 대상 | 실제 WinDbg MCP 결과 |
|---|---|
| memory marker | `dd 0x140003000 L1` → `00000070` (112), B live read와 일치 |
| 함수 bytes·명령 | RVA `0x1000`, `6bc10783f05ac3`, `imul eax,ecx,7; xor eax,5Ah; ret`, IDA 및 원본과 일치 |
| module identity | base `0x140000000`, `RACP_RE_Probe.exe`, B의 이번 own folder 경로, dump thread PID `0x336c`=13164 |
| stack/register | `ntdll!NtDelayExecution` → `KERNELBASE!SleepEx` → own entry 주소, 정적 분석의 Sleep 대기 동작과 일치 |

[실제 MCP open 응답](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/windbg-open-response.json), [명령 응답](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/windbg-validation-response.json), [독립 결과 assertion](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/native-analysis-assertions.json)을 저장했다. 이 파일은 정상 실행 중 수집한 snapshot이며 실제 crash로 해석하지 않는다. PDB가 없는 own PE의 stack 표시는 가장 가까운 export 이름과 offset이며, 그 이름을 실행 중인 정확한 함수로 단정하지 않는다.

**RE-MCP-STATIC와 RE-MCP-DUMP는 실제 조합 PASS**다. RACP 경로는 owner API이고 analyzer 경로는 실제 Codex IDA/WinDbg MCP다. named-pipe는 A에서 dump engine/client를 연결한 것으로 B에 대한 실시간 debugger attach/step 증거가 아니다.

도구 목록이 재설정된 뒤 실제 MCP close는 [세션 부재](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/windbg-client-cleanup.json)를 반환했고, [own CDB process inventory](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/cdb-process-inventory.json)에서도 잔류 0개였다. 다시 B를 조회해 [own PID 부재](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-process-cleanup-check.json)와 [폴더 PATH_NOT_FOUND](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-folder-cleanup-check.json)를 확인했다.

### 18.3 실시간 연결·인증 및 Browser 잔여 조건

현재 Gateway의 실제 `/mcp/` initialize는 [HTTP 503 / Remote MCP requires configured OAuth](../../dist/acceptance/RUN-20261007-racp-mcp-protocol/initialize-response.json)다. SDK 버전 추측 대신 실제 ingress 응답으로 원인을 확인했다. OAuth 미설정 public endpoint의 owner-only 차단이며 이를 해제하거나 인증을 우회하지 않았다. 실제 Codex RACP MCP/OAuth 도구 인수는 아직 BLOCKED_ENV다.

관리자 B의 [SDK/Tools/App Execution Alias 및 WinDbg Appx inventory](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-debugger-inventory.json)는 cdb_paths/windbg_packages가 비어 있었다. 검사한 경로에서 debugger endpoint 준비를 확인하지 못했으며 전역 미설치를 단정하지 않는다. 직접 B attach/break/step/continue의 RE-MCP-LIVE는 미검증이고, 현 RACP에는 일반 CDB transport tunnel이 없다.

Browser는 [역할 조회 재검 2 passed](../../dist/acceptance/browser-native-roles.xml)였지만, worker stdin EOF 후 종료를 유도한 [비교 4개 중 3 failed](../../dist/acceptance/browser-eof-experiment.xml)로 해결되지 않았다. [trace](../../dist/acceptance/RUN-20261007-browser-eof-experiment/native-events.json)에서 main Chrome과 NetworkService의 signal 대기를 확인했다. 이 가설을 제품 수정으로 반영하지 않았고 deadline·cleanup 성공 기준을 유지했다. 전체 Browser gate와 Windows release gate는 계속 FAIL/미완료다.

### 18.4 Gateway 상태 복원

분석 후 `192.168.29.141:8765` listener가 사라진 것을 실제 확인했다. localhost:8765는 다른 앱의 listener이므로 유지했다. 기존 `.racp/two-pc-141-121` DB·owner store·CA/server certificate를 보존한 채 기존 `.venv`의 Gateway를 141 주소에 hidden으로 다시 시작했고, [PID/생성 시각·설정](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/gateway-restoration-process.json)과 [121 재연결 epoch 5/새 boot](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/gateway-restoration-observation.json)를 확인했다. 이 관측만으로 모든 장애·중복 부작용 gate를 PASS로 승격하지 않는다.

이후 별도의 own counter 시험으로 실제 Gateway 재시작과 멱등성을 검사했다. 다른 ACCEPTED/RUNNING 작업이 없는 것을 확인하고, 저장한 정확한 PID/create_time/command로 이번 Gateway만 종료·복원했다. **Agent boot는 유지, epoch 5→6**, 이전과 동일한 key의 shell.exec는 **같은 operation ID·동일 결과**를 반환했고 파일의 실행 counter는 **1**이었다. [실제 결과](../../dist/acceptance/RUN-20261007-gateway-restart-2/results.json)의 PASS 및 own revision 폴더 정리 complete를 확인했다. 최초 [시도](../../dist/acceptance/RUN-20261007-gateway-restart/results.json)는 복사한 create_time의 정밀도 차이로 identity guard가 재시작 전에 거부했고 own 폴더를 정리했다. 임의 허용 오차를 추가하지 않고 저장된 metadata의 정확한 값을 사용했다. 이 PASS는 Gateway 재시작 한 종류의 owner API 시험이며 네트워크·Agent·로그인·재부팅·actual Codex MCP의 전체 복구 matrix를 대체하지 않는다.

[최종 분석 검증](../../dist/acceptance/RUN-20261007-racp-re-mcp-2/verification-final.json)은 상대 링크 227개 오류 0, 원본/회수 PE·dump SHA-256, native 함수 bytes, live/dump marker와 own process/folder/CDB 정리를 독립 대조해 PASS였다. 문서 추가 전 집계이며 원본 실행 기록을 보존한다.

---

<a id="windows-host-resource-regression"></a>

## 19. 호스트 자원 기준선 재확인과 Python 전체 회귀

동일 호스트 `DESKTOP-06NU139`의 실제 물리 메모리 보고가 이전 약 **16GiB**에서 현재 약 **32GiB**로 바뀌었다. 이전 [자원 관측](../../dist/acceptance/RUN-20261007-gui-preflight/host-resource-observation.json)은 CPU 100%·가용 약 6GB였고, 현재 [호스트 identity](../../dist/acceptance/RUN-20261007-browser-late-signal/host-identity.json)와 [자원 관측](../../dist/acceptance/RUN-20261007-browser-late-signal/current-host-resource.json)은 6 logical CPU·가용 약 23GB·CPU 39.2%다. 이 배치에서 메모리 설정·서비스·프로세스 우선순위를 변경하지 않았다. 실제 자원 조건이 다르므로 앞선 실패와 현재 통과를 같은 환경의 제품 수정 결과로 합치지 않는다.

기존 cleanup deadline과 return 값을 유지하는 진단 hook으로 Job에 속한 own child handle을 복제해 보존하고, UNKNOWN일 때만 cleanup 반환 뒤 최대 15초의 native signal을 별도 thread에서 관측하도록 했다. 추가 관측 대기는 제품 cleanup이나 operation deadline을 늘리지 않는다. [lifecycle 3 passed](../../dist/acceptance/browser-late-signal.xml), [Browser/file 12 passed](../../dist/acceptance/browser-late-signal-combined.xml)를 확인했다. 후자 [15개 cleanup event](../../dist/acceptance/RUN-20261007-browser-late-signal-combined/events.json)는 모두 complete, 최대 0.110초였고 late-signal 관측이 필요한 UNKNOWN은 발생하지 않았다.

이 결과는 현재 자원 조건에서의 PASS다. 실패 시 kernel/I/O 원인은 아직 확정하지 못했고, 제품 Browser 코드·5초 종료 확인·성공 기준은 변경하지 않았다. WMI `0x8007000e`와 native 종료 지연의 과거 증거는 §14/17/18에 보존한다. 현재 source v0.1.16의 `tests/unit`+`tests/integration` 전체 회귀를 별도로 시작했으며, 이 절에 실제 종료 결과를 이어서 기록한다. 배포용 build는 실행하지 않았다.

### 19.1 현재 기준선 전체 Python 결과

[전체 JUnit](../../dist/acceptance/python-current-32gb.xml)은 **361 passed / 21 skipped**, failure/error 0, 577.12초다. [분리 집계](../../dist/acceptance/python-current-32gb-summary.json)는 unit **211 passed**, integration **150 passed / 21 skipped**다. Browser/file의 12개도 이 전체 실행에서 진단 hook 없이 통과했다. 이전 실패를 삭제하거나 현재 수치와 중복 합산하지 않는다.

| skip 범위 | 개수 / 실제 사유 |
|---|---|
| own Windows GUI | 15 / `RACP_TEST_GUI=1` 미설정; 별도 opt-in 검사로 이어감 |
| POSIX dirfd | 1 / 실제 POSIX runner 필요; Windows에서 지원 증거로 이관하지 않음 |
| native GDB | 2 / 명시적인 설치 GDB 경로 필요 |
| native Ghidra/JDK | 2 / 명시적인 설치 executable 경로 필요 |
| Docker Keycloak | 1 / 별도 local integration opt-in 필요; 직접 RACP MCP/OAuth의 현재 503은 §18에서 실제 확인 |

지연 관측을 위해 own pytest stack을 읽으려던 시점에는 검사 프로세스가 이미 종료해 py-spy가 process-open 오류를 반환했다. 실제 exec handle exit 0·완성된 JUnit으로 종료를 확인했고 그 결과를 pytest hang이나 제품 오류로 해석하지 않는다. 현재 기본 Python gate는 PASS이며 native/실제 MCP/새 B 배포 후보 및 Windows release gate는 별도로 판정한다.

### 19.2 GUI opt-in 및 동시 변경의 검증 경계

현재 호스트에서 `RACP_TEST_GUI=1`로 실행한 [native GUI JUnit](../../dist/acceptance/native-gui-current-32gb.xml)은 **5 passed / 10 skipped / 160 deselected**, 22.49초다. skip 9개는 `OS did not activate the owned fixture`, 1개는 `OS refused activation of the owned fixture`다. 창 활성화 거부를 입력 성공으로 집계하지 않는다. §17의 실제 121 PC 입력 PASS는 별도 증거로 유지한다.

전체 Python 실행이 수집한 뒤 다른 작업에서 Gateway config/migrations/service/management 파일과 시험이 추가되고 있다. §19.1의 361 passed는 이 신규 코드의 검증을 포함하지 않는다. 이후 [전체 Ruff 검사](../../dist/acceptance/lint-oauth-preflight.log)는 **12 errors / exit 1**이었다. 동시 작성 중인 Gateway 파일을 이 배치에서 덮어쓰지 않았으며, 전체 현재 작업 트리의 lint gate는 FAIL이다. 기존 onboarding·Agent·GUI fixture 변경 범위의 [별도 Ruff 검사](../../dist/acceptance/lint-onboarding-oauth-preflight.log)는 exit 0이다.

추가 [전체 mypy 검사](../../dist/acceptance/mypy-oauth-preflight.log)는 142개 source file 중 신규 `windows_service.py`의 Windows 기준 `fcntl.flock/LOCK_EX/LOCK_NB/LOCK_UN` attribute 오류 **5개 / exit 1**이었다. 해당 파일이 다른 작업에서 작성 중이므로 이 배치에서 수정하지 않았다. SSOT는 **0.1.16**이며 [version·desktop opt-in·connection import 재검](../../dist/acceptance/onboarding-oauth-preflight.xml)은 **27 passed**, 2.35초다.

### 19.3 기존 OAuth 제공자 부재와 직접 MCP 시험 조건

사용자가 기존 OAuth/OIDC 제공자가 없다고 확인했다. 현재 로컬 PATH와 일반 설치 경로에서 Docker/Keycloak을 찾지 못했고 Java 21 경로는 있다. 저장소에는 기존 `test_oauth_keycloak.py`와 `prepare_codex_oauth_lab.py`가 있지만 Docker 실행을 전제로 하므로 그대로 실행하지 않았다. 후자의 이전 lab 경로도 현재 141–121 lab과 다르다.

직접 Codex RACP MCP 시험을 위한 다음 제안은 공식 ZIP Keycloak의 별도 임시 localhost HTTPS 실행, 임시 realm/계정, 기존 Gateway DB·등록 상태 보존, PKCE S256·scope 검증 후 Codex 연결이다. 이 신규 인증 시험 환경의 구성 승인을 요청했고 아직 시작하지 않았다. §18의 owner API 기반 원격 실행·회수 및 실제 Codex IDA/WinDbg dump 분석 PASS와 직접 RACP MCP/OAuth 인수를 구분한다. 제품 빌드·패키징은 계속 보류한다.

### 19.4 실제 121 메모리 크기·identity·정책 경계

현재 Gateway listener PID/create_time 및 121 ONLINE, epoch 6·boot `boot_729ea07e06c544d684ce7d767071e71f`를 실제 재확인했다. 관리자 권한을 확인하는 자체 Python fixture에 16 MiB 고정 buffer를 만들고 source/설치 Agent v0.1.10의 owner API 경로에서 관측했다. [첫 실행](../../dist/acceptance/RUN-20261007-remote-memory-boundaries/results.json)의 regions가 요청 주소를 포함했고 **32 B / 4 KiB / 128 KiB / 16 MiB** 모두 원본 bytes·길이·SHA-256과 일치했다. 작은 결과는 inline, 큰 결과는 인증된 Artifact로 회수했다. `live_process_observation`, `atomic_snapshot=false`도 확인했다. 16 MiB SHA-256은 `341aacac661ccb210720bedaa9ead5d668fe5ea41a73532fc147c71e34040df1`이다.

첫 실행 전체의 FAIL은 시험 helper가 stale identity 오류를 임의로 예상한 데서 발생했다. 실제 제품은 기존 `ProcessProvider.identity`와 unit contract에 맞는 **PRECONDITION_FAILED / PID identity changed / not_started**를 반환했다. 첫 기록을 보존하고 helper의 기대값을 기존 계약으로 수정한 뒤 이미 통과한 크기 읽기를 반복하지 않고 [거부 항목만 재검](../../dist/acceptance/RUN-20261007-remote-memory-negative/results.json)했다. read_only profile은 **403/PERMISSION_DENIED**, stale create_time·boot는 **PRECONDITION_FAILED**, 16 KiB 접근 불가 주소는 **MEMORY_UNAVAILABLE**, 16 MiB+1은 **400/INVALID_ARGUMENT**로 거부됐고 결과 자료는 반환하지 않았다.

두 실행 모두 정확한 own PID/create_time/boot로 종료했고 process.inspect의 PROCESS_NOT_FOUND, own 폴더 revision 확인 뒤 삭제, cleanup errors 0을 기록했다. 이는 T-MEM-01/02의 설치 Agent owner API 증거 및 T-MEM-03/04의 일부 경계 증거다. 실제 read 중 cancel/spool 잔류, protected control PID, OAuth scope 및 source v0.1.16 신규 배포 후보의 검증을 대체하지 않는다. 직접 Codex RACP MCP와 live debugger gate도 그대로 미완료다.

### 19.5 실제 PC 요청 멱등성과 100 MiB 중단·재개

T-EXEC-02의 [100개 중첩 동일-key 요청](../../dist/acceptance/RUN-20261007-concurrent-counter-100/results.json)은 모두 HTTP 200/SUCCEEDED, 동일 operation ID·동일 결과였고 실제 121의 own counter 파일은 **1**이었다. 승인 대기 요청은 실행하지 않고 owner 승인 후 100개 요청을 함께 전송했다. 같은 key에 다른 payload를 보낸 요청은 **409/IDEMPOTENCY_CONFLICT/not_started**로 거부됐다. 변경 요청 뒤 counter도 1이며 own 폴더 revision을 확인해 삭제했다.

T-ART-01의 [121 생성 100 MiB binary 회수·resume](../../dist/acceptance/RUN-20261007-remote-artifact-100mib-resume/results.json)은 실제 live download stream을 **4 MiB** 수신 뒤 EOF 전에 닫았다. 로컬 prefix를 유지하고 **같은 transfer ID**로 SDK download를 재개했다. 최종 **104,857,600 bytes**와 SHA-256 `4cbf988462cc3ba2e10e3aae9f5268546aa79016359fb45be7dd199c073125c0`가 B 원본 및 Gateway Artifact와 일치했다. download committed_bytes는 중단 시 0이고 전체 검증 ACK 뒤 104857600 / DOWNLOAD_COMPLETE였다. 이를 upload의 chunk commit offset과 혼동하지 않는다. 121 own 폴더는 revision 확인 후 삭제했고 A의 회수 원본은 증거로 유지한다.

두 시험은 설치 Agent v0.1.10의 owner API 경로이며 source v0.1.16 배포 후보 및 actual Codex RACP MCP 인수를 대체하지 않는다. [현재 후보의 54개 상세 항목 audit](../../dist/acceptance/RUN-20261007-windows-current-audit/test-matrix.json)은 각 절차·합격 기준을 보존하고 installed/owner 증거와 새 후보의 미검증 상태를 분리한다. 제품 빌드·패키징은 실행하지 않았다.

후속 [보호 대상 memory 검사](../../dist/acceptance/RUN-20261007-windows-current-audit/protected-control-memory.json)는 현재 Desktop Broker의 정확한 PID/create_time/boot로 요청했고 **PERMISSION_DENIED**, result=null이었다. 주소는 0으로 지정해 guard가 실패하더라도 사용자 자료를 읽지 않도록 했다. [독립 정리·해시 검증](../../dist/acceptance/RUN-20261007-windows-current-audit/verification.json)은 네 시험의 own 폴더가 모두 PATH_NOT_FOUND, 100개 요청 operation ID 1개·counter=1, A 회수 100 MiB의 SHA-256 일치를 다시 확인했다.

현재 source의 [Client Node 검사](../../dist/acceptance/client-node-test-current.log)는 **11 passed / fail 0 / skip 0**, exit 0이었다. 실행 명령은 `node --test tests/*.test.cjs`이며 이 검사에서 product bundle을 빌드하거나 배포 패키지를 생성하지 않았다.

### 19.6 실제 파일 revision 및 입력 없는 lease 수명

[121 자체 파일 시험](../../dist/acceptance/RUN-20261007-file-lease-boundaries/results.json)에서 한글·공백 filename, UTF-8 BOM·CRLF·한글/emoji의 원본 bytes를 Artifact로 비교해 일치를 확인했다. shell의 own 파일 변경 뒤 이전 revision의 replace/delete는 **PRECONDITION_FAILED**, 외부 변경 내용은 보존됐고 새 revision으로 replace/read는 성공했다. T-FS-01의 일부 bytes 경계와 T-FS-03의 설치 Agent owner API 증거다.

같은 실행에서 read_only lease acquire는 PERMISSION_DENIED, 중복 acquire는 RESOURCE_BUSY, 잘못된 lease ID release는 PERMISSION_DENIED, 정상 renew/release는 성공했다. 실제 입력은 보내지 않았다. 만료 이후 helper가 LEASE_EXPIRED 하나만 예상해 첫 실행 전체는 FAIL이었다. Broker main의 주기적 `core.tick()`이 만료 lease를 이미 폐기한 경우 scoped_lease는 **PERMISSION_DENIED / desktop lease scope mismatch**를 반환한다. 제품 결함으로 판단하지 않았고 원래 실패 기록을 보존했다. helper의 finally가 이미 폐기된 lease release에서 중단되어 폴더 정리가 빠진 것은 시험 helper 결함이다. [별도 정리 기록](../../dist/acceptance/RUN-20261007-file-lease-boundaries/cleanup-recovery.json)에서 정확한 own 폴더와 revision으로 삭제 후 PATH_NOT_FOUND를 확인했다.

이미 통과한 파일·lease 항목을 반복하지 않고 [만료 후 재취득만 검증](../../dist/acceptance/RUN-20261007-lease-expiry-reacquire/results.json)했다. expired ID renew는 result=null/not_started로 거부됐고, 새 ID의 acquire/release는 PASS였다. 이는 같은 owner principal의 서로 다른 요청에서 관측한 T-DESK-08 일부 증거이며 다른 owner·동시 입력·modifier 해제·Guardian 장애 검증을 대체하지 않는다.

[릴리스 게이트 기준표](windows-release-gates.md)의 VERIFIED 열은 v0.1.8 역사적 기록임을 명시했다. 이를 v0.1.16 또는 121의 현 인수로 오인하지 않도록 했고 현재 실행 결과의 SSOT는 이 문서로 유지한다. 제품 source, B 설정·디스플레이·사용자 입력은 변경하지 않았다. OAuth 구성 승인 및 빌드 요청은 계속 기다린다.

### 19.7 읽기 지연 실측과 실제 Job tree 취소

[읽기 지연 원시 표본](../../dist/acceptance/RUN-20261007-read-latency-baseline/results.json)은 warm-up **20회** 후 각 **200회**의 HTTP owner API 왕복을 측정했다. 두 지표 모두 실패 **0/200**, nearest-rank p95는 **device_list 6.81 ms / filesystem.stat 45.65 ms**다. 현재 A의 6 logical CPU·32 GiB와 설치 B Agent v0.1.10 조건이며 실제 Codex MCP, 계획의 4 vCPU/8 GiB 기준 환경, 나머지 네 지표·30분 부하·8시간 soak를 대신하지 않는다. own marker 폴더는 revision 확인 후 삭제했다.

[첫 Job tree 시도](../../dist/acceptance/RUN-20261007-owned-job-tree-cancel/results.json)는 helper의 process.tree 요청이 create_time/agent_boot_id를 빠뜨려 HTTP 400으로 접수 거부됐다. protocol registry의 ProcessTarget 계약을 확인하고 helper에 payload validation 및 정확한 target identity를 적용했다. 첫 Job은 CANCELLED, own 폴더 삭제·cleanup errors 0을 기록했고 [부모/자식/손자 후속 부재 관측](../../dist/acceptance/RUN-20261007-owned-job-tree-cancel/post-cleanup-processes.json)을 보존했다.

[계약 수정 후 실제 검증](../../dist/acceptance/RUN-20261007-owned-job-tree-cancel-2/results.json)은 자체 root/child/grandchild의 PID/create_time과 각각의 loopback ephemeral listen port를 기록하고 tree 관측과 일치시켰다. process.wait **100 ms timeout** 뒤 정확한 root가 살아 있음을 확인했다. 실제 Job cancel 뒤 **CANCELLED**, 세 process.inspect는 **PROCESS_NOT_FOUND**, own 세 포트 connect_ex는 모두 실패였다. 늦은 cancel은 같은 terminal Job 기록을 반환했고 own 폴더 삭제·cleanup errors 0이었다. T-PROC-01, T-JOB-01 및 T-EXEC-03의 cancel 부분에 대한 owner API 증거이며 자동 shell timeout·Agent crash·actual MCP를 완료로 승격하지 않는다.

### 19.8 실제 121 Browser 및 핵심 연결 차단

[121 native Browser 인수](../../dist/acceptance/RUN-20261007-remote-browser-native/results.json)는 **PASS**였다. pinned runtime capability 보고뿐 아니라 실제 browser.open이 isolated_ephemeral/sandbox=true로 성공했다. 자체 headless about:blank의 DOM만 구성해 한글·emoji type/click 뒤 `Hello 한글 😀 RACP`를 snapshot 및 [회수 PNG](../../dist/acceptance/RUN-20261007-remote-browser-native/owned-browser.png)에서 확인했다. **1280×720** PNG의 SHA-256은 Gateway Artifact metadata와 일치했고 오래된 observation ID 입력은 STALE_OBSERVATION으로 거부됐다. 외부 사이트·사용자 Browser·OS foreground를 사용하지 않았다.

browser.close는 **CLOSED / cleanup_status=complete / owned_browser_terminated**, 반복 close는 동일 결과였다. close 후 snapshot은 HANDLE_EXPIRED, owner handle 조회도 CLOSED였다. 이는 설치 Agent v0.1.10 owner API의 T-BROW-01 및 T-BROW-02 일부 증거이고 새 source 후보·실제 Codex MCP·upload/download·CDP·전체 lifecycle/soak를 대신하지 않는다. §14/17/18의 과거 A Browser cleanup 실패를 제품 수정으로 해결한 증거로도 이관하지 않는다.

현재 핵심 완료를 막는 조건은 직접 RACP MCP의 OAuth 미설정, 임시 인증 시험 구성의 미승인, live B debugger endpoint 미준비 및 사용자가 보류한 새 후보 빌드·배포다. 같은 owner API 증거를 반복해 실제 Codex/OAuth 인수로 승격하지 않는다. 구현·부분 인수 결과는 보존하고 해당 연결·환경 준비 후 남은 경로를 재개한다.

---

<a id="remote-analysis-scope-correction"></a>

## 20. 사용자 요청 범위 정정 — OAuth 구성은 원격 분석의 필수 조건이 아님

2026-10-08 사용자는 OAuth 구현을 요청한 적이 없다고 정정했다. 요청은 **RACP를 활용해 원격 정적·동적 분석이 가능한지 기존 Codex 리버싱 MCP와 함께 시험**하는 것이며, Gateway MCP를 Codex에 직접 등록하는 특정 연결 방식이나 OAuth 제공자 신설을 필수로 요구하지 않았다. 직접 MCP/OAuth와 live attach를 핵심 선행 조건으로 확대한 agent의 해석을 철회한다. §19.8의 OAuth·선택 live 경로에 의한 전체 목표 차단 판정도 이 정정으로 대체한다.

기존 RACP owner API는 이미 보유한 owner 인증으로 원격 작업·파일 회수가 가능하다. 실제 Codex가 그 API를 호출해 B의 자체 PE·메모리·dump를 회수하고 기존 IDA/WinDbg MCP로 분석한 §15/18의 증거는 요청한 정적 분석 및 원격 동적 실행·dump 분석의 PASS다. dump 분석이 B live attach를 입증하지 않는다는 기술적 한계는 유지하되, 별도로 요구되지 않은 live debugger를 완료 조건으로 추가하지 않는다.

Gateway `/mcp/`의 HTTP 503은 **직접 Gateway MCP 등록이라는 선택 경로**에서 현재 코드가 외부 OAuth를 요구하기 때문이다. [10월 8일 관측](../../dist/acceptance/resume-20261008-auth-check.json)도 같은 응답과 121 ONLINE을 확인했으며 기존 owner API 경로의 기능 장애를 뜻하지 않는다. 임시 Keycloak 구성 승인 요청은 철회한다. OAuth 제공자 설치·신규 OAuth 구현·인증 우회는 수행하지 않았다.

이 정정은 Windows 배포 검증 전체의 완료 선언이 아니다. 사용자 지시에 따른 제품 빌드 보류와 새 후보·display/session·부하/soak 등의 미검증 범위는 유지한다. 병행 변경 후 현재 SSOT는 v0.1.17이며 [전체 Ruff](../../dist/acceptance/lint-20261008-resume.log) exit 0, [mypy](../../dist/acceptance/mypy-20261008-resume.log)는 161 source files에서 오류 0이었다. 앞선 v0.1.16/B 설치 v0.1.10 결과를 새 배포 후보 인수로 자동 이관하지 않는다.

---

<a id="gateway-web-management-0117"></a>

## 21. v0.1.17 Gateway 웹 관리 G00~G12 구현·인수

2026-10-08에는 [Gateway 웹 관리 서버 작업 계획서](../spec/gateway-web-management-plan.md)의 구현을 기존 dirty worktree 위에서 이어서 검증했다. 버전 SSOT는 `uv run python scripts/version.py show`로 **0.1.17**을 확인했고 추가 PATCH 증가는 하지 않았다. G00~G06의 기존 검증 29건은 이전 실행 증거로 유지하고 재작성하지 않았으며, 실제 저장소와 지정 회귀에서 차이가 확인된 G07 및 G11~G12만 보강했다.

### 21.1 이번 배치 구현 범위

- **G07**: 관리 status/settings에 revision header와 설정 `ETag`/`If-Match` 경합 검사를 추가했다. SQLite `busy`를 일반 failure와 구분하고 busy 상태에서 후속 DB 조회로 status endpoint 자체가 실패하지 않도록 했다. source SSOT와 설치 package version 차이도 warning으로 검출한다.
- **G11**: `gateway_management_acceptance.py`가 credential 원문 대신 보호된 file reference를 사용하고 candidate SHA-256, topology SHA-256, 시작/종료 clock, elapsed time, host, cleanup receipt를 증거에 포함한다. 격리 fixture에서 **50 synthetic Agent + 10 독립 Console session/SSE slot**을 생성해 장비 ID 격리, 49 ONLINE/1 OFFLINE, revoke가 다른 장비에 영향을 주지 않음, slot 전부 회수를 검증했다.
- **G12**: token/cookie/private key/원문 DB를 포함하지 않는 bounded support bundle을 management API와 Console Logs 화면에 연결했다. bundle 생성은 `logs.export` 권한을 사용하고 다운로드는 strict bundle ID, 인증 session, `Cache-Control: no-store`를 적용한다. 550개 진단 log fixture에서도 최대 500개·5 MiB 기본 경계와 partial archive 미잔존을 검증했다.
- 계획서에 명시됐지만 없던 `apps/console/tests/management.spec.ts`를 추가해 Overview/Settings/Logs/Backups/Users/Updates가 동일 session을 사용하고 support bundle mutation이 CSRF header를 포함하는지 격리 Chromium에서 검증했다.
- OpenAPI와 `apps/console/src/generated.ts`를 현재 management schema에서 다시 생성했다. `docs/protocol/`의 기존 경로는 변경하지 않았다.

### 21.2 자동화·정적 검증

| 검증 | 실제 결과 | 판정 |
|---|---|---|
| G07~G12 기존 핵심 지정 suite | status/settings, backup/restore, maintenance, update manifest/updater, packaging, management E2E, support bundle, console drift **24 passed** | **PASS** |
| G07/G11/G12 보강 회귀 | 현재 source에서 status/ETag/DB busy + management E2E + support bundle **10 passed**; 이후 G07 status 단독 **4 passed** | **PASS** |
| version/packaging/updater/OpenAPI 지정 gate | **13 passed** | **PASS** |
| management Playwright | pinned Node/Chromium, `management.spec.ts` **1 passed (15.9s)** | **PASS** |
| Ruff | `uv run ruff check apps packages scripts tests` | **PASS** |
| Mypy | `uv run mypy` → **161 source files, issues 0** | **PASS** |
| OpenAPI/generated client | contract 재생성 후 `test_console_drift.py` **1 passed** | **PASS** |
| Console production build | pinned Node 22.23.0, Vite **167 modules**, JS 392.78 kB / gzip 117.37 kB | **PASS** |
| 문서 상대 링크 | 변경한 Gateway/OAuth/PC/release-gate 문서의 local relative link 존재 검사 오류 0 | **PASS** |
| 전체 `pytest -q` | 현재 source 기준 장시간 실행 중이며 이 섹션 최종 갱신 전에는 PASS로 간주하지 않음 | **RUNNING** |

### 21.3 Setup·Portable·업데이트 배포 증거

`scripts/build_gateway.py --platform win --arch x64 --targets setup,portable --node <pinned-node> --dry-run`은 0.1.17/Windows x64/Python 3.12.11 계약으로 통과했다. 그러나 현재 호스트에는 `makensis`가 없어 `setup_engine_available=false`이며 실제 NSIS Setup EXE 생성, clean-PC SCM install/reboot/uninstall은 **BLOCKED_ENV**다. Setup이 없는데 Portable 결과를 native service 인수 PASS로 대체하지 않는다.

기존에 완성되어 있던 `dist/gateway/0.1.17/win-x64/RACP-Gateway-0.1.17-win-x64.zip`은 덮어쓰지 않고 고정 후보로 smoke했다. SHA-256은 `4f8a44267998b486baee694cc73714856dc1b3601a242e2a2532c6e6ef6a747b`이며, **4,354 file hashes**, ZIP CRC, fresh PowerShell launch, Console asset, TLS readiness, 1회용 enrollment/Console login, status script, loopback MCP **86 tools**가 통과했다. 이 ZIP은 이번 G07/G11/G12 최종 source 변경 전에 생성된 후보이므로 **현재 source 재패키징 증거로 승격하지 않는다**. 같은 0.1.17 출력의 안전한 덮어쓰기 방지 guard가 정상 동작해 현재 source로 기존 완성 산출물을 교체하지 않았다.

### 21.4 GT01~GT29 판정

| 시험군 | 현재 판정 | 이번 기준의 근거/제한 |
|---|---|---|
| GT01~GT02 | **PASS** | config/path와 migration 보존 회귀. G00~G01 기존 검증 증거 유지 |
| GT03 | **PARTIAL / BLOCKED_ENV** | SCM lifecycle/service-secret 자동화는 통과했으나 관리자 clean host의 3회 start/stop·reboot는 이번 환경에서 미실행 |
| GT04 | **PASS (automated)** | local bootstrap/1회성 credential 권한 회귀; live 제품 service를 fixture로 사용하지 않음 |
| GT05 | **PARTIAL** | TLS/origin validation 자동화는 통과; 운영 CA/browser trust 실증은 별도 환경 gate |
| GT06~GT07 | **PASS (synthetic)** | enrollment/multi-Agent identity·disconnect/reconnect/revoke 격리 자동화. 물리 50대로 해석하지 않음 |
| GT08~GT10 | **PASS (automated)** | RBAC/OIDC/session/Agent local-capability 상한 회귀; 실제 운영 IdP는 별도 gate |
| GT11~GT12 | **PASS (automated)** | redaction/bounded log/search 범위와 기존 G06 검증 증거. 실제 대용량 운영 export는 별도 운용 범위 |
| GT13~GT14 | **PASS** | SSE replay gap→full refresh, browser 관리 화면 회귀, revision + ETag conflict, secret echo 차단 |
| GT15~GT17 | **PASS (isolated)** | maintenance, backup, corrupt/incomplete restore, identity/operation 보존 시험 |
| GT18~GT21 | **PASS (isolated)** | 서명/manifest/hash/platform 검증, updater apply/receipt/rollback core 시험 |
| GT22 | **BLOCKED_ENV** | `makensis` 부재로 실제 Setup EXE 및 clean-PC/reboot 설치 수명주기 미실행 |
| GT23 | **PARTIAL / BLOCKED_ENV** | installer contract는 state 보존/제품 범위 제거를 정의; 실제 native uninstall은 Setup 부재로 미실행 |
| GT24 | **PASS (고정 Portable 후보)** | 기존 0.1.17 ZIP 독립 해제 smoke 통과. 최종 source 재패키징과는 구분 |
| GT25 | **PARTIAL PASS** | 50 synthetic Agent + 10 browser/SSE slot 및 SQLite busy 분류 통과. 1M audit/100 log-s 장시간 성능 목표 전체 측정은 미실행 |
| GT26 | **NOT_RUN** | 8시간 soak/RSS·handle·task 추세 증거 없음. 자동 PASS하지 않음 |
| GT27 | **BLOCKED_ENV / NOT_RUN** | 이번 후보로 실제 141→121 등록·작업·로그·폐기 인수를 수행하지 않음 |
| GT28 | **BLOCKED_ENV / NOT_RUN** | 외부 OAuth/Codex + 실제 121 MCP 인수 미실행. loopback 86-tool catalog를 대체 증거로 사용하지 않음 |
| GT29 | **PASS** | support bundle unit/API/Console, secret/path redaction, size/log bound, ZIP 구성 검증 |

### 21.5 GA01~GA03와 출시 판정

- **GA01 최초 설치·웹 관리 — BLOCKED_ENV**: Portable의 launch/TLS/1회용 등록·로그인은 검증했지만 native Setup EXE, SCM service, reboot, uninstall이 `makensis` 부재로 미실행이다.
- **GA02 상시 운영·다중 장비 — PARTIAL PASS**: synthetic 50 Agent + 10 browser/SSE 기능·격리는 통과했다. 실제 121 후보와 8시간 soak는 `NOT_RUN/BLOCKED_ENV`다.
- **GA03 업데이트·실패 복구 — PARTIAL PASS**: 서명 manifest, updater stage/apply/receipt/rollback, backup/restore core 자동화는 통과했다. 실제 설치 서비스 N→N+1→health 실패 rollback은 Setup 환경 부재로 미실행이다.

따라서 **구현 수준은 G00~G12의 실행 가능한 개발 범위를 충족**하지만, **Windows 운영 release acceptance는 승인하지 않는다**. 남은 필수 외부 gate는 native NSIS Setup clean-PC 수명주기, 실제 121 현재 후보, 외부 MCP가 요구되는 경우의 운영 IdP/Codex, 8시간 soak, 정식 release signing/hash metadata다. 미실행·환경 차단 항목은 PASS로 계산하지 않는다.

---

<a id="windows-oct08-test-fixes"></a>

## 22. v0.1.18 추가 시험과 실제 제품 수정

사용자의 남은 시험 진행 요청에 따라 build 없이 검사를 재개했다. 초기 등록 desktop opt-in의 UI/IPC/Agent 저장 및 Hello SSOT 수정은 이전 배치에서 수행한 제품 변경이다. x64 WNDPROC 주소 보존은 별도의 시험 fixture 수정이다. 이번 배치에서는 새로 재현한 **Browser 종료, Console terminal stream 인증 경합, Windows execution identity 조회**를 제품 코드에서 수정했다. 수정 배치의 PATCH는 **0.1.17→0.1.18** 한 번만 증가했고 `uv lock --offline`으로 lock을 갱신했다. 운영 venv를 sync하거나 제품 build/패키징을 실행하지 않았다.

### 22.1 실패 기준선과 원인 검증

[초기 전체 검사](../../dist/acceptance/python-20261008-current.xml)는 **330 passed / 20 skipped / 5 failed**, maxfail=5로 중단됐다. Browser cleanup unknown 2건, login Broker RPC timeout, Console logout stream 순서, timeout fixture의 PID 파일 미생성이었다. [5건 분리 재현](../../dist/acceptance/failures-20261008-isolated.xml)은 **3 failed / 2 passed**였으며 Browser 2건과 login Broker가 재현됐다. 원래 실패·환경 부하 증거를 보존하고 단독 통과를 전체 해결로 이관하지 않는다.

Browser의 [EOF 진단](../../dist/acceptance/browser-graceful-20261008-events.json)은 즉시 강제 종료 대신 worker 정상 종료를 먼저 유도하면 두 실패가 통과함을 관측했다. 1초 제품 시도에서는 [관련 회귀 3 failed / 2 passed](../../dist/acceptance/browser-close-green-20261008.xml)였고, 추가 grace의 [비교 5 passed](../../dist/acceptance/browser-extra-grace-20261008.xml)로 조정 근거를 확보했다. 최종 제품 구현은 **child kernel handle을 정상 종료 전에 보존**, EOF 정상 종료에 최대 **2초**, 미응답 시 기존 Job 강제 종료 및 native signal 검증이다. 전체 cleanup 한도 **5초**는 늘리지 않았고 외부 CDP scope에는 EOF 유도를 적용하지 않았다.

자체 cooperative worker가 EOF 정리를 수행해야 하는 [RED](../../dist/acceptance/browser-close-red-20261008.xml)는 **1 failed / 1 passed**, 수정 후 [Browser 관련 회귀](../../dist/acceptance/browser-close-green2-20261008.xml)는 **20 passed**, 140.86초다. 정상/비응답 worker, Browser file·timeout·CDP 보존 범위를 포함한다. 단순히 unknown을 complete로 바꾸지 않았다.

로그인 Broker의 [phase 관측](../../dist/acceptance/login-phase-20261008-2.jsonl)은 register 완료 뒤 Guardian bootstrap에 약 **3.78초**, 그 이후 RPC loop 진입을 보여 줬다. 시험은 adoption 직후를 실제 준비 완료로 가정했다. 실제 IPC/Guardian readiness를 확인한 다음 기존 5초 RPC 예산을 검사하도록 시험 전제를 수정했으며 [관련 4 passed](../../dist/acceptance/broker-login-ready-20261008.xml)였다. 제품 RPC 예산과 startup/권한 경계를 바꾼 것으로 보고하지 않는다.

### 22.2 Terminal stream 인증 경합 수정

기존 forward는 인증 후 queue.get에서 기다리는 동안 로그아웃되면 재검 없이 새 프레임을 보낼 수 있었다. stream_open 입력 대기 동안의 인증 취소도 relay dispatch 전에 재검하지 않았다. 두 await 경합을 강제로 만드는 [RED](../../dist/acceptance/terminal-auth-red-20261008.xml)는 **2 failed**였다. 제품 `terminal_socket.py`에서 stream_open 입력 이후 및 frame dequeue 이후 인증을 재검하도록 수정했다. [인증·Console·실제 terminal stream 회귀](../../dist/acceptance/terminal-auth-green2-20261008.xml)는 **14 passed**, 27.91초다. 새 OAuth 기능이나 제공자 구성을 추가하지 않았다.

### 22.3 현재 source UI·실제 121 추가 검사

[renderer source 4가지 조합](../../dist/acceptance/client-source-ui-20261008-summary.json)은 연결 파일/직접 입력 × checkbox false/true 전달과 설정 화면 값 일치를 검증했다. Vite 개발 서버와 주입된 API fixture를 사용했으며 제품 renderer build나 새 EXE를 만들지 않았다. [초기 화면](../../dist/acceptance/RUN-20261008-client-source-ui/initial-source.png)·[설정 화면](../../dist/acceptance/RUN-20261008-client-source-ui/settings-source.png)을 보존한다. 첫 helper가 없는 input[type=url]을 찾은 실패는 보존하고 실제 label로 수동 경로만 재검했다. 이는 renderer 시험이며 Electron/Agent 경로의 검사를 대신하지 않는다.

[실제 121 ConPTY](../../dist/acceptance/RUN-20261008-terminal-boundaries/results.json)는 Unicode REPL 출력, resize, 동일-key write 1회, 독립 cursor, read timeout 뒤 동일 session 유지, close complete·반복 close·late write 거부·PID 부재를 PASS로 확인했다. [자동 shell deadline tree 검사](../../dist/acceptance/RUN-20261008-owned-job-tree-timeout/results.json)는 세 listen port가 시작 전에 연결 가능하고 deadline 후 연결 불가, root/child/grandchild PROCESS_NOT_FOUND, TIMED_OUT 및 late cancel 불변·own 폴더 정리를 확인했다. 두 시험은 설치 B Agent v0.1.10 경로다.

### 22.4 자동 검사 결과와 적용 범위

Browser/login 수정과 버전 갱신 후 [전체 Python 실행](../../dist/acceptance/python-20261008-fixed.xml)은 **419 passed / 21 skipped**, 640.47초였다. 이 실행의 collection 이후 추가한 terminal 인증 수정·신규 2개 시험은 §22.2의 14개 별도 회귀로 검증했다. 같은 source의 한 번의 전체 실행으로 합쳐 표현하지 않는다. [Ruff](../../dist/acceptance/lint-20261008-final-fix.log)와 [mypy](../../dist/acceptance/mypy-20261008-final-fix.log)는 exit 0 / 161 source files 오류 0, [version 6 passed](../../dist/acceptance/version-20261008-close-fix.xml)다. [Client Node](../../dist/acceptance/client-node-20261008.log)는 **11 passed**, [Client TypeScript](../../dist/acceptance/client-types-20261008.log)는 --noEmit exit 0이었다.

수정은 source에 존재한다. 기존 121 Client/Agent binary와 운영 Gateway를 교체·재시작하지 않았으므로 새 수정의 실제 적용과 Windows 배포 인수는 별도 확인이 필요하다. 제품 build는 사용자가 마지막에 요청할 때까지 보류한다. 현재 source를 기존 121 Python에서 별도 own fixture로 실행할 수 있는지 검토하며, 가능 여부·실행 결과를 이 절에 추가한다.

### 22.5 정제된 Windows 환경의 Agent 시작 결함과 현재 source의 실제 121 검증

121의 기존 Python과 별도 own source snapshot을 사용한 시험에서 등록 후 Agent가 ONLINE으로 진행하지 못했다. [단계·예외 관측](../../dist/acceptance/RUN-20261008-source-agent-121-startup/results.json)은 Hello의 `getpass.getuser()`가 **ModuleNotFoundError: pwd**로 실패함을 확인했다. RACP 실행 환경에서 LOGNAME/USER/LNAME/USERNAME이 빠진 Windows에서는 getpass가 POSIX pwd fallback에 도달한다. 등록은 완료됐으나 연결되지 않은 own fixture Device도 revoke하고 PID/폴더를 정리했다.

변수 부재 및 위조 환경으로 실제 Hello 생성을 검증하는 [RED](../../dist/acceptance/windows-username-red2-20261008.xml)는 **2 failed**였다. Windows 계정명을 `win32api.GetUserName()`으로 구하는 공통 `execution_identity.py`를 추가하고 Hello/Ready·등록/Client 정보·background 상태의 기존 getuser 호출을 교체했다. POSIX 경로와 등록 토큰의 getpass 비밀 입력은 유지했다. [신규 회귀와 등록/연결·인증 실행](../../dist/acceptance/windows-identity-green-20261008.xml)은 **30 passed**, 10.56초다. 같은 v0.1.18 수정 배치에 포함했으며 재시도마다 버전을 올리지 않았다.

[수정 후 실제 121 source Agent](../../dist/acceptance/RUN-20261008-source-agent-121-identity-fixed/results.json)는 등록·ONLINE·Hello v0.1.18·native Browser close complete가 PASS였다. 추가 [파일 upload/close 시험](../../dist/acceptance/RUN-20261008-source-agent-121-upload/results.json)에서도 **v0.1.18 Hello**, 자체 **1024 bytes** Artifact upload SHA-256 일치, Browser close **complete**를 확인했다. 해당 두 own Device는 revoke, 정확한 parent-managed PID/create_time/boot로 종료 후 PROCESS_NOT_FOUND, own 폴더 revision 삭제·cleanup errors 0이었다.

현재 pure Python source를 별도 fixture 폴더에 전달하고 **121의 기존 CPython/의존성/browser runtime을 재사용**했다. 기존 Client/Agent 설치를 교체하거나 EXE/wheel/배포 패키지를 build하지 않았다. source runtime 검증을 새로운 설치 패키지·외부 Python 없는 clean 설치 검증으로 이관하지 않는다. source launcher의 초기 enroll 인자 오류는 별도 helper 실패로 보존했으며 제품 결함으로 집계하지 않았다. 1회용 등록 값은 보고서·명령행에 남기지 않았고 임시 credential과 파일은 정리했다.

추가 코드 후 [전체 Ruff](../../dist/acceptance/lint-20261008-final-source.log) exit 0, [mypy](../../dist/acceptance/mypy-20261008-final-source.log)는 **162 source files 오류 0**이었다. 이전 전체와 영향 회귀의 [중복 제외 집계](../../dist/acceptance/python-20261008-verification-union.json)는 **421 passed / 21 skipped**이며 서로 다른 코드 시점의 실행이라는 적용 범위를 명시한다. 세 제품 수정을 모두 포함한 최종 source 전체 검사는 별도로 진행 중이며 아래에 실제 종료 결과를 추가한다.

### 22.6 세 제품 수정 후 동일 source의 전체 결과

[최종 v0.1.18 전체 Python JUnit](../../dist/acceptance/python-20261008-final.xml)은 **423 passed / 21 skipped / failure·error 0**, **570.38초**였다. Browser 종료, stream await 이후 인증 재검, Windows native execution identity의 세 제품 수정을 모두 포함한 동일 source 시점의 실행이다. 이전 실패·진단/부분 회귀 수치와 중복 합산하지 않는다.

default skip는 GUI opt-in 15, 실제 POSIX runner 1, 명시 GDB 경로 2, Ghidra/JDK 경로 2, 선택 Docker Keycloak 1이다. OAuth provider 시험을 사용자 요청의 필수 조건으로 추가하지 않는다. Windows native GUI의 후속 opt-in 결과는 §22.7에 기록한다. source runtime의 실제 121 검증과 binary/package 설치 인수는 구분하며 제품 build 보류는 유지한다.

### 22.7 Windows GUI fixture의 결과 게시 경합 수정

전체 검사 뒤 [첫 native GUI opt-in](../../dist/acceptance/native-gui-20261008-final.xml)은 **4 passed / 10 skipped / 1 error**였다. fixture가 결과 JSON을 게시할 때 관측자의 Windows 파일 읽기 handle과 atomic replace가 경합해 **WinError 5**로 종료됐다. 이후 setup의 NoSuchProcess는 이 fixture 종료의 결과다. 이를 제품 desktop 입력 결함으로 집계하지 않는다.

실제 파일 reader를 잠시 유지하는 [게시 회귀 RED](../../dist/acceptance/gui-publish-red2-20261008.xml)는 **1 failed**였다. `scripts/desktop_acceptance_fixture.py`에서 Windows sharing/access denied **5/32/33**에만 최대 **0.5초** 재시도를 적용했다. 다른 오류와 지속 실패는 그대로 보고하며 최종 closed 결과를 생략하지 않는다. [수정 후 native GUI opt-in](../../dist/acceptance/native-gui-20261008-publish-fixed.xml)은 **6 passed / 10 skipped / failure·error 0**, **60.77초**, exit 0이다. skip 10건은 OS가 자체 창의 foreground activation을 거부한 조건이며 입력 성공으로 계산하지 않는다.

§22.6의 전체 source 검사는 세 제품 수정을 모두 포함했다. 그 이후 추가한 것은 이 fixture 게시 회귀 1개와 fixture 수정이며 전체 423개 통과 수치에 GUI 결과를 중복 합산하지 않는다. 새 opt-in 회귀는 기본 실행에서는 추가 skip 대상이다. [최종 Ruff](../../dist/acceptance/lint-20261008-fixture-final.log) exit 0, [mypy](../../dist/acceptance/mypy-20261008-fixture-final.log) **162 source files 오류 0**, 변경 fixture 두 파일의 format check 및 version show **0.1.18**도 통과했다.

### 22.8 현재 완료 범위와 남은 실제 환경 인수

초기 등록 checkbox의 source UI 네 조합, 세 제품 결함의 재현·수정·전체 회귀, 실제 121에서 현재 v0.1.18 source Agent 등록·연결·Browser upload/close를 확인했다. RACP 원격 실행·자료 회수와 기존 Codex IDA/WinDbg MCP의 정적·동적 dump 분석 PASS는 §15/18/20에 기록돼 있다. 직접 Gateway MCP/OAuth나 B live attach를 새 필수 조건으로 추가하지 않는다.

Windows 전체 인수는 완료로 선언하지 않는다. 이번 실행에 남은 환경 범위는 foreground 승인이 필요한 GUI 항목, display 배율·다중 monitor·locked/RDP·실제 logoff/reboot 조합, 새 package의 clean 설치·upgrade/uninstall·rollback, 계획의 30분 혼합 부하·8시간 soak다. 기존 B 설치는 v0.1.10이며 현재 source fixture의 성공을 새 package 인수로 바꾸지 않는다. 운영 Gateway도 이번 수정 적용을 위해 재시작하지 않았다. 사용자의 build 보류 지시를 유지하며 제품 EXE/wheel/package는 생성하지 않았다.

---

<a id="gateway-management-final-0118"></a>

## 23. v0.1.18 Gateway G11/G12·Portable 최종 검증

2026-10-08 후속 사용자 요청은 §22의 일반 제품 build 보류 이후 **Gateway 웹 관리 계획의 남은 G11/G12와 Windows Gateway Portable 검증을 명시적으로 계속하도록 승인**했다. 시작 시 인계에는 0.1.17이 보고됐으나 작업 도중 병행 배치가 SSOT와 workspace/lock을 **0.1.18**로 함께 올린 것을 `uv run python scripts/version.py show`와 버전 파일로 재확인했다. 이 작업에서는 추가 version bump를 실행하지 않았고, 이미 동기화된 0.1.18을 현재 기준으로 사용했다. 기존 dirty worktree와 관련 없는 변경은 reset/clean하지 않았다.

### 23.1 G11/G12 구현과 관리 경로

- `scripts/gateway_management_fixture.py`를 추가해 새 loopback state/credential reference만 소유하는 1~100 synthetic device acceptance fixture를 만들었다. 50 device fixture는 원문 session cookie/CSRF를 topology/evidence에 넣지 않으며 비-loopback host를 거절한다.
- `gateway_management_acceptance.py`는 candidate package hash, topology hash, 시작/종료 UTC, elapsed time, host와 read-only cleanup 의미를 남기고 expected version/최소 device 수를 검증한다. 실제 실행은 50-device 격리 Gateway에서 `multi-agent`, `upgrade`, `restore`, `soak` 네 시나리오 모두 exit 0이었다. soak 실행은 **5초 bounded smoke**이며 8시간 soak를 대체하지 않는다.
- support bundle은 관리 API `POST /api/v1/management/support-bundles`와 인증 download에 연결했고 Console Logs에서 생성/다운로드할 수 있다. strict bundle ID, `logs.export`, CSRF/same-origin mutation, `Cache-Control: no-store`, 500-log 상한, 기본 5 MiB, partial archive 제거를 검사했다.
- 관리 OpenAPI와 generated TypeScript client를 현재 FastAPI schema에서 재생성했다. `docs/protocol/console-openapi-v1.json`의 **경로는 그대로 유지**했다.

### 23.2 현재 source 품질·Console·acceptance 결과

| 검증 | 실제 결과 | 판정 |
|---|---|---|
| G00~G12 계획 지정 Python 묶음 | config/service/setup/multi-agent/RBAC/OIDC/log/status/backup/maintenance/update/packaging/G11/G12/contract 포함 **122 passed**, 309.28초 | **PASS** |
| G11/G12 보강 | acceptance fixture/management API/support bundle targeted **4 passed**, 별도 보강 묶음 **13 passed** | **PASS** |
| Ruff | `uv run ruff check packages/ apps/ scripts/ tests/` | **PASS** |
| Mypy | `uv run mypy` → **162 source files, issues 0** | **PASS** |
| version | 현재 SSOT/workspace **0.1.18**, `tests/unit/test_version.py` 포함 지정 gate 통과 | **PASS** |
| OpenAPI/generated client | `scripts/console_contract.py` 재생성 후 `test_console_drift.py` 통과 | **PASS** |
| Console production build | pinned Node 22.23.0, Prettier/TypeScript/client drift/Vite; 167 modules, JS 392.78 kB / gzip 117.37 kB | **PASS** |
| Management Chromium | 고유 preview `127.0.0.1:4197`에서 `management.spec.ts` **1 passed / 3.6s** | **PASS** |
| Acceptance CLI evidence | [multi-agent](../../dist/acceptance/R18A-multi/result.json), [upgrade](../../dist/acceptance/R18A-upgrade/result.json), [restore](../../dist/acceptance/R18A-restore/result.json), [5초 soak](../../dist/acceptance/R18A-soak/result.json) exit 0; raw credential evidence scan PASS | **PASS** |
| Acceptance fixture cleanup | own PID/server 종료, temp state·credential 삭제, 18877 listener 잔존 false; [receipt](../../dist/acceptance/R18A-cleanup/cleanup.json) | **PASS** |
| 전체 `pytest -q` | 최종 source 실행 중; 종료 전 PASS로 승격하지 않음 | **NOT_RUN** |

### 23.3 Gateway Portable·Setup·업데이트 인수

`scripts/build_gateway.py --platform win --arch x64 --targets setup,portable --node .tools/node-v22.23.0-win-x64/node.exe --dry-run`은 version 0.1.18, Windows x64, deployment schema 1, publish=false를 확인했다. `setup_engine_available=false`였고 `Get-Command makensis`도 결과가 없어 **Native NSIS Setup EXE는 BLOCKED_ENV**다. installer contract/unit 회귀는 지정 122-test 묶음 안에서 PASS지만 실제 Setup install/reboot/upgrade/uninstall smoke로 확대하지 않는다.

Portable-only 실빌드는 현재 source에서 성공했다. [build manifest](../../dist/gateway/0.1.18/win-x64/build-manifest.json)의 후보는 `RACP-Gateway-0.1.18-win-x64.zip`, **34,365,297 bytes**, SHA-256 `434474193d9333f68b5937eb897bffb69896bd6ae2b45ecf820757fe563e7c33`이다. 내장 native Python import/version도 0.1.18로 일치했다.

첫 smoke는 긴 evidence root 아래 pywin32 license 경로에서 Windows `WinError 206`으로 **FAIL (시험 경로 길이)**했다. 기존 실패 폴더를 삭제/재사용하지 않고 짧은 새 root `dist/acceptance/R18P`로 같은 ZIP을 재실행했다. [최종 smoke](../../dist/acceptance/R18P/result.json)는 **4,354 file hashes, ZIP CRC, fresh PowerShell launch, Console/assets, TLS readiness, one-use enrollment, one-use Console login, status PowerShell, loopback MCP 86 tools**를 PASS했다. `external_oauth_login`과 `agent_execution`은 이 Portable smoke에서 `not_run`이며 다른 증거로 자동 승격하지 않는다.

### 23.4 GT01~GT29 현재 판정

| 시험군 | 판정 | 현재 근거와 남은 범위 |
|---|---|---|
| GT01~GT02 | **PASS** | config/path/migration 지정 회귀 포함 |
| GT03 | **BLOCKED_ENV** | service 자동화는 PASS지만 clean host 실제 3회 start/stop·reboot는 미실행 |
| GT04 | **PASS** | local bootstrap/one-use 경계 자동화 |
| GT05 | **PASS** | TLS/origin/cert 오류 자동화; 운영 CA trust는 release 환경 별도 확인 |
| GT06~GT07 | **PASS** | enrollment·multi-device identity/disconnect/revoke 및 G11 50-device fixture. 물리 50대로 보고하지 않음 |
| GT08~GT10 | **PASS** | RBAC/OIDC/session 및 Agent local capability 상한 자동화 |
| GT11~GT14 | **PASS** | log redaction/filter, dashboard/SSE, logout, revision/ETag/secret echo 및 Chromium 관리 화면 |
| GT15~GT17 | **PASS** | maintenance/backup/restore isolated 회귀. 실제 1 GiB RTO/RPO 측정은 별도 성능 gate |
| GT18~GT21 | **PASS** | signed manifest/hash/platform, updater receipt/idempotency/rollback core 자동화 |
| GT22 | **BLOCKED_ENV** | `makensis` 부재로 Setup EXE·clean install/reboot/cancel 실제 수명주기 미실행 |
| GT23 | **BLOCKED_ENV** | uninstall 보존 계약은 PASS, 실제 Setup uninstall/data-delete 인수는 미실행 |
| GT24 | **PASS** | 현재 source v0.1.18 Portable build + 독립 해제 smoke PASS |
| GT25 | **NOT_RUN** | 50 synthetic device + 10 Console/SSE 기능 격리는 PASS지만 10 browser 실제 동시 부하, 1만 metadata/100만 audit, 100 log/s·30분과 p95 목표 전체는 미측정 |
| GT26 | **NOT_RUN** | 5초 bounded soak만 수행. 계획의 8시간 RSS/handle/task/SSE 증가율 측정 없음 |
| GT27 | **NOT_RUN** | §22의 121 source Agent 결과는 있으나 **현재 v0.1.18 Gateway Portable 후보와 묶은** 141→121 웹 인수는 미실행 |
| GT28 | **BLOCKED_ENV** | 이번 후보의 actual Codex→Gateway MCP→121 실행 환경 미구성. loopback 86-tool catalog를 대체 PASS로 사용하지 않음 |
| GT29 | **PASS** | support bundle unit/API/Console, secret/path 비노출, log/size bound, ZIP 구성 검증 |

### 23.5 GA01~GA03와 출시 수준 판정

- **GA01 — BLOCKED_ENV**: 현재 Portable 후보의 launch/TLS/등록/login/Console 관리 subset은 PASS. NSIS Setup EXE, 실제 SCM clean install/reboot/uninstall이 `makensis` 부재로 미실행이다.
- **GA02 — NOT_RUN**: 50 synthetic device + 10 Console/SSE slot 격리와 acceptance CLI는 PASS했지만 실제 121 후보를 포함한 8시간 soak/로그 export/장기 resource trend의 완전 시나리오는 수행하지 않았다.
- **GA03 — BLOCKED_ENV**: update trust/updater/backup/restore/rollback core 자동화는 PASS. 실제 설치 서비스 N→N+1, drain/stop, health-failure rollback, uninstall까지 묶은 시나리오는 Setup 환경 부재로 미실행이다.

따라서 **G00~G12의 실행 가능한 구현·자동화 범위와 G12 support workflow는 완료**했지만, §13의 **웹 관리 인수 완료 / Windows 배포 완료 / 운영 release 가능** 수준은 선언하지 않는다. 현재 release 제한은 Native NSIS Setup clean-PC 수명주기, GT25 전체 부하, GT26 8시간 soak, 현재 Gateway package와 실제 121을 묶은 GA01~GA03, 실제 release signing/update feed metadata다. macOS/Linux 빌드는 실행하지 않았다.
---

<a id="host-mcp-agent-only-analysis"></a>

## 24. v0.1.19 Client에 분석 도구 사전 설치를 요구하지 않는 MCP 경로와 Agent 수정

사용자는 분석 도구가 없는 일반 Client PC에서 **RACP가 수집·제어 역할을 하고 Codex PC의 기존 MCP를 사용**하는 것이 목표라고 재확인했다. Agent의 결함은 수정하고 OS/tool/runtime 조건과 기술적 불가를 구분한다. PATCH는 이번 수정 배치에서 **0.1.18→0.1.19 한 번** 증가했고 `uv lock --offline`을 갱신했다. 제품 build·패키징, 분석 IDE/driver의 Client 설치, 새 OAuth 제공자 및 자체 MCP adapter 추가는 수행하지 않았다.

### 24.1 Windows native 프로그램의 시스템 경로 오류 수정

원격 shell의 공용 데이터 폴더 API가 `SHGetFolderPath(CSIDL_COMMON_APPDATA)` **0x80070003**으로 실패했다. 같은 환경에 OS의 SystemDrive를 복원하면 **C:\ProgramData**로 정상 해석됐다. A에서는 제거된 변수가 literal `%SystemDrive%\ProgramData`로 반환되는 [실제 API RED](../../dist/acceptance/windows-env-red-20261008.xml) **3 failed**, 이어서 parent에도 변수가 없는 [복구 RED](../../dist/acceptance/windows-env-red-recovery-20261008.xml) **1 failed / 3 passed**를 확보했다.

Agent `execution_env`가 Windows의 SYSTEMDRIVE/ALLUSERSPROFILE만 보존하고 caller의 해당 변수 변경은 거부하도록 수정했다. parent에도 SystemDrive가 없으면 `GetWindowsDirectory`의 실제 drive에서 복구한다. credential 관련 환경을 추가로 상속하지 않는다. [영향 회귀](../../dist/acceptance/windows-env-green-recovery-20261008.xml)는 **29 passed**였다.

[현재 source의 실제 121 검증](../../dist/acceptance/RUN-20261008-source-v019-process-close-2/results.json)은 v0.1.19 Hello·native 공용 폴더 조회·process tree close·Browser close가 PASS였다. 기존 121 CPython/browser를 재사용한 별도 source fixture이며 새 설치 package 인수를 대신하지 않는다. own Device revoke·PID 부재·폴더 삭제와 cleanup errors 0을 확인했다.

### 24.2 Pktmon 실패를 우회하는 Agent 내장 IPv4 수집 코드

기본 Windows Pktmon은 자체 TLS 연결의 **19개** counters를 관측했지만 [명시 provider 진단](../../dist/acceptance/RUN-20261008-remote-packet-tls-provider-diagnostic/results.json)에서도 ETL에는 metadata 3개, 변환 PCAP에는 **0 packet / 156 bytes**만 있었다. all components·raw flags·필수 OS 환경 비교를 보존했으며, 이를 Agent 방식의 기술적 불가로 판정하지 않는다. memory logging 비교도 native 변환 오류로 실패했다. 부분 파일 전달만 성공한 기록은 packet 인수 PASS가 아니다.

Windows Winsock API를 사용한 [원격 feasibility 시험](../../dist/acceptance/RUN-20261008-remote-raw-ip-packet/results.json)은 **19 packets / 5540 bytes**를 회수했고 기존 Wireshark MCP가 raw IP·TCP·TLS를 확인했다. 이 근거로 Agent source에 `packet_capture.py`를 추가했다. [신규 회귀 RED](../../dist/acceptance/packet-capture-red-20261008.xml)는 module 부재로 collection error였다. packet filter·완전한 PCAP record·empty 거부·byte limit·socket 정리의 회귀를 구현했다.

최종 helper는 **RCVALL_IPLEVEL(3)**을 사용해 NIC promiscuous mode를 켜지 않고 선택 interface의 IPv4 TCP/UDP 흐름만 저장한다. exact local IP/peer/local port, 최대 **30초 / 16 MiB**, exclusive output 생성, deadline/byte limit, 항상 raw socket 해제, empty capture failure를 적용했다. byte limit 결과는 `complete=false`다. IPv6/Ethernet/ARP·non-initial fragments·TLS 평문은 이 helper의 제공 범위가 아니다. [Microsoft의 IP-level API 조건](https://learn.microsoft.com/en-us/windows/win32/winsock/sio-rcvall)을 기준으로 administrator token을 실제 확인했다.

[실제 121에서 최종 Agent 코드](../../dist/acceptance/RUN-20261008-remote-agent-packet-iplevel-2/results.json)를 hash 확인 후 실행해 **19 packets / 5541 bytes**, SHA-256 `098f617f3673f392dd900aeeee5096e54fdb327f823acf0e0de4268223cbd0d9`를 회수했다. [실제 기존 Codex Wireshark MCP](../../dist/acceptance/RUN-20261008-remote-agent-packet-iplevel-2/native-mcp-analysis.json)는 full scan **19/19**, 송신 **10 / 수신 9**, Device IP·전용 port·두 방향 group을 일치시켰다. empty capture 거부·raw socket 해제·own 폴더 삭제도 PASS다. 이전 [TLS handshake 조회](../../dist/acceptance/RUN-20261008-remote-agent-packet-code/native-mcp-analysis.json)와 최종 IP-level 범위를 구분한다.

이는 Agent 내장 CLI를 **기존 승인된 shell operation**으로 실행하는 경로다. 전용 `network.capture` RPC/MCP tool을 구현한 것으로 보고하지 않는다. Client에 Wireshark·mitmproxy·Npcap 또는 분석 IDE를 설치하지 않았고, 기존 Npcap driver의 존재도 이 helper의 사용 조건으로 삼지 않았다.

### 24.3 실제 host Ghidra·mitmproxy MCP 분석

[Ghidra native MCP](../../dist/acceptance/ghidra-native-remote-20261008.json)는 RACP로 회수한 동일 SHA-256의 PE에서 `racp_transform` RVA 0x1000, **param*7 xor 0x5a**, IMUL/XOR/RET와 entry 0x140001049의 caller를 확인했다. A의 별도 own project와 원본 GUI plugin을 사용했고 해당 helper만 종료했다. 최초 GUI 미기동·숫자 문자열 인자·GUI launcher 오류는 host/tool/helper 문제이며 Agent 결함이나 원격 분석 불가로 분류하지 않는다. 사용자 DB를 대체하거나 upstream server를 수정하지 않았다.

[실제 121 Browser의 HTTP HAR](../../dist/acceptance/RUN-20261008-remote-har-mcp/results.json)는 bundled browser가 own loopback HTTP 요청과 **result=112**를 기록했다. **2301 bytes / SHA-256 5c7e247a37a34efb59f3e09af479571b5edf924f6c09c38ef4b18905ecba8c56**가 회수 원본과 일치했다. [기존 mitmproxy MCP](../../dist/acceptance/RUN-20261008-remote-har-mcp/native-mcp-analysis.json)는 append import **1 / error 0**, 동일 marker GET·HTTP 200·JSON result 112를 실제 inspect했다. MCP의 working-directory path guard를 준수했으며 기존 traffic DB를 clear하지 않았다. Browser/server 및 own 원격 폴더는 정리했다.

HAR import/조회는 live proxy interception·원격 replay를 입증하지 않는다. legacy B CPython의 직접 TLS keylog 설정은 OpenSSL_Applink 오류로 실패했으며 runtime 수정/재빌드는 하지 않았다. raw packet 수집 또는 HAR 분석의 성공을 임의 앱 TLS 복호화 성공으로 확대하지 않는다.

### 24.4 추가 전체 회귀에서 확인한 process tree 종료 경합 수정

[첫 v0.1.19 전체](../../dist/acceptance/python-20261008-v019.xml)는 **428 passed / 22 skipped / 2 failed**, 992.83초였다. OpenAPI 실패는 SSOT version 0.1.19와 저장된 document 0.1.18의 불일치였으며 `scripts/console_contract.py`로 기존 경로를 갱신했다.

process.terminate는 spawn gate 종료만 기다린 뒤 실제 child가 아직 종료 중인데 complete로 반환할 수 있었다. gate를 먼저 종료하고 native child 종료를 지연하는 [결정적 RED](../../dist/acceptance/process-close-red-20261008.xml)는 **1 failed**였다. Agent `ProcessProvider.close`가 기존 **5초 총 cleanup 예산 안에서 Job ActiveProcesses=0을 먼저 확인**한 뒤 handle을 닫고 gate.wait를 완료하도록 수정했다. timeout에는 complete를 선언하지 않는다. [filesystem/process/lifecycle/OpenAPI/backup 영향 회귀](../../dist/acceptance/process-close-green-20261008.xml)는 **17 passed**였고, §24.1의 실제 121 current source에서도 종료 후 PROCESS_NOT_FOUND를 확인했다.

현재 [신규 Agent·version 회귀](../../dist/acceptance/v019-new-agent-final.xml)는 **15 passed**이며 [mypy](../../dist/acceptance/mypy-20261008-v019-final2.log)는 **165 source files 오류 0**이다. 병행 Gateway backup/restore 변경에서 확인한 변수 타입 shadowing과 formatting만 보완했다. 세부 기능을 바꾼 것으로 집계하지 않는다.

중간 [전체 재검](../../dist/acceptance/python-20261008-v019-final.xml)은 **374 passed / 22 skipped / 3 failed**, maxfail=3이었다. Python socket에 RCVALL_IPLEVEL의 symbolic name이 없는 버전을 처리하도록 수정했지만, 이미 collection에서 import한 runner는 이전 module을 유지해 세 packet unit에서 AttributeError가 발생했다. Winsock의 문서화된 값 **3**과 symbolic name fallback을 사용한 fresh [15개 지정 회귀](../../dist/acceptance/v019-new-agent-final.xml), 실제 121 IP-level 수집을 확인하고 새 process에서 전체를 다시 실행했다.

**최종 current-source 전체** [JUnit](../../dist/acceptance/python-20261008-v019-final3.xml)·[로그](../../dist/acceptance/python-20261008-v019-final3.log)는 **449 passed / 22 skipped / failure·error 0**, **474.49초**, exit 0이다. 결과를 이전 전체/부분 회귀와 합산하지 않는다. [Ruff](../../dist/acceptance/lint-20261008-v019-verified.log) pass, [mypy](../../dist/acceptance/mypy-20261008-v019-verified.log) **165 source files 오류 0**, pinned Node 22.23.0의 [Client Node](../../dist/acceptance/client-node-20261008-v019.log) **11 passed**, [Client](../../dist/acceptance/client-types-20261008-v019.log)·[Console](../../dist/acceptance/console-types-20261008-v019.log) TypeScript --noEmit exit 0도 확인했다. 제품 build는 실행하지 않았다.

### 24.5 MCP 경로 판정

| 경로 | 현재 판정 |
|---|---|
| RACP 파일 회수→IDA/Ghidra 정적 분석 | **PASS (실제 native MCP의 지정 함수·bytes·xref)** |
| RACP 원격 실행·메모리/dump 회수→WinDbg | **PASS (지정 실행 상태와 dump 분석)** |
| Agent native IPv4 수집→RACP 회수→Wireshark | **PASS (지정 흐름·19 packets·hash·full count)** |
| Remote Browser HAR→RACP 회수→mitmproxy | **PASS (HAR import·지정 요청/응답 조회)** |
| 모든 native MCP tool의 모든 command | **NOT_RUN**. catalog 노출이나 위 지정 경로로 승격하지 않음 |
| WinDbg live attach/break/step·원격 proxy/replay | **미구현 경로/추가 인수 필요**. Client 사전 IDE 설치 부재를 기술적 불가의 근거로 쓰지 않음 |
| key 없는 TLS 평문·OS 보호/권한 밖 상태 | 수집/전달만으로 해당 경계를 없앨 수 없음. 지원 조건과 기술적 한계를 개별 명시 |

---

<a id="agent-permissions-design"></a>

## 25. Client 세부 권한·OS 기능 확대와 향후 중앙 정책의 설계

2026-10-08 사용자 요청과 첨부 UI 예시를 기준으로 [Agent 세부 권한 및 원격 OS 기능 아키텍처](../spec/agent-permissions-and-capabilities.md)를 **Draft**로 작성했다. **18개 카테고리 / 중복 없는 143개 permission ID 후보**에 항목·대상/예산 제약·기존 primitive/CLI/후속 상태를 연결했다. 최초 집계 140개는 ID 정규식이 숫자를 제외하여 `network.capture.ipv4`, `network.capture.ipv6`, `network.capture.layer2`를 누락한 결과였다. 원래 표의 후보를 다시 집계해 정정했으며, 143개 기능이나 세부 permission enforcement의 완료 선언이 아니다.

설계는 Client의 등록/설정 공통 UI, local settings v2·migration·revision, Agent/Broker의 authoritative 검사, 출력 전달 전 재검, 권한 축소 시 소유 자원 정리, capability/availability/OS token의 분리, shell/interpreter 우회와 실제 시행 수준을 정의한다. 미래 managed policy는 로컬 관리 상한과 합성하는 별도 snapshot 공급자로 연결하며 **Server→Client 정책 배포·원격 설정 덮어쓰기·Enterprise worker는 이번에 구현하지 않았다**.

AI가 선택한 원격 Device에서 일관된 file/process/terminal/UI/network/memory 작업을 하고, 출처·boot/epoch·revision·hash를 가진 자료를 기존 host MCP로 분석하도록 설계했다. A-local fallback과 Client OS 격리를 가장하지 않는다. 실시간 native debugger/proxy transport는 후속 A5 과제로 구분한다. 현재 기능의 세부 권한 검사·공통 UI를 A0–A3에서 먼저 구현하고, 실제 backend가 없는 tool을 stub으로 노출하지 않는 순서를 제안한다.

---

<a id="gateway-management-final-0119"></a>

## 26. v0.1.19 Gateway 웹 관리 최종 구현·인수 판정

2026-10-08 `docs/spec/gateway-web-management-plan.md`의 G00~G12를 당시 SSOT **0.1.19** 기준으로 재대조했다. G00~G06의 기존 검증을 보존하고, G07~G09에서 실제로 남아 있던 settings commit/ETag, stale ONLINE, 외부 MCP 미설정 상태, 관리 user/group/log-export, restore/update management plane, disk-low backup/restore, signed update compatibility 필드를 보완했다. G11/G12는 acceptance evidence, support bundle API/Console, 운영 문서를 후보에 연결했다. 2026-10-09 후속 인수에서는 cached/pinned NSIS compiler를 찾아 native Setup/SCM을 실제 실행하고, exact v0.1.19 Gateway Portable에 실제 121의 별도 source Agent v0.1.19를 연결해 Web Console까지 검증했다. 이어서 runtime/Python/Node가 없는 격리 Windows 11 VM에서 clean install을 실제 수행해 native3 Setup의 bootstrap JSON 결함을 발견·보존하고, 수정된 installer를 다시 컴파일한 최종 Setup으로 clean install→실제 guest reboot→post-reboot readiness→uninstall/state 보존→명시적 data 삭제까지 검증했다. 외부 OAuth/Codex와 8시간 soak는 실제 환경/시간 증거 없이 승격하지 않는다. 이후 병행 작업이 repository SSOT를 v0.1.20으로 올렸으므로 이 절의 후속 검증은 보존된 v0.1.19 snapshot/artifact에만 한정하며 v0.1.20 변경을 되돌리지 않는다.

### 26.1 구현·정적 품질 게이트

| 항목 | v0.1.19 현재 결과 | 판정 |
|---|---|---|
| G00~G06 | 기존 config/migration/service/setup/multi-Agent/RBAC/OIDC/log 회귀를 보존하고 최종 전체 회귀에서 다시 포함 | **PASS** |
| G07 | `PUT /management/settings`, revision/ETag 409, secret 비노출, DB busy, stale ONLINE 제외, source/package mismatch, 외부 origin의 `MCP not_configured` | **PASS** |
| G08 | maintenance/online backup/restore staging, disk-low fail-fast, corrupt backup 거절, restore 시 session/credential 무효화·inflight `UNKNOWN`·pre-restore snapshot/handoff | **PASS** |
| G09 | Ed25519 manifest, HTTPS origin, hash/size/expiry/downgrade, `protocol_major`, schema min/max, minimum updater version, public-key fingerprint `key_id`, receipt recovery/rollback | **PASS** |
| G10 | Windows x64 Setup/Portable 계약, pinned NSIS, installer state 보존/service identity, current-host SCM + runtime-free clean Windows install/reboot/uninstall/data-delete, Portable build/smoke | **PASS** |
| G11 | 50 synthetic Agent + 10 management session/SSE isolation, quantitative load, exact final-native3 multi-agent/upgrade/restore/short-soak, 실제 121 Web Console/작업/log/cleanup | **PASS** |
| G12 | safe support bundle API/download/Console, bounded logs/size, raw DB/key/cookie/token/output 제외, 운영 가이드와 release 판정 | **PASS** |
| Ruff | `uv run ruff check apps packages scripts tests` | **PASS** |
| Mypy | `uv run mypy` → **165 source files, issues 0** | **PASS** |
| version | SSOT/workspace **0.1.19**, version unit gate 6 passed | **PASS** |
| OpenAPI/generated client | 기존 `docs/protocol/console-openapi-v1.json` 경로 유지, drift test 1 passed | **PASS** |
| Console production | pinned Node 22.23.0, Prettier/TypeScript/client drift/Vite 167 modules | **PASS** |
| Browser | 격리 Gateway+Agent `console.spec.ts` **14 passed**; management/update mocked browser **2 passed** | **PASS** |
| v0.1.19 전체 pytest 기준선 | `uv run pytest -q` → **462 passed, 22 skipped in 501.86s**, exit 0; native Setup 후속 수정 전의 최종 전체 실행 | **PASS** |
| 최종 Setup/SCM delta | 보존된 v0.1.19 snapshot에서 기존 version/OpenAPI 포함 핵심 **22 passed**를 보존하고, 최신 packaging/service/updater **15 passed**, 관련 Python Ruff PASS + 실제 current-host/clean-VM Setup 인수 | **PASS** |

GT12의 인증 export 경로는 [현재 API 점검](../../dist/acceptance/RUN-20261008-1935-gateway-management-0.1.19/log-export-check.json)에서 비동기 생성→상태→인증 다운로드가 `SUCCEEDED`, 1 row, SHA-256 일치, `Cache-Control: no-store`로 확인됐다.

### 26.2 GT25 부하와 성능·신뢰성 목표

첫 1만 device/100만 audit 실행은 audit p95 **2.035463초**로 1초 목표를 초과해 FAIL로 보존했다. 원인은 source가 고정돼도 UNION 전체를 정렬하고 audit 정렬 인덱스가 없던 것이었다. 기존 migration v1 checksum을 바꾸지 않고 **Gateway schema v2 additive index migration**과 source-specific log query를 추가한 뒤 [재측정](../../dist/acceptance/RUN-20261008-1935-gateway-management-0.1.19/load-v2/result.json)은 device metadata p95 **0.004891초**, 100만 audit 검색 p95 **0.000618초**, SQLite writer lock **5.591초 감지 후 release 뒤 write 복구**로 통과했다. 초기 실패 evidence는 삭제하지 않았다.

| §11 목표 | 현재 판정 | 근거 / 제한 |
|---|---|---|
| 50 synthetic Agent | **PASS** | identity/epoch 격리와 한 장비 offline/revoke 시 다른 장비 유지 |
| 10 browser session/SSE slot | **PASS** | 기능 기준으로 ConsoleAuth 10 session + EventFeed 10 slot 격리/회수. 10개 Chromium 동시 렌더 부하로 확대하지 않음 |
| 1만 device / 100만 audit page 50 warm p95 ≤ 1초 | **PASS** | 위 `load-v2` 정량 결과 |
| SQLite single-writer busy/recovery | **PASS** | lock 감지와 release 후 write 복구 |
| 운영 log 100건/s × 30분 | **NOT_RUN** | bounded queue/drop 회귀는 PASS지만 30분 wall-clock throughput/RSS 측정은 실행하지 않음 |
| 10개 장비 병렬 read | **NOT_RUN** | 기능 격리 회귀는 있으나 별도 정량 latency run 없음 |
| 서비스 start/readiness ≤ 60초 | **PASS** | final-native3 Setup의 실제 SCM 3회 stop/start에서 start→`/healthz` **5.804~6.078초** |
| 1GiB DB + 1GiB Artifact backup/restore RTO 15분 | **NOT_RUN** | 실제 restore와 integrity는 PASS, 해당 데이터 크기/시간 목표는 미측정 |
| 8시간 soak / RSS·handle·task·SSE 추세 | **NOT_RUN** | 15초 functional soak는 PASS이나 GT26로 승격하지 않음 |

### 26.3 G10/G11 후보·evidence와 정리

초기에는 `makensis`가 PATH에 없어 Setup을 차단했지만, 후속 확인에서 electron-builder cache의 NSIS **v3.04**를 발견했다. compiler archive SHA-256 `9877df902530f96357d13a7a31ae2b9df67f48b11ffc9a1700a7c961574ec5fa`, `makensis.exe` SHA-256 `f2b2b7726ac0d4e720dff52bfca11a5518d550fc75ed34a48dc47921527293f0`를 deployment pin으로 고정했고 build는 NSIS `/WX`를 사용한다. 실제 compile/설치 과정에서 `$COMMONAPPDATA`, 32-bit registry view, SCM host/quoting, uvicorn service logging/signal 문제가 드러났으며 실패를 보존한 뒤 all-users ProgramData, `SetRegView 64`, `pythonservice.exe` + `PythonClass`/`ConfigFile`, service-safe uvicorn으로 수정했다. 이후 진짜 clean Windows VM에서 native3 Setup을 실행하자 새 `gateway.json`의 Windows `state_root`가 JSON backslash escape를 깨뜨려 service가 `START_PENDING` 뒤 실패하는 추가 RED가 드러났다. current-host에서는 기존 유효 config 때문에 이 bootstrap 경로가 가려져 있었다. 최종 installer는 bootstrap `state_root`를 config-relative `".."`로 기록하고 fresh-install abort/failure cleanup과 cancel hook을 갖도록 수정했다.

현재 v0.1.19 최종 선택은 [candidate manifest](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-candidate.json)에 고정한 **Portable + Setup pair**다. Portable은 실제 121 인수와 정확히 같은 [`dist/gw-v019-final-native3/RACP-Gateway-0.1.19-win-x64.zip`](../../dist/gw-v019-final-native3/RACP-Gateway-0.1.19-win-x64.zip) **34,592,955 bytes / SHA-256 `5e9920f082ac9397117ee01544c344e84ca8ea8b76cea6cebb1064d7efd4d4f7`**를 유지한다. Setup은 최신 frozen v0.1.19 installer source SHA-256 `144d12c782cab89a360d111f0fbcac25a725c3447bb76b663abe4ab7edf372c9`를 pinned NSIS `/WX`로 다시 컴파일한 [`dist/gw-v019-final-setup-20261009-1715/RACP-Gateway-0.1.19-win-x64-setup.exe`](../../dist/gw-v019-final-setup-20261009-1715/RACP-Gateway-0.1.19-win-x64-setup.exe) **24,449,655 bytes / SHA-256 `099145b5c6f2125c9447fa10dd3349225ac067fce842474378e030c2482922b8`**다. 이 Setup의 source bundle에서 만든 native5 ZIP과 선택 Portable은 [4,392 ZIP entry 전부 byte-identical](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/native3-native5-payload-diff.json)하므로 installer-only 수정이 실제 121에서 검증한 runtime payload를 바꾸지 않았다. [Portable smoke](../../dist/acceptance/RUN-20261008-2230-gateway-management-0.1.19/portable-smoke-native3.json)는 manifest **4,391 files**, CRC/fresh PowerShell/Console/TLS/one-use enrollment/login/status/loopback MCP 86 tools를 통과했다.

운영자가 두 선택 파일을 한 위치에서 확인할 수 있도록 비게시 통합 복사본 `dist/gw-v019-final-selected`도 만들었고, [candidate manifest](../../dist/gw-v019-final-selected/candidate-manifest.json)와 `SHA256SUMS.txt`가 위 두 exact hash를 다시 고정한다. 원본 artifact와 역사 evidence는 삭제하거나 덮어쓰지 않았다.

Native Setup의 current-host [SCM/install 3-cycle](../../dist/acceptance/RUN-20261008-2230-gateway-management-0.1.19/setup-native-final.json)과 [updater demand-start](../../dist/acceptance/RUN-20261008-2230-gateway-management-0.1.19/updater-demand-native3.json)은 기존 서비스 수명주기 근거로 보존한다. 새 runtime-free Windows 11 Enterprise Evaluation VM에서는 native3 Setup clean-install 실패와 root cause를 [별도 evidence](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/cleanvm-root-cause.json)로 보존한 뒤, 최종 Setup `099145…922b8`의 [clean install](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-setup-clean-install.json), [실제 guest reboot와 post-reboot readiness](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-setup-reboot.json), [uninstall/state preservation](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-setup-uninstall.json), [backup 확인 후 명시적 ProgramData 삭제](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-explicit-data-delete.json)를 모두 PASS했다. clean install 전 guest에는 RACP/Python/Node가 없고 default route도 0이었다. 실제 installer Cancel 버튼은 로그인된 guest desktop과 Desktop 제어 채널이 없어 수행하지 못했으므로 [cancel gate](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-install-cancel-gate.json)는 `BLOCKED_ENV`를 유지한다; 강제 process 종료나 static hook을 실제 cancel로 대신하지 않는다. [clean-VM cleanup receipt](../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/cleanup-receipt.json)는 VM을 원래 clean checkpoint·Off·NIC disconnected로 복원하고 run-owned checkpoint/vmconnect를 제거했으며 새 evidence의 credential 문자열 scan이 모두 0임을 기록한다.

선택 Portable `5e9920…d4f7`와 같은 runtime에 묶은 bounded G11은 [multi-agent](../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/multi-agent/result.json), [restore](../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/restore/result.json), [upgrade](../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/upgrade/result.json), [15초 short soak](../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/short-soak/result.json)을 모두 exit 0으로 완료했다. 이 네 역사적 결과에는 당시 native3 Setup `4295a2…a3ca4`도 candidate metadata로 기록돼 있으므로 새 Setup 증거로 재사용하지 않는다. runtime/Portable 쪽은 선택 ZIP과 exact hash가 같고 [cleanup receipt](../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/cleanup-receipt.json)의 credential leak 0/process/listener/private cleanup PASS를 유지한다. 15초 soak를 GT26의 8시간 soak로 사용하지 않는다.

실제 121에서는 기존 8765 Agent 연결을 변경하지 않고 별도 18819 Gateway와 별도 enrollment를 만들었으며, 실제 실행 runtime은 현재도 선택된 Portable ZIP `5e9920…d4f7`과 exact match다. [stage evidence](../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-stage.json)는 당시 pair의 ZIP/Setup hash와 보존된 source Agent v0.1.19 archive SHA-256 `46907edfe6df2135420d8ac6f924f4a4e386b94933380541bd28358986d1483a`를 기록한다. 이후 Setup만 교체됐으므로 이 물리 증거를 새 Setup 인수로 사용하지 않고, 새 Setup은 위 clean-VM evidence로 별도 판정한다. [원격 operation 인수](../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-operations.json)는 file/hash/artifact, shell idempotency, owned job cancel, persistent terminal을 PASS했고, [Web Console 인수](../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-console.json)는 login→ONLINE→standard approval→실제 job→device-filtered management logs **100 rows**, browser page error 0을 PASS했다. [remote cleanup](../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-remote-cleanup.json)과 [cleanup receipt](../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-cleanup-receipt.json)에서 device REVOKED, source process/folder 제거, Gateway/listener/firewall/private state 제거와 owner secret leak 0을 확인한다.

[외부 gate 기록](../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/external-gates.json)에서 Codex CLI는 존재하지만 `codex mcp get racp`가 `No MCP server named 'racp' found`를 반환했고, acceptance JSON의 duration 기록 중 8시간 이상은 **0건**이다. 따라서 GT28은 `BLOCKED_ENV`, GT26은 `NOT_RUN`을 유지한다.

### 26.4 GT01~GT29 최종 판정

| 시험 | 판정 | v0.1.19 근거 / 남은 조건 |
|---|---|---|
| GT01 설정·경로 | **PASS** | 공백/경로/listener/TLS config 회귀, state 보존 |
| GT02 migration | **PASS** | checksum/future/backup + schema v2 additive index migration |
| GT03 서비스 | **BLOCKED_ENV** | current-host SCM 3회 start/stop·health + clean VM 실제 reboot 후 자동 start/readiness는 PASS. **실제 interactive logout**만 미실행 |
| GT04 로컬 bootstrap | **PASS** | SID/TTL/one-use/local admin 회귀 |
| GT05 TLS/외부 주소 | **PASS** | SAN/expiry/origin/host 검증과 packaged TLS smoke |
| GT06 enrollment | **PASS** | one-use/동시·multi-device identity 회귀 및 packaged smoke |
| GT07 장비 관측 | **PASS** | 20/50 synthetic, stale ONLINE 제외, disconnect/revoke isolation |
| GT08 RBAC | **PASS** | role/device/output/operation grant server-side 거절 |
| GT09 OIDC/세션 | **PASS** | issuer/state/nonce/session/auth revision 자동화. 실제 운영 IdP는 release gate 별도 |
| GT10 Agent 로컬 권한 | **PASS** | Gateway grant가 local capability 상한을 확대하지 않는 회귀 |
| GT11 audit·log | **PASS** | token/cookie/key/HTML/ESC/64KiB redaction, bounded queue/drop |
| GT12 log filter/export | **PASS** | cursor/scope/search + authenticated bounded export 실제 API 점검 |
| GT13 dashboard freshness | **PASS** | stale cutoff, SSE gap refresh, logout cache clear, slot release |
| GT14 settings 경합 | **PASS** | revision/ETag, stale commit 409, secret echo 거절 |
| GT15 maintenance | **PASS** | admission close, active/CLOSED handle, idempotent job/drain defer |
| GT16 backup | **PASS** | online SQLite backup, corruption/integrity, disk-low fail-fast |
| GT17 restore | **PASS** | staged restore, old credential 무효화, inflight `UNKNOWN`, health rollback helper |
| GT18 update trust | **PASS** | signature/key-id/expiry/platform/protocol/schema/updater-version/size/hash/redirect 검증 |
| GT19 update 경합 | **PASS** | maintenance idempotency, active work DEFERRED, duplicate apply job 재사용 |
| GT20 update 장애 | **PASS** | isolated persisted `staged/stopped/switched` receipt recovery + health failure/FAILED. 실제 전원 차단은 Windows deployment gate 별도 |
| GT21 rollback | **PASS** | isolated schema compatible binary rollback + incompatible pre-update backup restore |
| GT22 설치 | **BLOCKED_ENV** | 최종 Setup의 runtime-free clean Windows install + 실제 reboot + post-reboot readiness는 PASS. **실제 UI 중간 cancel**만 interactive guest desktop 부재로 미실행 |
| GT23 제거 | **PASS** | 최종 Setup uninstall이 service/binary/registry를 제거하고 ProgramData를 보존함. disposable clean VM에서 guest-local backup hash를 확인한 뒤 정확한 `%ProgramData%\RACP\Gateway`만 명시 삭제해 scope까지 확인 |
| GT24 Portable | **PASS** | exact v0.1.19 final ZIP 독립 build/smoke |
| GT25 부하·SQLite busy | **PASS** | bounded 50 Agent, 10 session/SSE, 10k/1M p95, writer contention/recovery. 30분 log throughput은 §26.2 NOT_RUN |
| GT26 8시간 soak | **NOT_RUN** | 15초 functional soak만 수행 |
| GT27 실제 A→B 웹 | **PASS** | 최종 선택 Portable `5e9920…d4f7` + 실제 121의 별도 source Agent v0.1.19: 등록/ONLINE, file/artifact/shell/job/terminal, packaged Web Console login/approval/job/log, revoke/cleanup 확인 |
| GT28 실제 MCP | **BLOCKED_ENV** | Codex CLI에 `racp` MCP server 자체가 미등록이고 external IdP/callback/TLS OAuth가 미구성. loopback 86-tool catalog는 대체 증거 아님 |
| GT29 지원 bundle | **PASS** | safe ZIP allowlist, log/size bounds, raw DB/private key/token/cookie/user secret path/output 제외 |

### 26.5 GA01~GA03와 §13 출시 수준

- **GA01 — PASS**: 최종 Setup `099145…922b8`을 runtime/Python/Node 없는 clean Windows 11 VM에 설치해 service identity/64-bit registry/config/readiness를 확인하고 **실제 guest reboot 후 자동 기동과 `/healthz` 지속성**까지 PASS했다. 최초 Web Console·실제 121 end-to-end는 최종 선택 Portable `5e9920…d4f7`에서 이미 PASS했고, Setup source bundle과 이 Portable의 4,392 entry runtime payload가 byte-identical임을 별도 확인했다. installer cancel은 GT22 Windows deployment 하위 gate로 독립 유지한다.
- **GA02 — NOT_RUN**: 50 synthetic + 10 session/SSE + log export + backup + actual 121 + cleanup은 PASS지만 **8시간 soak**가 없으므로 전체 시나리오를 PASS로 선언하지 않는다.
- **GA03 — BLOCKED_ENV**: signed update/DEFERRED/idempotency/backup/receipt/rollback/restore core와 실제 updater service demand-start는 PASS지만, 실제 installed service release N→N+1 apply와 health-failure rollback을 한 묶음으로 수행하지 않았다.

| §13 수준 | 판정 | 이유 |
|---|---|---|
| 구현 완료 | **PASS** | G00~G12 코드·계약·격리 fixture·Console/support workflow 구현 및 실행 가능한 회귀 통과 |
| 웹 관리 인수 완료 | **NOT_RUN** | GA01은 PASS로 닫혔지만 GA02의 실제 8시간 soak가 미완료 |
| Windows 배포 완료 | **BLOCKED_ENV** | clean install/reboot/uninstall/data-delete는 PASS했으나 실제 interactive logout, 중간 install cancel, installed-service GA03가 미완료 |
| 운영 release 가능 | **BLOCKED_ENV** | 실제 운영 TLS/IdP/Codex MCP, release signing/update feed, 8시간 soak, install cancel/logout 및 위 미통과 인수 해소 필요 |

공개 artifact 업로드는 요청되지 않았으므로 수행하지 않았다. macOS/Linux build도 실행하지 않았다. current-host SCM, clean Windows install/reboot/uninstall/data-delete와 physical 121은 실제 증거로 갱신했다. 남은 실제 interactive logout/install cancel, installed-service N→N+1, 8시간 soak, actual OAuth/Codex는 각각 독립 gate로 유지한다.


---

<a id="agent-permissions-v020"></a>

## 27. v0.1.20 Client 세부 권한 구현 진행

[구현 계획](../spec/agent-permissions-implementation-plan.md)에 따라 로컬 권한 모델과 등록/편집 경로를 구현하고 있다. PATCH를 **0.1.19→0.1.20 한 번만** 증가하고 `uv lock --offline`으로 workspace 버전을 동기화했다. 제품 build·패키징·설치·배포는 실행하지 않았다. 기존 Gateway 작업의 v0.1.19 인수 결과를 새 패키지 인수로 바꾸지 않는다.

### 27.1 현재 구현

- Python/Client가 같은 JSON catalog를 읽는다. 18개 category/143개 leaf 중 **64개는 기존 RPC에 매핑**, 1개는 내장 capture CLI, 78개는 후속 항목이다. CLI/후속 권한의 allow는 현재 strict settings가 거절하며 UI도 비활성화한다. 이는 전체 OS 기능 구현 수가 아니다.
- immutable compiled snapshot의 revision, category deny 우선, 누락/unknown ID 거절, 작업 폴더·요청 시간·출력/Artifact 크기·최초 executable allow/deny ceiling을 구현했다. `strict_os_isolation=true`는 지원한다고 가장하지 않고 거절한다.
- 보호 설정 v2와 v1 read migration, 초기 등록 두 경로 및 revision/backup/lock을 이용한 설정 편집에 permissions를 연결했다. credential/Device identity를 보존하며 이전 대상 **64개 ID를 고정**해 미래 RPC 추가로 이전 권한이 확대되지 않게 했다. Agent가 실행 중이면 기존처럼 편집을 차단한다.
- Agent Provider 호출 직전/await 이후/결과 전달과 Artifact retry에서 local ceiling을 확인한다. process cmdline은 별도 leaf가 없으면 cache를 변경하지 않고 가린다. Artifact upload는 매 chunk/complete 직전 검사하고, terminal stream은 접수·loop·실제 writer dequeue 시 검사한다.
- 등록/설정에 공통 category/leaf UI와 제약 편집을 연결했다. category off는 선택을 보존하고 실행만 차단한다. 미구현과 profile/OS 조건, 광역 실행과 OS sandbox의 차이를 표시한다. [소스 UI 화면](../../dist/acceptance/agent-permissions-source-ui.png)은 mock IPC로 렌더링한 화면이며 설치된 Client 인수가 아니다.
- Hello에 local permission revision/grants/constraints와 실제 시행 수준을 별도 capability metadata로 선언했다. 로컬 설정을 Gateway가 변경하는 API는 추가하지 않았다.

### 27.2 현재 시험 증거

| 시험 | 확인한 결과 | 증거와 한계 |
|---|---|---|
| catalog payload selector | window screenshot 권한 분류 오류 RED 1 failed/13 passed → GREEN 14 passed | [RED](../../dist/acceptance/permissions-catalog-red-window-20261008.xml), [GREEN](../../dist/acceptance/permissions-catalog-green3-20261008.xml) |
| 모델·설정 초기 통합 | **72 passed** | [초기 핵심 통합](../../dist/acceptance/permissions-core-20261008.xml); 후속 writer/transfer 검사 추가 이전 결과 |
| 실제 Gateway→Agent RPC | 로컬 write off, ask 우회, process cmdline, binary export, await 중 변경 **5 passed** | [통합 GREEN](../../dist/acceptance/local-permission-execution-green-20261008.xml); 격리 loopback fixture, 실제 121 증거와 구분 |
| current v0.1.20 단위·버전·schema | **44 passed**, 4.29초 | [현재 핵심 시험](../../dist/acceptance/permissions-v020-core-current.xml); unknown/future schema·64 ID migration freeze·16KiB 설정 경계 포함 |
| 실제 전송 직전 검사 | writer 대기 중 변경 RED → GREEN; 첫 upload chunk 뒤 변경으로 다음 bytes/complete 차단 | [writer RED](../../dist/acceptance/permission-writer-red-20261008.xml), [transfer RED](../../dist/acceptance/permission-transfer-red-20261008.xml), [GREEN + version](../../dist/acceptance/permissions-output-version-green-20261008.xml) |
| Client source browser | 연결 파일/직접 등록/설정 편집 3경로·category off/복원·동일 payload·실패 보존 **PASS** | [결과](../../dist/acceptance/agent-permissions-source-ui.json); Vite dev server+headless Chromium, production build 없음 |
| Client Node·타입 | Node **11 passed**, TypeScript `--noEmit` exit 0 | source-only; backend bound/login/controller/packaging 계약 검사, 제품 packaging 실행 아님 |
| Ruff·mypy | Ruff PASS, mypy **168 source files / issues 0** | [lint](../../dist/acceptance/permissions-v020-lint.log), [mypy](../../dist/acceptance/permissions-v020-mypy.log); Gateway service의 기존 nullcontext assignment 타입 ignore만 보완 |
| 최초 전체 회귀 | **FAIL: 495 passed / 22 skipped / 7 failed**, 927.14초 | [최초 결과](../../dist/acceptance/permissions-v020-regression.xml); 원본 실패를 보존하고 아래 §27.4에서 수정·재검을 구분 |
| 현재 전체 회귀 재실행 | **PASS: 529 passed / 22 skipped**, 650.60초, exit 0 | [현재 결과](../../dist/acceptance/permissions-v020-regression2-20261009.xml), [현재 로그](../../dist/acceptance/permissions-v020-regression2-20261009.log); source-only, skip을 native 실제 인수로 계산하지 않음 |
| 실제 121 source 재검 | **PASS (권한·OS 관측 범위)** | 121 구동 후 기존 v0.1.10 Client의 runtime으로 v0.1.20 임시 source Agent를 실행했다. §27.5에 실제 grant/deny·재시작·정리 증거를 기록. live debugger/proxy 및 새 binary 인수는 별도 미완료 |

시험 중 CPython 3.12.11 `platform._wmi_query`에서 Windows `0x8007000e` 진단이 일부 출력됐다. 해당 단위/schema 실행은 결과 XML 및 exit 0으로 끝났으며, 이 진단을 제품 기능 실패나 원격 불가로 바꾸지 않았다. 전체 회귀 결과는 별도로 확인한다.

### 27.3 계속 진행할 항목

로컬 `require_approval`은 현재 **LOCAL_APPROVAL_UNAVAILABLE로 차단**하며 owner trusted profile 또는 caller payload로 우회할 수 없다. 이를 사용 가능한 승인 UI로 표시하지 않는다. 실행 중 settings 변경/소유 Handle 폐기, Broker의 세부 revision 결합, category별 실제 availability 및 전체 회귀·actual 121 검증을 추가해야 Task 3을 완료할 수 있다.

Task 4의 profile/availability 세부 표시와 광역 실행·새 leaf·제약 편집의 추가 browser 검증, Task 5의 typed network/system/memory/clipboard/storage/config Provider, Task 6의 native MCP live debugger/proxy duplex 경로는 계속 진행한다. 이 문서의 core PASS를 전체 143개 기능이나 모든 MCP 명령의 완료로 확대하지 않는다. Server→Client Enterprise 정책 배포는 계속 제외한다.

### 27.4 2026-10-09 코드 검토 수정 및 OS 관측 확장

7개 readonly RPC `system.info`, `system.resources`, `system.locale`, `system.environment`, `network.interfaces`, `network.connections`, `storage.volumes`를 strict input model·Registry·policy·Provider·capability에 연결했다. arbitrary shell 없이 OS 계정 범위의 정보를 관측하며 Device/boot/시각을 반환한다. 환경 변수는 명시한 안전한 key만 조회하고 route/DNS 쿼리는 지원한다고 광고하지 않는다. 현재 catalog는 **70개 RPC-backed leaf / 1개 CLI / 72개 후속 = 143개**다. 위 §27.1의 64개는 이 확장 전 수치다. v1 이전은 고정 64개 baseline만 보존해 새 leaf를 자동 허용하지 않는다.

독립 코드 검토에서 다음 실제 결함을 확인하고 수정했다.

- **재전송 권한 검사 누락:** 재시작·idempotent replay·reconcile·writer queue에서 저장된 결과/Artifact descriptor를 현재 ceiling으로 재검한다. cmdline은 현재 leaf에 따라 가린다. 완료된 실행 사실과 Journal 상태는 바꾸지 않고 전달 거절을 별도 error로 나타낸다. MCP도 명시한 전달 error를 실패로 표시한다.
- **재시작 후 실제 workspace 소실:** 호출자가 보낸 default context와 실제 terminal scope를 혼동하지 않도록 Agent가 생성한 principal/Device/boot/handle/workspace/root fingerprint를 SQLite에 별도 보관한다. 변경된 workspace root, 타 소유자, 검증된 binding이 없는 이전 버전의 미전송 결과는 차단한다. 원래 요청·idempotency digest를 변경하거나 작업을 재실행하지 않는다.
- **검사 기록 무한 누적:** Journal의 active task/Artifact upload pin을 유지한 뒤, 전달 가능한 outcome이 없는 workspace proof를 maintenance마다 최대 1000개씩 회수한다. 실제 SQLite 보존·만료·고아 기록 시험으로 검증했다.
- **직접 Agent 호출 기본값 누락:** validate된 payload의 기본값이 Provider에 전달되도록 수정하고 OS Provider 경계에서도 검증한다. 빈 payload를 허용하는 목록 쿼리가 UNKNOWN/KeyError로 끝나던 경로를 수정했다.
- **version 타입 혼동:** bool/float를 저장 schema version의 정수로 간주하던 경로를 차단한다. 손상된 `true`/`1.0`/`2.0` 설정이 v1 migration이나 v2 로딩으로 승인되지 않는다.

| 검증 | 결과 | 근거 / 범위 |
|---|---|---|
| replay/profile/default/MCP 초기 수정 | **17 passed** | [검토 수정 GREEN](../../dist/acceptance/review-fixes-green-20261009.xml) |
| durable scope + 실제 Gateway/Agent 통합 | **50 passed**, 24.41초 | [현재 scope 통합](../../dist/acceptance/permissions-current-scope2-20261009.xml); workspace workflow/output recovery 포함 |
| scope proof 수명주기 | RED **2 failed** → GREEN **15 passed** | [RED](../../dist/acceptance/permission-binding-retention-red-20261009.xml), [GREEN](../../dist/acceptance/permission-binding-retention-green2-20261009.xml); active/task/upload 보존 및 1001 orphan의 bounded 회수 |
| version 타입 + 설정/권한/retention | RED **5 failed** → GREEN **41 passed** | [RED](../../dist/acceptance/permission-version-types-red-20261009.xml), [GREEN](../../dist/acceptance/permissions-settings-retention-20261009.xml) |
| 최종 독립 검토 | 기존 P1/P2 및 추가 lifecycle P2 종료, 새 correctness regression 미발견 | read-only reviewer; replay/writer/transfer **12 passed**; 전체 기능/원격 인수 완료 선언 아님 |
| 현재 Ruff/mypy | **PASS**, mypy **170 source files / issues 0** | lifecycle/version 수정 이후 직접 전체 검사, exit 0 |
| 현재 Client 재검 | Node **11 passed**, TypeScript `--noEmit` exit 0, source UI **PASS** | 2026-10-09 현재 catalog로 초기 등록/설정 3경로 재실행; [UI 결과](../../dist/acceptance/agent-permissions-source-ui.json), [현재 화면](../../dist/acceptance/agent-permissions-source-ui.png); 종전 파일도 `*-before-20261009`로 보존 |

최초 전체 회귀의 7개 실패 중 stale settings schema는 계약 asset 재생성으로, workspace terminal handle은 실제 Agent scope 수정으로 해결했다. updater fixture가 현재 버전과 동일한 고정 candidate를 쓰던 문제는 현재 PATCH+1 후보로 고쳤다. 해당 focused 재검은 [workspace 2 passed](../../dist/acceptance/permissions-workspace-handle-green.xml), [OS/update/desktop 5 passed](../../dist/acceptance/permissions-v020-failure-focus2.xml)다. Browser/controller 및 Desktop의 나머지 실패는 별도 재실행에서 통과했으나 최초 일시 실패의 원인이 입증된 것은 아니다. [Browser 재검 2 passed](../../dist/acceptance/permissions-browser-failure-focus.xml)를 안정성 전체 PASS로 확대하지 않는다. 최신 전체 재실행은 529 passed / 22 skipped / 실패 0으로 종료했다. 최초 실패 로그를 보존하며 재실행 통과만으로 일시 실패의 원인까지 해결됐다고 주장하지 않는다.

> [!NOTE]
> §27.4의 시험은 소스 및 격리 loopback 검증이다. 이후 121에서 실행한 제한된 권한·OS 관측 인수는 §27.5에 구분한다. native live debugger/proxy transport와 신규 제품 binary 인수는 수행하지 않았다. 제품 build, OAuth 제공자 신설, Server→Client 정책 배포도 실행하지 않았다.

### 27.5 실제 121 — v0.1.20 소스 권한·OS 관측·재시작 검증

사용자가 121에서 기존 `RACP-Client-0.1.10-win-x64-portable.exe`를 실행했다. 기존 Device `dev_b12478e45ab646e5838c31218ab85fb1`의 ONLINE·Broker healthy를 확인했으며 Agent Hello의 버전은 `0.1.0`이었다. 이 실행 파일을 수정본으로 간주하지 않고, 기존 Python/native runtime을 사용해 별도 폴더·별도 시험 Device에서 hash 검증한 v0.1.20 source를 실행했다. 호스트 및 임시 Agent의 실제 관리자 token을 확인했다. 설치 파일을 교체하거나 제품을 빌드하지 않았다.

기존 랩 Gateway도 새 `system.resources` operation을 422 `CAPABILITY_UNAVAILABLE`로 거절했다. 실제 listener PID/생성 시각/랩 data_dir를 확인하고 SQLite online backup 후 **동일 등록 DB·TLS·주소·포트에서 현재 source Gateway로 재시작**했다. 첫 실행은 이전 interpreter가 상속받던 module path를 재현하지 못해 `ModuleNotFoundError`로 종료했고, 프로젝트 `.venv` interpreter로 수정해 복구했다. 기존 121 Agent가 epoch 9로 자동 재연결했다. [재시작 기록](../../dist/acceptance/RUN-20261009-1425-source-permissions/gateway-restart.json)은 PID와 원인을 기록하며 credential을 포함하지 않는다. 다른 앱의 같은 port listener는 변경하지 않았다.

| 실제 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 새 소스 Agent / Hello / 로컬 revision | **PASS** | [첫 성공 run](../../dist/acceptance/RUN-20261009-1435-B-source-permissions/results.json); source v0.1.20, 별도 Device, 실제 boot/epoch와 설정의 compiled revision 일치 |
| typed OS 관측 7 RPC | **PASS** | system info/resources/locale/safe environment, network interfaces/connections, storage volumes. 각 결과의 Device/boot/관측 시각을 검사. shell grant 없이 실행 |
| 파일 변경·명령 실행 차단 | **PASS** | 로컬 grant deny인 filesystem.write/shell.exec가 `layer=agent`, `PERMISSION_DENIED`로 종료하며 대상 파일은 생성되지 않음 |
| binary Artifact 반출 차단 | **PASS** | [재시작 포함 run](../../dist/acceptance/RUN-20261009-1440-B-permissions-restart/results.json); binary read leaf가 있어도 export deny이면 result/outputs 없이 Agent에서 차단 |
| 보호 설정 유지·재시작 | **PASS** | 같은 Device·저장 credential/설정으로 재시작, boot 변경·epoch 2, permission revision 유지. OS read 허용·파일 write 거부를 다시 확인 |
| native Browser 수명주기 | **PASS** | 실제 headless Browser open/close와 cleanup_status complete. Browser 분석 전 기능 또는 사용자 GUI 입력 인수로 확대하지 않음 |
| 시험 자원 정리 | **PASS** | 두 성공 run 모두 시험 Device revoke, 정확한 PID/create_time/parent boot의 process 종료·부재 확인, 자체 생성한 folder만 revision 검증 후 삭제. cleanup_errors=[] |

첫 준비 시도는 harness의 `legacy_permissions` 필수 인자 누락으로 실패했다. [실패 run](../../dist/acceptance/RUN-20261009-1430-B-source-permissions/results.json)을 보존하고 준비 코드를 수정했다. 해당 시도에는 임시 Agent를 시작하지 않았고 생성 폴더를 정리했다. 이를 제품 Agent 결함 또는 원격 기술 불가로 분류하지 않는다.

> [!IMPORTANT]
> 위 결과는 실제 121에 대한 **RACP RPC와 저장 설정·권한 검사**의 인수다. 기존 Codex native MCP의 live attach/break/step/continue/detach·proxy/replay, typed capture/dump Provider, 새 Client 설치 UI 인수까지 완료했다는 뜻이 아니다. 기존 v0.1.19의 native MCP 파일/dump/PCAP/HAR 분석 증거와 이번 source 권한 인수를 구분한다. 추가 구현과 해당 native 경로 인수는 계속 남아 있다.

### 27.6 전용 network.capture RPC와 실제 121→Wireshark MCP

v0.1.20에 `network.capture` RPC/MCP·strict IPv4/local port/peer 입력·PCAP MIME 계약을 추가했다. `network.capture.ipv4`와 `artifacts.export`를 모두 요구하며 generic argv/shell grant는 요구하지 않는다. read_only profile에서는 거절하고 standard에서는 Gateway 승인을 요구한다. 30초/16 MiB 및 현재 local output ceiling을 적용하며 임의 명령·출력 경로·BPF는 입력받지 않는다. 현재 catalog는 **71 RPC-backed leaf / 72 후속 = 143**이며 v1 migration의 64 ID baseline은 확대하지 않는다.

Agent 고정 recipe가 기존 Job Object 관리 경로를 재사용한다. 작업 프로세스와 자손을 생성 시점부터 관리하고 요청 취소·deadline·현재 grant 검사를 적용한다. stdout/stderr receipt는 각 8 KiB로 제한한다. private spool의 root를 canonicalize·pin하고, 모든 PCAP record의 길이·해시·IP/port scope를 검증한다. 검증 스레드는 취소 뒤에도 열린 파일 handle이 닫힐 때까지 기다리고, 미전송 결과는 정리한다. 완료 Artifact는 기존 OutputSpool의 durable retry에 넘긴다. 활성 worker PID는 process/memory/debugger의 보호 대상에 포함한다.

| 시험 | 결과 | 근거 / 범위 |
|---|---|---|
| capture 입력·profile·grant 계약 | RED **12 failed** → GREEN **12 passed** | [RED](../../dist/acceptance/network-capture-contract-red-20261009.xml), [GREEN](../../dist/acceptance/network-capture-contract-green-20261009.xml) |
| owned worker/cancel/timeout/revoke | **PASS** | 실제 contained fixture worker로 검증. raw NIC 수집 증거와 구분 |
| 정리 중 late cancellation | RED **1 failed** → GREEN | [RED](../../dist/acceptance/network-capture-late-cancel-red-20261009.xml), [GREEN 5 passed](../../dist/acceptance/network-capture-late-cancel-green-20261009.xml); 취소를 성공으로 반환하던 경로 수정 |
| RPC/MCP→PCAP Artifact | RED → **52 passed** | [GREEN](../../dist/acceptance/network-capture-current-green-20261009.xml); 초기 UNKNOWN 원인은 PCAP MIME을 OutputDescriptor/TransferCreate가 허용하지 않던 계약 누락. 양쪽 계약·기존 schema 경로에 같은 타입 추가 |
| 독립 검토 수정 | RED **3 failed** → GREEN cohort **23 passed** | [RED](../../dist/acceptance/network-capture-review-red-20261009.xml), [GREEN](../../dist/acceptance/network-capture-review-green2-20261009.xml); 68-byte UDP, relative spool, pinned verifier cancellation. UDP는 fixture이며 실제 NIC UDP 인수로 확대하지 않음 |
| 현재 static checks | Ruff PASS, mypy **172 source files / issues 0**, Client TypeScript `--noEmit` exit 0 | 제품 build 없음 |
| 실제 121 capture RPC | **PASS** | [현재 source run](../../dist/acceptance/RUN-20261009-B-capture-rpc-2/results.json); 설치된 v0.1.10의 기존 Python으로 별도 source Agent 실행 |
| 기존 native Wireshark MCP | **PASS** | [native MCP 원문](../../dist/acceptance/RUN-20261009-B-capture-rpc-2/native-wireshark-mcp.json); open_file + 전체 aggregate + 범위 밖 filter aggregate |
| 두 번째 전체 회귀 | **FAIL: 548 passed / 22 skipped / 2 failed**, 759.84초 | [원본 결과](../../dist/acceptance/network-capture-full2-20261009.xml); fixture PID 게시/시작 경합을 아래에 구분 |
| 캡처 변경 전체 회귀 | **PASS: 550 passed / 22 skipped**, 625.83초, exit 0 | [세 번째 실행 결과](../../dist/acceptance/network-capture-full3-20261009.xml), [로그](../../dist/acceptance/network-capture-full3-20261009.log); 아래 dump 변경 이전 기준선 |

실제 121의 시험 Device `dev_f17637a710224b65ab8f01c25f8bffaa`, boot `boot_285491b73e4e40cebbb47d69fb071087`가 별도 권한 설정으로 실행됐다. source ZIP SHA-256은 `40a578ddd470c774d01aa31e23d130bb7f851af4f561a556df85a7f7b1832e8a`다. `192.168.29.121:51655 ↔ 192.168.29.141:8765`의 자체 TLS health 요청을 수집했고, shell/argv deny 상태에서 **19 packets / 5,427 bytes / duration 2,562 ms** PCAP를 회수했다. SHA-256은 **`31777920c344ebe90a6ac2de22c90a739d1b91db81ba0aaf4e814d0843a8d892`**다. 빈 수집은 CAPABILITY_UNAVAILABLE로 거절했고 generic execution은 layer=agent에서 차단했다. Raw socket close와 worker cleanup complete를 확인하고 시험 Device revoke·traffic/source PID 부재·자체 folder 삭제를 확인했다.

native Wireshark는 같은 SHA의 **19/19 packets**, 121→141 **10**, 141→121 **9**, 범위 밖 packet **0**을 확인했다. 저장소 경로는 MCP allowlist 밖이라 최초 open이 거절됐고, 같은 해시의 파일을 기존 허용된 RE Lab `_MCP_Sessions/racp/RUN-20261009-B-capture-rpc-2/own.pcap`에 복사해 분석했다. 도구의 경로 정책은 변경하지 않았다. TLS 평문·ARP/layer2·IPv6를 제공한다고 주장하지 않는다.

첫 실제 run은 [보존된 실패 기록](../../dist/acceptance/RUN-20261009-B-capture-rpc-1/results.json)에서 패킷 수집·hash 회수 자체는 성공했지만 시험 HTTP Host header의 port 누락으로 403을 받았다. fixture를 고친 뒤 현재 run의 health 요청 200까지 확인했다. Agent capture 실패로 분류하지 않는다.

첫 전체 회귀는 standalone Broker 시험의 중복 `WindowsDesktop` probe가 pytest 본체에서 COM `CoCreateInstance`에 멈췄다. [스택](../../dist/acceptance/network-capture-full-hang-stack-20261009.txt)과 live PID를 확인했으며 실제 Broker는 pipe accept 상태였다. [종료 기록](../../dist/acceptance/network-capture-full-hang-20261009.json)에 따라 해당 시험과 소유 자식만 종료하고 원본 [로그](../../dist/acceptance/network-capture-full-20261009.log)를 보존했다. native status 조회를 실제 Broker의 authenticated/bounded pipe로 옮겨 중복 in-process COM 초기화를 없앴으며, 정상 monitor 결과·다른 session 거부·Input ABI 검사를 유지했다. focused Broker/capture **12 passed** ([결과](../../dist/acceptance/network-capture-broker-green-20261009.xml)). 새 전체 회귀는 별도 실행 중이다. Broker 자체의 optional UIA 초기화가 영구 block하지 않는다는 일반 보장은 이 시험 변경으로 입증하지 않는다.

두 번째 전체 회귀의 실패는 `test_life_01_timeout_kills_children_and_grandchildren`의 `grandchild.pid` 미생성 및 `test_auth_03_revoke_blocks_new_work_and_cleans_owned_process`의 빈 PID 문자열이었다. 전자는 2초 내 fixture의 3세대 시작을 가정했고 후자는 파일 존재를 내용 게시 완료로 간주했다. PID를 temp→atomic replace로 게시하고, 자식/손자의 실제 생존을 먼저 확인한 뒤 실제 10초 deadline의 tree cleanup과 동일 요청 replay를 검사하도록 fixture를 수정했다. [Focused 재검](../../dist/acceptance/network-capture-lifecycle-focus-20261009.xml)은 **10 passed**, 25.55초다. 제품 timeout/cleanup 구현을 완화하지 않았다. 모든 실패·hang 기록은 보존하며 세 번째 전체 회귀는 위 PASS로 완료됐다.

> [!IMPORTANT]
> 전용 캡처와 native Wireshark의 실제 원격 자료 분석 경로를 새로 검증했다. typed dump, native live debugger/proxy duplex, 로컬 승인 UI·hot permission retirement, Enterprise 정책 배포 제외 조건 및 나머지 후보 기능의 미완료 상태는 유지한다. PATCH는 0.1.20을 재사용하고 제품 build·패키징·OAuth 설정은 수행하지 않았다.

### 27.7 전용 process.dump RPC와 실제 121→WinDbg MCP

`process.dump`는 Windows x64에서 OS DbgHelp로 mini/full user-mode dump를 만든다. `memory.dump.create`와 `artifacts.export`를 함께 요구하고 generic shell/argv grant를 요구하지 않는다. read_only에서는 deny, standard에서는 Gateway 승인 대상이다. PID/create_time/Agent boot를 확인하고 Agent·부모·Broker·분석 helper·활성 수집 worker를 보호한다. 요청 deadline·현재 권한·64 MiB default/256 MiB hard ceiling 및 Client output ceiling을 적용한다. 임의 flags·명령·출력 경로는 입력받지 않는다. 현재 catalog는 **72 RPC-backed leaf / 71 후속 = 143**이며 v1의 고정 migration baseline을 확대하지 않았다.

DbgHelp는 System32의 OS DLL을 명시적으로 로드한다. callback ABI에 SDK의 pack(4)를 적용하고, IoStart의 S_FALSE 이후 모든 쓰기를 callback에서 수행한다. offset+length가 한도를 넘으면 바이트 복사/쓰기 전에 거부한다. 유효한 NUL device handle을 전달해 alternate I/O가 없는 backend가 spool에 제한 없이 쓰지 않도록 하고, start/finish receipt가 없으면 결과를 거절한다. 대상 handle의 생성 FILETIME·생존을 확인하고 읽기/query/synchronize 권한만 요청한다. 권한 승격·debugger 설치·대상 종료를 수행하지 않는다. 동일 PID의 live debugger 기능과 구분한다.

capture의 기존 Job Object·private pinned spool·bounded receipt·shielded verification/cleanup 수명주기를 공통 `OwnedArtifactRecipe`로 추출해 dump에서도 재사용했다. 실제 MDMP header·stream directory bounds·full-memory flag·파일 크기·SHA·target receipt를 검증한다. Artifact 등록 전 실패한 파일은 삭제하고 등록된 durable outcome만 OutputSpool에 넘긴다. CLI worker도 exclusive 생성한 파일만 정리하며 identity 실패 때 기존 파일을 삭제하지 않는다. 파일 close 실패 시 성공으로 기록하지 않고 부분 파일을 정리하며 Win32/HRESULT access denied를 PERMISSION_DENIED로 분류한다.

| 검증 | 결과 | 근거 / 범위 |
|---|---|---|
| strict 계약·Provider | RED **18 failed** → GREEN | [RED](../../dist/acceptance/process-dump-contract-provider-red-20261009.xml); caller flags/path·PID/boot·budget·profile·separate export |
| 실제 OS worker | RED **5 failed** → GREEN **6 passed** | [RED](../../dist/acceptance/process-dump-worker-red-20261009.xml), [GREEN](../../dist/acceptance/process-dump-worker-green4-20261009.xml); 실제 Windows 자체 Python 대상 mini/full, budget/identity, 기존 파일 보존 |
| 고정 recipe RPC/MCP·Artifact | RED → GREEN cohort **48 passed** | [RPC RED](../../dist/acceptance/process-dump-rpc-red-20261009.xml), [GREEN](../../dist/acceptance/process-dump-rpc-green2-20261009.xml); 실제 DbgHelp, capture 수명주기 회귀 및 catalog 포함 |
| close 실패·access HRESULT | RED **3 failed** → GREEN worker **9 passed** | [RED](../../dist/acceptance/process-dump-error-cleanup-red-20261009.xml), [GREEN](../../dist/acceptance/process-dump-error-cleanup-green2-20261009.xml) |
| source Client 공통 UI | **PASS: 등록 2경로 + 설정** | [새 결과](../../dist/acceptance/process-dump-source-ui-20261009/agent-permissions-source-ui.json); Vite/headless source 시험, 이전 증거 덮어쓰기 없음 |
| 정적 검사·version | Ruff PASS, mypy **175 source files / issues 0**, version/기본 계약 **12 passed** | [계약 결과](../../dist/acceptance/process-dump-contracts-version2-20261009.xml); Client Node **11 passed**, TypeScript `--noEmit` exit 0, VERSION=0.1.20 |
| 실제 121 process.dump | **PASS** | [source Agent run](../../dist/acceptance/RUN-20261009-B-dump-rpc-1/results.json); 임시 source Agent·기존 packaged Python, argv/shell deny 상태 |
| 기존 native WinDbg MCP | **PASS: open/메모리 읽기/모듈/스레드/close** | [원문](../../dist/acceptance/RUN-20261009-B-dump-rpc-1/native-windbg-mcp.json); dump 분석이며 live attach/step 인수 아님 |
| dump 변경 전체 회귀 | **PASS: 575 passed / 22 skipped**, 908.22초, exit 0 | [결과](../../dist/acceptance/process-dump-full-20261009.xml), [로그](../../dist/acceptance/process-dump-full-20261009.log); 597 collected. 이후 추가한 close/HRESULT 3개는 위 focused worker 재검으로 별도 확인 |
| 마지막 현재 cohort | **PASS: 50 passed**, 21.33초 | [결과](../../dist/acceptance/process-dump-current-green-20261009.xml); 현재 worker의 close/HRESULT/readiness 보완·Provider·실제 MCP/Artifact·공통 capture 수명주기 포함 |

실제 source Device `dev_28312062c86b49ceb9a6f2a9d8c4b40f`, boot `boot_90c0f9c4cceb458aa49a32527db4ff3e`는 v0.1.20이며 source ZIP SHA-256은 `45a236d43d08175dd56ba6dd8ddc4b7e6aa5c537b2c1e406d4b9b4f77e42dc1d`다. 자체 시험 target **PID 2464/create_time 1791530152.8899777**을 full dump로 회수했다. **49,318,730 bytes**, SHA-256 **`68bf296ca57af72765d2f9f30daaa74081296fc2ed4d08e5469bcbaf9bc34df2`**, Artifact `art_7f663d369aa4409fb691c0c526cc5764`다. 잘못된 boot/create_time·Agent 자체 PID를 거절하고 4 KiB budget은 RESOURCE_EXHAUSTED로 종료했다. 덤프 뒤 target 생존을 확인했으며 시험 완료 후 정확한 parent boot/PID/create_time으로 source와 target을 종료하고 folder를 삭제·시험 Device revoke했다.

기존 WinDbg MCP에서 같은 SHA의 파일을 기존 RE Lab 허용 경로로 복사해 열었다. `|`의 target ID **0x9a0=2464**, python.exe/python312.dll 모듈·2개 스레드를 확인했다. `db 0x1db51a3c410 L20`이 실제 remote buffer의 **`RACP_REMOTE_TYPED_DUMP_20261009`**와 NUL을 반환했다. 예외 정보를 넣지 않은 수동 덤프의 `!analyze -v` 출력에 나온 0x80000003을 실제 target crash로 분류하지 않는다. 실제 생존 검사를 별도로 수행했다.

첫 native open은 자동 `!analyze -v`의 45초 timeout으로 실패했다. [원본 실패](../../dist/acceptance/RUN-20261009-B-dump-rpc-1/native-windbg-open-failure.json)를 보존했고, Session ID를 반환하지 않은 own orphan cdb의 PID/생성 시각/해당 dump 경로를 확인해 그 프로세스만 정리했다. timeout 120초의 새 호출은 약 3초의 자동 분석으로 정상 Session ID를 반환했다. 원인을 확정하지 않은 symbol/초기 분석 지연을 Agent 수집 오류 또는 원격 기술 불가로 바꾸지 않는다. 반환받은 자기 Session만 close했고 [최종 상태](../../dist/acceptance/RUN-20261009-B-dump-rpc-1/final-status.json)에서 cdb 부재·임시 Device REVOKED·기존 121 ONLINE epoch 11을 확인했다.

native worker 첫 구현은 유효한 hFile 누락, callback packing, FILETIME float 계산 때문에 실패했다. 실제 OS 시험으로 수정했으며 DbgHelp가 자동 추가하는 header flags를 요청의 full-memory bit와 구분했다. 추가 close-failure fixture는 target Python의 초기 loader 상태에서 ERROR_PARTIAL_COPY를 받아 실패했고([보존 결과](../../dist/acceptance/process-dump-error-cleanup-green-20261009.xml)), 명시한 own target readiness 이후 주입하도록 fixture를 고쳐 위 9개 worker 시험을 통과했다. 원격 121 fixture도 수집 전에 ready receipt를 확인했다.

> [!IMPORTANT]
> 제품 build·패키징·OAuth 설정·Enterprise 정책 배포는 수행하지 않았다. 기존 Client v0.1.10 설치 UI가 새 기능을 포함한다는 주장이 아니라 실제 121의 별도 source Agent와 기존 native MCP의 경로를 검증했다. dump 분석·capture 분석 뒤 text clipboard 구현·인수는 §27.8에 기록한다. native live debugger/proxy duplex, system/config 확장, 로컬 승인 UI·권한 축소 시 session 폐기와 잔여 UI 검증은 계속 남아 있다. 전체 goal을 완료로 표시하지 않는다.

### 27.8 typed text clipboard와 실제 121의 Broker 경로

`clipboard.read/state/write`를 Windows 로그인 session의 authenticated Broker pipe에 연결했다. `clipboard.text.read`와 `clipboard.text.write`를 별도로 검사하며 state는 write 권한으로 sequence만 반환한다. CF_UNICODETEXT만 다루고 UTF-8 8 KiB, strict Unicode/NUL 검사, code point 경계의 prefix 읽기를 적용한다. write/clear에는 필수 expected_sequence가 있으며 clipboard를 연 상태에서 비교한 뒤 변경한다. input lease·화면 focus·키보드 입력·generic shell은 사용하지 않는다. 현재 catalog는 **74 RPC-backed leaf / 69 후속 = 143**이며 v1 migration baseline은 그대로다.

`desktop_enabled=false`라도 명시한 clipboard grant가 있으면 로그인 session Broker를 시작하도록 derived 설정을 추가했다. desktop 입력 권한을 함께 허용하지 않는다. read/state 뒤 잠긴 session 재검사, 변경 직전 session guard, clipboard busy·sequence 충돌·손상 데이터 분류를 적용했다. 실제 Win32 시험에서 close 전후 sequence가 달라지는 오류를 발견하고 반환 시점을 close 뒤로 수정했다. 반환 sequence는 그 시점의 관측이며 다른 앱의 후속 변경을 막는 lock으로 간주하지 않는다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 계약·실제 native Win32 | RED **13 failed** → GREEN **13 passed** | [RED](../../dist/acceptance/clipboard-contract-native-red-20261009.xml), [GREEN](../../dist/acceptance/clipboard-contract-native-green-20261009.xml); 자체 private window station으로 사용자 clipboard 보존 |
| Broker·cold settings | **31 passed** | [결과](../../dist/acceptance/clipboard-broker-settings-green3-20261009.xml); clipboard-only session, desktop deny 및 기존 설정 회귀 |
| MCP/RPC 계약 | **33 passed** | [결과](../../dist/acceptance/clipboard-rpc-green2-20261009.xml); fake Broker의 계약 시험과 다음 실제 Win32 시험을 구분 |
| 실제 Win32 + authenticated pipe + MCP | **1 passed** | [결과](../../dist/acceptance/clipboard-native-rpc-green-20261009.xml); test-only private station, 실제 pipe/BrokerCore, Unicode write/read/CAS/clear와 cleanup |
| 현재 focused cohort | **90 passed**, 6.82초 | [결과](../../dist/acceptance/clipboard-current-green-20261009.xml); contracts/version/catalog/pipe/desktop fences 포함 |
| source Client UI | **PASS: 초기 등록 2경로 + 설정** | [결과](../../dist/acceptance/clipboard-source-ui-20261009/agent-permissions-source-ui.json); source 시험, 설치 제품 인수 아님 |
| 정적 검사 | Ruff PASS, mypy **177 source files / issues 0**, Client Node **11 passed**, TypeScript `--noEmit` exit 0 | 제품 build 없음, VERSION=0.1.20 재사용 |
| 전체 회귀 | **601 passed / 22 skipped**, 549.04초 | [XML](../../dist/acceptance/clipboard-full-20261009.xml), [로그](../../dist/acceptance/clipboard-full-20261009.log); 623 collected, 실패 0 |
| 실제 121 일반 로그인 clipboard | **PASS: state/read 및 변경 전 충돌 거부** | [source run](../../dist/acceptance/RUN-20261009-B-clipboard-rpc-1/results.json), [정리](../../dist/acceptance/RUN-20261009-B-clipboard-rpc-1/final-status.json); 실제 CF_UNICODETEXT가 없어 text=null, Unicode payload 성공으로 확대하지 않음 |
| 실제 121 private Win32 clipboard | **PASS: Unicode write/read/prefix/CAS/clear** | [source run](../../dist/acceptance/RUN-20261009-B-private-clipboard-rpc-2/results.json), [정리](../../dist/acceptance/RUN-20261009-B-private-clipboard-rpc-2/final-status.json); test-only backend, 실제 native APIs/pipe, 사용자 WinSta0 clipboard 변경 없음 |

일반 121 run의 source ZIP SHA-256은 `bf92bd4f63be36d782e37432d7e55aba48c6ffad35ee553859678119d973a457`이다. Session 1의 sequence 25/Unicode format 없음 상태를 읽고 의도적으로 다른 sequence의 write가 PRECONDITION_FAILED/CLIPBOARD_CHANGED로 변경 전에 차단됨을 확인했다. desktop.type은 Agent에서 PERMISSION_DENIED다. 실제 user clipboard에는 쓰지 않았다.

private run의 source ZIP SHA-256은 `1cdf80deeb3524b7a64bfe39bdc4729e8b08794d4f5cdbb43dd0db237adde2dd`, test-only backend SHA-256은 `ddae11956fdaef8d5bfb73ae6ba4fd8283fed47f18e6995cb47ce94aa09b24d5`다. 별도 source Device `dev_5bdd608bf1514324b0f4be1d8662c996`에서 `RACP_REMOTE_CLIP_ROUNDTRIP_한글🙂`의 roundtrip, 5-byte `RACP_` prefix/truncated, 내용 없는 state, stale sequence 거부 및 clear 뒤 text=null을 확인했다. private window station은 테스트 fixture에만 존재하며 제품에서 임의 선택할 수 있는 backend로 추가하지 않았다. generic execution과 keyboard grant는 deny였다.

첫 private run은 기존 `run_source.py`를 create로 다시 쓰던 harness 충돌로 Agent 시작 전에 실패했다. [원본](../../dist/acceptance/RUN-20261009-B-private-clipboard-rpc-1/results.json)을 보존하고 launcher를 한 번만 생성하도록 고쳤다. 두 성공 run은 자신의 source/Broker PID와 생성 시각을 확인해 정리하고 시험 Device revoke·자체 폴더 삭제를 완료했다. 최종 기존 121은 ONLINE epoch 12였다.

> [!IMPORTANT]
> text clipboard의 source 구현과 실제 121 native API 경로를 검증했다. rich formats/images/files clipboard, native live debugger/proxy duplex, config 확장 및 나머지 계획은 미완료다. 현재 설정은 Agent 재시작으로 반영하는 cold 설정이며 hot permission 변경 지원을 주장하지 않는다. Enterprise Server→Client 정책 배포·OAuth 설정·제품 build는 수행하지 않았다.

### 27.9 native WinDbg 복수 채널 실패 원인과 Agent 전송 코어

기존 `open_cdb_remote`를 자체 정상 Python target에 사용해 native CDB의 역방향 연결을 조사했다. 첫 중계는 한 개 TCP 연결만 전달해 초기화 timeout으로 실패했다. 명시한 symbol 경로, 서버 `-noio`, 추가 prompt를 각각 시험했으나 해결하지 못했다. 직접 로컬 named pipe는 기존 MCP에서 정상 Session ID와 실제 marker 메모리를 반환했다. 이후 CDB 서버가 복수 TCP 연결을 생성한다는 사실을 확인하고 **각 연결을 별도 채널로 전달**하자 같은 native MCP의 TCP 역방향 초기화도 성공했다. 현재 자체 fixture에서는 **3 connections**였으며 모든 CDB 대상이 항상 정확히 3개라는 계약으로 고정하지 않는다.

| 검증 | 결과 | 근거 / 범위 |
|---|---|---|
| 단일 채널 reverse TCP | **FAIL: initialization timeout** | [run 3](../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-3/native-open.json), [run 4](../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-4/native-open.json), [noio](../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-6/native-open.json), [prompt](../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-7/native-open.json); 실패 기록 보존 |
| native MCP의 직접 named pipe | **PASS: open/marker read/close** | [환경 출력 제외 초기화](../../dist/acceptance/RUN-20261009-cdb-npipe-loopback-1/native-open-redacted.json); host 내부 비교, 원격 인수 아님 |
| 복수 reverse TCP prototype | **PASS: open/read/step/resume/break/detach/close** | [실제 MCP 명령](../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-9/native-commands.json), [자원 정리](../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-9/results.json); 3채널, target/engine 부재 및 target의 engine 종료 후 생존 |
| Agent source relay 계약 | RED **2 collection errors** → GREEN **15 passed** | [RED](../../dist/acceptance/native-duplex-red-20261009.xml), [초기 GREEN](../../dist/acceptance/native-duplex-green3-20261009.xml); 실제 loopback 3 socket/binary 정확성, typed canonical framing |
| ACK/scope/sequence/budget/lease | **21 passed / 1 failed** → 수정 | [보존 실패](../../dist/acceptance/native-duplex-current-20261009.xml); lease 정리 event와 watcher 완료의 경합 수정 |
| listener 생성 중 retirement | RED **1 failed** → 수정 | [RED](../../dist/acceptance/native-duplex-listen-retirement-red-20261009.xml); cleanup 완료 뒤 늦게 생성된 listener도 닫고 반환을 거절 |
| 현재 전송 코어 + version | **29 passed**, 4.58초 | [현재 GREEN](../../dist/acceptance/native-duplex-current3-20261009.xml); 23 relay/계약 + version 6개, 기존 601 전체 회귀 이후 추가 범위 |
| native MCP → 실제 Agent source core | **PASS: open/read/step/resume/break/detach/close** | [원문, 환경 제외](../../dist/acceptance/RUN-20261009-cdb-source-duplex-2/native-mcp-redacted.json), [최종 상태](../../dist/acceptance/RUN-20261009-cdb-source-duplex-2/results.json); JSON roundtrip carrier/실제 OS socket peer PID 검증, Gateway/WSS 아님 |
| 현재 static checks | Ruff PASS, mypy **179 source files / issues 0**, VERSION **0.1.20** | 제품 build·추가 version bump 없음 |

새 `racp_protocol.native_duplex`는 frozen scope(Device/boot/epoch/principal/workspace/revision/session), 16채널, strict canonical base64의 16 KiB chunk, offset/ACK/end를 정의한다. `racp_agent.native_duplex.NativeDuplexRelay`는 loopback만 bind하고 trusted peer verifier/고정 connector·현재 gate/lease를 요구한다. 채널마다 최대 4개 미확인 chunk를 허용하며 전송된 boundary의 ACK만 credit로 처리한다. 기본 8 MiB/120초, hard 64 MiB/1시간으로 제한하고 frame마다 현재 gate를 재검하며 idle watcher도 회수한다. fingerprint는 scope 결합일 뿐 인증 token이나 암호학적 접근 권한이 아니다. 실제 carrier가 인증한 authority를 제공해야 한다.

새 코어의 첫 소켓 시험은 열린 accepted socket을 닫기 전에 `Server.wait_closed()`를 기다려 멈췄다. [소유 pytest 종료 기록](../../dist/acceptance/native-duplex-first-hang-20261009.json), [진단 원인](../../dist/acceptance/native-duplex-close-hang-20261009.json)을 보존했다. socket→task→server 순서로 닫도록 고쳤으며 자신의 시험 프로세스만 정리했다. 초기 직접 재귀 callback fixture는 bounded queue carrier로 바꿨다. send callback은 인증된 bounded carrier에 frame을 넘기는 계약이며 상대 receive 전체의 동기 완료를 기다리는 구조로 구현하지 않는다.

실제 source core run의 target **PID 21780/create_time 1791544709.0158207**, engine **PID 6180/create_time 1791544718.8557162**를 기록했다. 기존 native MCP Session `cdb-65be5cee`가 `db 0x1ad4b10b090 L18`로 **RACP_SOURCE_DUPLEX_OWN**을 읽었고 step/resume/CTRL+BREAK/detach를 실행했다. source relay 양쪽 모두 **3 channels / 43,412 transferred bytes**였으며 native session close, target의 engine 종료 후 생존, 자체 target/engine 종료 후 부재, relay task 부재를 확인했다. CDB SHA-256은 `5f54abafca3ae5638bbf807d402fabb350a64575c1dfa9fbfc7f5732df5bee67`이다.

첫 source-core run은 MCP open과 start-file 쓰기를 병렬 dispatch했지만 실제 도구 실행에서 start가 open timeout 뒤 수행돼 실패했다. [실패](../../dist/acceptance/RUN-20261009-cdb-source-duplex-1/native-open.json), [정리](../../dist/acceptance/RUN-20261009-cdb-source-duplex-1/results.json)를 보존했다. start-file 생성 후 native open을 순서대로 호출한 두 번째 run은 성공했다. 첫 named-pipe 성공의 자동 `!peb`가 inherited process environment까지 출력했으므로 이후 자체 target은 최소 OS 환경으로 생성하고 저장된 native 증거에서는 환경을 제외했다.

> [!IMPORTANT]
> 실패한 단일 채널 중계는 Agent 전송 코드로 해결 가능한 문제다. 기존 native MCP를 교체하거나 upstream을 수정하지 않았다. 새 코어는 **아직 Gateway/Agent authenticated WSS에 연결하거나 public RPC로 노출하지 않았다**. 121의 managed CDB runtime·exact target containment·outbound authenticated carrier·native MCP live 인수·proxy/replay는 남아 있다. 원본 native command의 광역 OS 제어를 세부 read-only 권한으로 보장하지 않으며, runtime/라이선스/OS 조건과 명시한 광역 grant를 후속 경로에서 적용해야 한다. 이번 host 증거를 121 인수 또는 전체 goal 완료로 바꾸지 않는다.

### 27.10 Hyper-V W11 — 인증 전송 코드와 실제 native WinDbg live 인수

사용자가 테스트 대상을 121에서 호스트의 Hyper-V `W11`로 전환했다. Default Switch의 VM 주소는 `172.28.147.80`, Gateway는 기존 `https://192.168.29.141:8765`다. 새 one-use 연결 파일을 발급해 Hyper-V guest file-copy로 VM에 전달하고 해당 integration service를 원래 disabled 상태로 복원했다. W11 Device **dev_a15c14e62d1d45e792786533702ac9d7**의 실제 Agent는 **0.1.13**이었다. 처음 read_only/medium integrity에서 사용자가 standard·관리자 실행으로 변경했고 session 2/integrity 12288/ONLINE을 확인했다. 기존 Client나 Agent binary를 빌드·교체하지 않았다.

Agent source에 `native.prepare/start/close`, 관리 CDB runtime 검증·Job Object 수명주기·helper socket peer 검증·현재 grant/revision/epoch/lease 회수와 queued output 재검을 추가했다. Gateway에 owner bearer/TLS WebSocket carrier, 단일 subscriber·Device/boot/epoch/owner/workspace/session scope·byte/queue ceiling·revocation/disconnect 정리를 연결했다. SDK가 공통 binary relay를 사용하며 원본 WinDbg MCP의 native protocol과 Session API를 그대로 유지한다. raw native bytes는 operation Journal에 저장하지 않는다. Gateway는 Client settings를 덮어쓰거나 grant를 확대하지 않는다.

관리 runtime은 caller RPC의 임의 executable/argv 대신 신뢰 설정의 manifest digest와 각 component SHA·x64 PE를 확인한다. 파일 handle을 FILE_SHARE_READ로 pin하고 helper 실행 중 변경·rename을 차단한다. fixed recipe는 CDB reverse loopback transport, `-pd/-noshell/-noinh`와 선택한 pinned Exts DLL만 사용한다. CDB engine은 Job Object에 포함하고 대상 프로세스는 borrowed다. native command는 넓은 OS 제어가 가능하므로 `network.tunnel.open`뿐 아니라 `exec.argv`, attach·memory read·export 등 명시한 grant를 요구한다. executable allow/deny 목록을 적용한 경우 opaque native bridge를 거절한다. 초기 target 검사와 별개로 임의 native command 전체를 read-only 또는 지정 PID에만 제한했다고 주장하지 않는다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| W11 source OS·권한 baseline | **OS 7개 및 deny PASS, Browser FAIL** | [원본](../../dist/acceptance/RUN-20261009-W11-source-permissions-1/results.json); source v0.1.20, 별도 Device. Browser는 UNKNOWN/cleanup grace 오류이며 전체 PASS로 표시하지 않음 |
| Browser 실패 후 시험 폴더 정리 | **복구 완료** | [진단](../../dist/acceptance/RUN-20261009-W11-source-permissions-1/diagnostic-native.json), [복구](../../dist/acceptance/RUN-20261009-W11-source-permissions-1/cleanup-recovered.json); source PID 부재, 남은 빈 자체 폴더만 nonrecursive rmdir. Browser 원인은 확정하지 않았고 사용자 요청에 따라 추가 Browser 시험 보류 |
| runtime pin 계약 | RED collection error → **14 passed**, 확장 추가 후 **15 passed** | [초기 GREEN](../../dist/acceptance/native-runtime-green-20261009.xml), [현재](../../dist/acceptance/native-runtime-extension-20261010.xml); manifest/version/alias/hash/PE/extra files·write pin |
| core/catalog/version focused | **71 passed** | [결과](../../dist/acceptance/native-carrier-foundation-20261010.xml); 기존 전체 601회귀 이후 신규 변경 범위 |
| carrier·계약 focused | **25 passed**, 9.48초 | [결과](../../dist/acceptance/native-carrier-current-20261010.xml); owner/epoch/scope/bytes·schema/기본 계약/runtime |
| 현재 최소 focused | **42 passed**, 5.20초 | [결과](../../dist/acceptance/native-final-focus-20261010.xml); 이후 확장 1개는 위 15개 runtime 재검으로 구분 |
| 최종 변경 범위 회귀 | **83 passed**, 5.55초 | [결과](../../dist/acceptance/native-carrier-final-20261010.xml); native carrier/runtime/공통 relay·schema·기본 계약·권한/catalog/version. 기존 전체 601회귀를 새 코드 전체 회귀로 주장하지 않음 |
| Gateway source reload | **PASS: DB backup, 같은 boot, W11 epoch 4** | [기록](../../dist/acceptance/RUN-20261010-W11-native-gateway/restart.json); 같은 DB/TLS/주소, 다른 앱 listener 유지 |
| W11 기본 live native MCP | **PASS: 실제 open/read/step/resume/break/detach/close** | [native 원문](../../dist/acceptance/RUN-20261010-W11-native-live-3/native-mcp.json), [carrier 결과](../../dist/acceptance/RUN-20261010-W11-native-live-3/native-results.json); 3채널/34,482 bytes, 처음 최소 runtime의 `!peb` 확장 없음은 별도 제한 |
| Exts 보완 및 W11 live/PEB | **PASS** | [native 원문, 환경 제외](../../dist/acceptance/RUN-20261010-W11-native-live-4/native-mcp-redacted.json), [결과](../../dist/acceptance/RUN-20261010-W11-native-live-4/native-results.json); 3채널/59,582 bytes, 실제 PEB·marker·step/continue/break/detach |
| 최종 소유 자원·기존 Agent | **PASS** | [최종 확인](../../dist/acceptance/RUN-20261010-W11-native-live-4/final-status.json); source/target PID 부재·helper process 없음·자체 folder 부재·시험 Device revoke·기존 W11 ONLINE epoch 4. 호스트 native client PID 19376 부재도 확인 |
| 정적 검사·버전 | Ruff PASS, mypy **186 source files / issues 0**, VERSION **0.1.20** | 같은 변경 batch 재사용, 제품 build·OAuth·Enterprise 정책 배포 없음 |

최종 run의 source Device **dev_348ce7d639924168af7c83eaa93f13d4**, boot **boot_c45d8fc6e50d4cd8b0d985f17d73bd5e**에서 native scope를 준비했다. VM target **PID 7704/create_time 1791560657.2513208**에 대한 native MCP Session **cdb-86556e6e**가 `db 0x23297df8a50 L20`으로 **RACP_W11_NATIVE_LIVE_OWN**을 읽었다. `|`의 **0x1e18=7704**, registers·threads, 실제 `t/g/CTRL+BREAK/.detach` 및 close를 확인했다. native session 정리 후 target 생존을 먼저 확인하고 자신의 source/target만 종료했다. runtime 전체 SHA와 source/runtime ZIP SHA·manifest digest는 각 run JSON에 보존했다. 런타임은 현재 호스트의 정상 Microsoft debugger 파일을 private 시험용으로 복사한 것이며 제품 재배포·설치·라이선스 인수를 뜻하지 않는다.

첫 run은 harness가 filesystem.read의 `text`를 `content`로 읽어 실패했다([기록](../../dist/acceptance/RUN-20261010-W11-native-live-1/native-results.json)). 두 번째는 native client의 IPv4/IPv6 listener 2개를 서로 다른 process로 간주해 start를 못 했다([기록](../../dist/acceptance/RUN-20261010-W11-native-live-2/native-results.json), [native 실패](../../dist/acceptance/RUN-20261010-W11-native-live-2/native-open-failure.json)). PID를 deduplicate하고 executable/owner/정확한 connection string 검사 후 시작하도록 고쳤다. 성공 run의 listener rows=2/PID=1로 원인을 재확인했다. Agent 기술 불가로 분류하지 않는다.

처음 최소 runtime에서 `!peb`가 없던 문제는 Microsoft 문서의 [Exts.dll 요구](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/-peb)를 확인해 pinned extension과 고정 `-a` recipe로 수정했다. 이후 필요한 ntdll PDB가 없어 조회가 실패했다. 원격 CDB의 symbol 경로는 **VM의 경로**이므로 호스트 E: cache를 사용하지 않고 자체 VM folder의 cache로 `.sympath`를 설정한 뒤 공개 Microsoft OS symbols를 로드해 PEB 조회를 성공시켰다. `.sympath`는 semicolon을 경로 구분자로 읽으므로 reload·PEB를 별도 명령으로 실행했다. `native.prepare`는 호스트 도구가 사용할 VM symbol cache 경로도 반환한다. PDB/도구 확장 조건을 원격 Agent 자체의 불가로 바꾸지 않는다.

> [!IMPORTANT]
> 기존 native WinDbg MCP→SDK→Gateway bearer/TLS→outbound Agent→관리 CDB→실제 VM process의 live 경로를 처음으로 완료했다. 현재 catalog는 **75 RPC-backed leaf / 68 후속 = 143**이며 신규 leaf를 v1 migration에 추가하지 않았다. 모든 native 도구·모든 command·무제한 OS 제어 또는 모든 143 후보의 구현 완료를 뜻하지 않는다. proxy/replay, 추가 OS 기능·로컬 승인/hot retirement·제품 배포 인수는 남아 있다. Browser 추가 시험은 보류하고 구현 및 필요한 최소 인수에 집중한다. 전체 goal을 완료로 표시하지 않는다.

### 27.11 Hyper-V W11 — 원본 proxy MCP의 HTTP 수집·인터셉트·재전송

Agent에 `proxy.prepare/close`와 process-bound ProxyProvider를 추가하고 기존 SDK·Gateway 인증 carrier를 양방향으로 확장했다. scope의 endpoint role을 검증하며 Agent listener는 정확한 client PID/생성 시각, connector는 정확한 server PID/loopback 포트에 결합한다. host native socket도 기존 MCP Python PID/생성 시각과 역방향 TCP tuple로 검증한다. proxy helper를 VM에 설치하거나 사용자 앱·시스템 proxy·Root CA를 변경하지 않았다. 광역 native 전송은 intercept/replay/tunnel/exec/export grant가 모두 필요하며 executable allow/deny constraint가 설정되면 거절한다. 이전 등록의 권한 확대·Enterprise 설정 배포·OAuth·제품 build는 수행하지 않았다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 첫 변경 범위 검사 | **28 passed / 1 failed** | [실패](../../dist/acceptance/proxy-carrier-current-20261010.xml); 시험 fixture가 venv launcher PID를 socket 소유 PID로 잘못 사용 |
| 실제 socket/계약 수정 | **29 passed**, 5.07초 | [결과](../../dist/acceptance/proxy-carrier-current2-20261010.xml); base Python의 실제 PID와 async subprocess 사용, binary 보존·peer birth 거부 |
| 최종 변경 범위 | **90 passed**, 5.86초 | [결과](../../dist/acceptance/proxy-carrier-final-20261010.xml); 반복 close·다른 workspace close 거부와 native/core/catalog/schema/version 회귀. 이전 전체 601회를 최신 전체 회귀로 주장하지 않음 |
| 정적 검사 | Ruff PASS, mypy **189 source files / issues 0** | VERSION 0.1.20 변경 batch 유지 |
| Gateway source reload | **PASS: 같은 boot, W11 epoch 5** | [기록](../../dist/acceptance/RUN-20261010-W11-proxy-gateway/restart.json); nonterminal 작업 0 확인·DB backup·동일 TLS/DB/주소 |
| 첫 실제 W11 수집·재전송 | **PASS, explicit close FAIL** | [native MCP](../../dist/acceptance/RUN-20261010-W11-proxy-live-1/native-mcp.json), [VM receipt/정리](../../dist/acceptance/RUN-20261010-W11-proxy-live-1/proxy-results.json); GET 200 및 수정한 POST/header/body가 VM server에 도착 |
| 범위 제한 native 인터셉트 | **PASS: 특정 URL header 수정** | [VM receipt](../../dist/acceptance/RUN-20261010-W11-proxy-live-1/intercept-results.json); nonce URL에만 inject_header, 자신의 rule만 remove_global_header로 제거·전후 rules={} |
| 새 source의 최종 실제 인수 | **PASS: capture/replay/close/borrowed 생존/정리** | [native MCP](../../dist/acceptance/RUN-20261010-W11-proxy-live-2/native-mcp.json), [결과](../../dist/acceptance/RUN-20261010-W11-proxy-live-2/proxy-results.json); origin 2채널/1,636 bytes, proxy 1채널/450 bytes |
| 최종 보존·정리 | **PASS** | [확인](../../dist/acceptance/RUN-20261010-W11-proxy-live-2/final-status.json); 기존 W11 ONLINE epoch 5·source Device REVOKED·자체 folder 부재·자신이 시작한 proxy listener 부재 |

원본 `mitmproxy_mcp`의 `start_proxy/search_traffic/inspect_flow/replay_flow/add_interception_rule/remove_global_header/stop_proxy`를 사용했다. 기존 rules·traffic DB는 지우지 않았고 자신의 새 `.flow` 파일에 기록했다. 첫 run에서 VM server PID 11744/client PID 11352의 자체 HTTP 요청을 host MCP flow `74a09999-da2e-43b8-8fdf-9d893b4733c1`로 읽었다. 같은 flow를 POST로 바꿔 `RACP_OWN_REPLAY_BODY`와 nonce header를 재전송했고, scoped rule로 변경한 header도 VM server receipt에서 확인했다. host MCP process PID 22860/create_time 1791542346.059802 및 socket ownership을 검증했다. 이 원본 MCP나 upstream 코드는 수정하지 않았다.

첫 실제 인수에서 SDK unsubscribe로 이미 정리된 session에 explicit close를 호출하면 HANDLE_EXPIRED를 반환했다. Agent의 close가 만료·종료 상태를 다시 active로 검사하던 결함이다. 현재 operation gate와 owner/device/boot/workspace를 확인하고 이미 닫힌 세션도 동일 CLOSED handle로 반환하도록 수정했다. 같은 범위 local 회귀와 새 source Agent의 실제 W11 인수로 수정 후 정리까지 검증했다. 첫 실패 기록을 PASS로 덮어쓰지 않았다. 두 run 모두 자신의 source/client/server를 생성 시각에 결합해 종료하고 폴더를 삭제했다. borrowed target은 carrier 정리 후에도 생존했고 이후 시험 cleanup에서만 종료했다.

> [!IMPORTANT]
> 현재 catalog는 **77 RPC-backed / 66 후속 = 143**이다. 실제 HTTP 경로의 수집·인터셉트·재전송과 native debugger의 live 분석을 증명했지만 모든 native 도구/command·모든 OS 앱의 자동 routing·HTTPS/pinning/mTLS·전체 143 후보 완료를 증명하지 않는다. 해당 조건 및 추가 OS Provider·로컬 승인/hot retirement·제품 배포 인수는 남아 있다. 이 경로의 반복 시험은 종료하고 남은 구현 작업으로 이동한다.

### 27.12 Windows 서비스·소프트웨어 inventory RPC 구현

`services.list/get`, `software.inventory`를 OSObservationProvider에 연결했다. 공통 model은 list limit 1..200/offset 0..10000과 제어 문자·경로 구분자를 제외한 service name을 검사한다. Registry는 Windows-only/read-safe로 광고하고 `services.read`와 `software.inventory.read`를 각각 검사한다. 기존 등록에 새 권한을 자동 부여하지 않는다. 임의 shell/argv·SCM 변경·install/uninstall 실행을 하지 않는다.

서비스 조회는 psutil native SCM 경로에서 이름·표시명·상태·시작 유형만 읽으며 executable arguments/logon account를 포함하는 `as_dict()`는 호출하지 않는다. 소프트웨어 조회는 machine/user 및 32/64-bit의 고정 Uninstall registry 경로에서 DisplayName/DisplayVersion/Publisher만 읽는다. native RegQueryValueExW는 4 KiB 고정 buffer로 조회하며 oversized/비문자/손상 값은 포함하지 않는다. 환경 변수를 확장하지 않고 uninstall command·credential을 읽지 않는다. registry 등록 inventory이며 MSIX 전체 목록이나 실제 설치 상태의 완전한 증명을 뜻하지 않는다. source 접근 실패·record unavailable·live pagination을 결과에 표시한다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 현재 inventory/OS·MCP·권한 계약 | **44 passed**, 16.12초 | [결과](../../dist/acceptance/windows-inventory-current-20261010.xml); 실제 host SCM·전용 HKCU fixture의 native bounded query·MCP grant off 거부/on 허용·실행 권한 없음·schema/catalog |
| 정적 검사 | Ruff PASS, mypy **190 source files / issues 0** | 첫 mypy의 list annotation 누락을 보완, VERSION 0.1.20 batch 유지 |
| W11 source reload 전제 검사 | **보류: 기존 Agent OFFLINE** | reload helper가 ONLINE 전제에서 멈춰 기존 Gateway를 종료하지 않음. Hyper-V W11 State=Running, 기존 Device epoch 5/같은 boot의 OFFLINE을 다시 조회 |
| 새 inventory의 실제 VM 인수 | **미실행** | 사용자에게 기존 Client의 Agent 재시작을 요청했다. host native/MCP 시험을 VM 성공으로 확대하지 않음 |

현재 catalog는 **79 RPC-backed / 64 후속 = 143**이다. 새 inventory와 남은 OS/로컬 승인/설정 기능 구현을 계속하며, 원격 연결 재개 후 해당 신규 RPC의 최소 VM 인수만 수행한다. 제품 build 및 Enterprise 정책 배포는 보류 상태를 유지한다.

### 27.13 원격 작업 폴더의 text 검색·검토 가능한 여러 파일 patch

`filesystem.search_content` 및 `filesystem.patch`를 Registry·MCP·Agent Provider·세부 권한에 연결했다. 검색은 literal line 방식이고 regexp/shell을 실행하지 않는다. anchored regular-file read, per-file revision 확인, 최대 10,000 entries/파일 수·파일 크기·전체 scan credit·깊이·결과 수 제한, 링크·binary·손상 encoding·변경 파일 skip을 적용한다. casefold가 ß 등을 여러 문자로 확장할 때도 원문의 Unicode codepoint 열을 반환하며 긴 줄의 match 주변 preview를 제공한다.

patch는 16개 파일/128 edits/입력 text 64 KiB 및 파일당 64 KiB로 제한한다. 각 대상의 예상 SHA와 선택적 revision, exact occurrence count를 **전체 preflight**에서 검사하고 diff/dry-run 또는 순차 변경을 수행한다. UTF-8 BOM·편집하지 않은 CRLF/LF bytes를 보존한다. 기존 anchored temp-file writer/ACL 보존/원자적 교체/cleanup을 재사용하며 commit 전 hash/revision을 다시 검사한다. file worker의 Budget에도 captured permission revision과 현재 grant 검사를 전달해 폐기 후 publication을 막는다. 기존 `filesystem.write`에는 명시한 bytes의 줄바꿈을 유지하는 `newline=verbatim`을 추가했다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 최초 변경 범위 | **53 passed / 1 failed / 1 skipped**, 50.91초 | [원본](../../dist/acceptance/text-files-current-20261010.xml); 실제 host file APIs·기존 파일/실패/권한·catalog/schema. MCP mutation 시험에서 필수 idempotency key 누락 |
| MCP 요청 보완·영향 큐 경로 | **4 passed**, 22.14초 | [결과](../../dist/acceptance/text-files-mcp-queue-final-20261010.xml); grant off의 실제 PERMISSION_DENIED/on patch/search, queued cancel/FIFO·Gateway restart·late result |
| 최종 text 기능·MCP | **10 passed**, 3.52초 | [결과](../../dist/acceptance/text-files-final-20261010.xml); Unicode 열/긴 줄 preview, scan/result budgets·link 거부·preflight 무변경·dry-run·BOM/혼합 newline 보존·partial batch·교체 직전 권한 폐기/cleanup |
| 정적 검사 | Ruff PASS, mypy **191 source files / issues 0** | 같은 VERSION=0.1.20 batch, build 없음 |
| 새 기능의 실제 W11 인수 | **대기: 기존 Agent OFFLINE epoch 5** | 같은 boot/OFFLINE을 현재 다시 조회했다. host file/MCP 결과를 VM 결과로 확대하지 않음. 기존 Agent 재시작 질문을 반복하지 않음 |

첫 과정의 Python WMI 호출에서 `0x8007000e` 예외 진단이 출력됐지만 검사 프로세스는 계속 실행됐다. 실제 실패는 MCP mutation signature의 필수 idempotency key 누락이었다. 해당 key를 넣고 off 결과가 PERMISSION_DENIED인지도 확인해 validation failure를 권한 거부 성공으로 착각하지 않도록 보완했다. 이후 MCP 검색·편집은 실제 파일 변경과 반환값으로 성공을 확인했다. 검사 중 관측한 host 메모리는 available 14.14 GiB/total 32 GiB이며 원격 Agent OFFLINE 원인을 이 진단으로 확정하지 않는다.

> [!IMPORTANT]
> 여러 파일의 교체는 **파일별 원자성**만 보장한다. 후속 파일 실패 시 완료된 파일과 hash/실패 대상을 error details로 반환하고 전체 batch 원자성·자동 rollback·외부 writer에 대한 atomic CAS를 주장하지 않는다. 완료된 mutation은 Journal의 같은 idempotency key로 다시 실행하지 않는다. 현재 catalog는 **81 RPC-backed / 62 후속 = 143**이며 로컬 승인/hot settings·나머지 OS 기능 및 W11 신규 인수는 아직 남아 있다.

### 27.14 Client 지연 측정 및 언어/플랫폼 전환 검토

사용자가 Client의 응답 지연과 Windows/macOS/Linux에 맞는 Rust/C++/C# 전환을 문의했다. 현재 UI는 Electron/React이고 로컬 Agent/desktop bridge는 Python이다. `backend.cjs`는 매 요청마다 Python을 실행하며 `controller.cjs`는 3초마다 status 다음 activity를 순서대로 요청한다. renderer의 overview는 cached 조회지만 refresh·변경 작업은 backend 응답을 기다린다. desktop bridge의 background/main import는 Agent runtime도 먼저 불러온다. 측정은 실제 사용자 설정·credential·실행 중 Client를 건드리지 않고 호스트의 새 빈 state에서 각 명령을 3회 실행했다.

| 측정 | info 중앙값 | status 중앙값 | activity 중앙값 | 범위 / 증거 |
|---|---:|---:|---:|---|
| 기존 0.1.13 번들 | 2,558.6 ms | 2,105.6 ms | 2,153.6 ms | [원본](../../dist/acceptance/RUN-20261010-client-latency-38f1bf09/timing.json); 같은 번들, 호스트의 별도 빈 설정 |
| 현재 0.1.20 source | 4,352.3 ms | 3,708.2 ms | 3,134.5 ms | [원본](../../dist/acceptance/RUN-20261010-client-latency-d4613d2c/timing.json); 프로젝트 .venv, 다른 runtime/package 조합이므로 버전별 순수 성능 비교 아님 |

기존 번들의 status/activity 중앙값 합은 약 4.26초다. 상태 조회만으로도 fresh interpreter/import 비용이 발생하며 serial sampling과 사용자의 변경 동작 대기가 지연 후보임을 확인했다. 해당 host/빈 설정 표본을 W11 실제 버튼 latency나 모든 CPU/메모리 병목의 확정으로 확대하지 않는다. 아직 언어 전환·새 Client 실행 파일·제품 build 또는 최적화 후 성능 수치를 보고하지 않는다.

검토 대상은 Rust+Tauri, C+++Qt, C#+Avalonia다. [Tauri process model](https://v2.tauri.app/concept/process-model/)은 Rust core/OS WebView 및 frontend 분리를, [Qt 플랫폼 지원](https://doc.qt.io/qt-6.10/supported-platforms.html)은 Windows/macOS/Linux를, [Avalonia 구조](https://docs.avaloniaui.net/docs/fundamentals/cross-platform-architecture)는 Skia 기반 공통 UI/플랫폼별 backend를 설명한다. [.NET Native AOT](https://learn.microsoft.com/en-us/dotnet/core/deploying/native-aot/)는 대상별 native 배포를 지원하지만 dynamic loading/reflection 및 Windows built-in COM 제약을 별도로 검토해야 한다. GUI 선택과 별개로 Agent의 직접 OS API·권한/수명주기·비동기 처리를 유지하고 Gateway의 기존 Python API/프로토콜과 호환해야 한다. 이 검토는 전환 구현 완료를 뜻하지 않는다.

### 27.15 Client 전체 Rust 전환 계획 수립

사용자가 Client 영역 전체 전환과 레거시 미유지를 명시했다. [Rust Client 전환 계획](../spec/rust-client-migration-plan.md)을 새 실행 SSOT로 작성하고 Python/Electron [기존 구현 계획](../spec/agent-permissions-implementation-plan.md)을 Superseded로 표시했다. 기준선은 0.1.20/등록 operation 100개/권한 143개(81 RPC-backed·62 planned)이며 기능/권한 요구와 과거 증거를 이어받는다.

전체 전환 조건에 따라 UI도 Rust/Iced native로 재작성하고 앞선 Tauri+React 재사용 제안은 제외했다. GUI·Agent·Broker/Guardian·OS Provider·관리 helper wrapper·Client 빌드 도구를 R00–R14로 분해하고 각 산출물·선행·gate, Windows/macOS/Linux adapter·성능 목표·실제 원격 MCP 인수·레거시 제거 조건을 정의했다. Iced의 upstream experimental 상태와 renderer/IME/트레이/접근성 적합성은 R01 gate로 명시했다.

Gateway/서버 Python·별도 Console·기존 host MCP·사용자 DB는 유지한다. 구 Client/Python fallback·구 설정 자동 이전·이중 운영은 최종 배포에서 제외한다. 기존 실행 파일/등록/자료를 현재 삭제하거나 자동 가져오지 않으며 새 Rust 설치는 새 설정·등록 기준이다. 새로운 조직 정책 배포·OAuth 설정·제품 build는 요청 범위에 추가하지 않았다.

이번 산출물은 계획서와 문서 포털/상호 링크 갱신이다. Rust 구현·Cargo 컴파일·Client 패키징·기존 Agent 중지/Device revoke는 수행하지 않았다. 후속 기능 구현과 Rust 실제 검증은 미완료이며 전체 제품 goal을 완료로 표시하지 않는다.

<a id="rust-plan-review-20261010"></a>

### 27.16 Rust 전환 계획 검토·보완

2026-10-10 [Rust Client 전환 계획](../spec/rust-client-migration-plan.md)을 현재 소스와 비교해 보완했다. 검토 기준 commit은 `c0fe694675ca74d2926d015843c11ee913311584`, 제품 SSOT는 v0.1.20이다. 실제 Registry와 공개 JSON은 각각 **100개 operation**, catalog는 **18개 category / 중복 없는 143개 permission(81 rpc·62 planned)**이며 catalog가 참조하는 미등록 operation은 없었다. 이는 계약 inventory 확인이며 Rust 기능 검증 결과가 아니다.

- 기준 기능 이전·신규 권한 확장·플랫폼별 완료를 구분하고 commit/hash·operation↔permission·backend·test·evidence 추적 필드를 정의했다.
- R12-D/R12-N과 R13-W/R13-N으로 Windows 우선 경로와 타 OS 인수 의존성을 분리했다. R-PKG 조기 배포 검증과 R06 최소 end-to-end checkpoint를 추가했다.
- Iced 접근성/IME/software renderer, 격리 profile의 Rust CDP 동등성, blocking OS 호출 취소, durable Journal/UNKNOWN/tombstone, hot policy CAS, Rust state upgrade/rollback, 장애 주입·soak·성능 표본 조건을 구체화했다.
- schema drift/버전 검사의 `racp_agent` import, root workspace dependency, quality CI를 확인해 레거시 삭제 전 계약 분리와 삭제 후 서버/Console 회귀 gate를 추가했다.

검증은 기준선 집계, 계획서 및 이번 현황 항목의 상대 링크 **40개**, 계획서의 명시적 목차 anchor **15개**, metadata/code fence 형식, `git diff --check` 모두 **PASS**다. 이번 변경은 계획/현황 문서 보완이며 제품 버전·runtime·CI·설치 상태는 변경하지 않았다. Rust 구현, 컴파일, 제품 패키징, 원격/OS 인수는 미실행이다.

작성 후 관련 문서 7개의 표준 metadata·상대 링크/명시 anchor, 계획서의 R00–R14 작업 번호·code fence를 확인했고 오류는 0개였다. 문서 검증이며 기능/플랫폼 실행 검사로 집계하지 않는다.
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

- Windows [38017082032](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38017082032)의 384737f checkpoint에서 Rust desktop 읽기 Broker를 포함한 production release 빌드·ZIP·artifact 업로드 PASS, acceptance job SKIPPED. 동봉 ZIP SHA-256: `f054aa9f6c1a6b3c2597f0aba5420d1bf11eb862c48bac6d7c916d53242db5cd`. [Windows Agent 빌드 다운로드](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38017082032/artifacts/11656414025)에 EXE, Chromium, 실행 가이드 및 파일별 build manifest가 들어 있다. 새 데스크톱 경로의 실제 입력/GUI 테스트는 실행하지 않았다. 클라우드 작업 환경이 이후 offline 상태로 전환되어 로컬 작업은 연결 복구를 기다린다.

## Rust 클라이언트 v0.1.21 네이티브 패키징 증거 (2026-10-10)

Windows [rust-client run 38036784974](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38036784974)는 소스 `0210ee520cc168df065c431d0fefa6eea2f2533d`에서 성공했다. Rust Agent/포터블 런처, Tauri 클라이언트와 React production build, Chromium 1243 및 fixed WebView2 154.0.4258.62의 실제 스테이징과 NSIS/portable/ZIP 패키징을 수행했다. Agent [run 38036784977](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38036784977)과 서비스 Broker/CDB provisioning 변경분 [run 38037685741](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38037685741)도 Windows release 컴파일에 성공했다.

| 산출물 | 바이트 | SHA-256 |
| :--- | ---: | :--- |
| `RACP-Client-0.1.21-win-x64-setup.exe` | 395362766 | `cbffa57a6b2d1127d0980118d6442e7b90967fefd72cf971a852930d67959691` |
| `RACP-Client-0.1.21-win-x64-portable.exe` | 1194188922 | `1230435cb3b272db3b02271067413624e112f624519e4944c10e294bdb417525` |
| `RACP-Client-0.1.21-win-x64.zip` | 543340387 | `4a2615d5369141c6b6a1583a23e451141566f74bed567cbaa82ce66feb731b06` |

WebView2 CAB SHA-256: `e8f55a4bde27c7f82512402b56a58539b5ec8928be4e500e077b6f66c9ef4668`. 기록은 해당 소스에 한정하며 이후 변경분은 다음 빌드로 구분한다. 이전 실패 묶음과 다른 버전 산출물은 보존한다.

> [!IMPORTANT]
> 사용자 지시에 따라 테스트만 보류한다. 이 빌드에서는 자동 테스트, GUI smoke, 실제 설치/업데이트/제거 및 2-PC acceptance를 실행하지 않았다. 구현 완료 판단과 테스트 통과를 혼동하지 않는다. 패키지는 unsigned 개발용이며 macOS/Linux 네이티브 빌드는 보류한다. 클라이언트 배포에는 CPython/Node/Electron이 필요 없다. Gateway/CLI 서버 측 Python은 유지한다.


<a id="rust-client-implementation-0121"></a>
## v0.1.21 전체 Rust/Tauri 구현 반영 (2026-10-10)

### 구현 범위와 구 코드 제거

Client와 내장 Agent의 전체 전환 구현을 반영했다. React 화면은 유지하며 호스트는 Tauri/Rust를 사용한다. 기존 100-operation registry, bridge/wire/schema, 보호된 credential과 로컬 정책·워크스페이스·저널·Artifact 계약을 유지한다. 파일·셸·프로세스·메모리·ConPTY·CDP 브라우저, OS 관측, 클립보드·Win32 Broker/UIA/입력·독립 Guardian, SCM·사용자 로그온 등록, GDB/Ghidra/CDB 및 native/proxy carrier를 Rust로 연결했다.

`apps/agent` Python 코드, `apps/client` Electron 호스트와 Python 설치 도우미, Python client build/staging/portable helper, 이 코드에 직접 의존하던 구 테스트를 제거했다. Gateway·CLI·공통 서버 Python 패키지는 유지한다. 서버/CLI의 frozen lock은 66개 패키지로 정리했다. 구 Python in-process Agent fixture는 명시적 보류로 바꾸었으며, 이 skip을 회귀 통과로 계산하지 않는다. 자동 테스트 재개와 새로운 native acceptance 작성·실행은 이번 범위에서 보류한다.

기존 프로필 탐색과 모호한 상태 거절, DPAPI와 소유권 확인, native 연결 파일 선택, 시작 등록, installer maintenance/backup, 포터블 프로필 격리 및 고정 WebView2를 구현했다. [ADR-0030](../adr/ADR-0030-rust-tauri-client-and-native-agent.md)이 구 Electron/Python 배포 결정을 대체한다. 실행·서비스 등록과 빌드는 [Rust Agent 가이드](../guides/rust-agent-guide.md)를 따른다.

### 독립 리뷰와 수정

독립 전체 브랜치 리뷰에서 확인한 두 중요한 오류를 수정했다. SCM callback은 Tokio runtime 내부에서 Agent를 생성한다. 최초 Gateway lease 발급 전에는 자원을 만료시키지 않으며, 만료 시 새 실행을 차단하고 취소된 작업을 기다린 뒤 실행 자원을 회수한다. Desktop 등록기는 Agent 종료까지 유지하되 유효하지 않은 연결에서 새 Broker 등록을 거절한다. 재연결 시 foreground Broker 또는 사용자 로그온 등록을 복구한다. Broker 종료까지 조회 권한을 유지하고, 실제 provider 및 인증된 Broker 상태를 Agent 상태에 반영한다. 클립보드 session 검사에 대한 초기 리뷰 우려는 published schema를 확인한 뒤 철회했다.

테스트는 보류했으므로 이 수정의 실제 SCM 시작·연결 유실·입력 해제 동작이 실기로 검증됐다고 주장하지 않는다.

### production 확인 근거

- 최신 코드 `ae53acab6ac776a85ab9d54847e7077ffeeffa55`: Windows Agent release [38051585594](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38051585594) PASS; Windows frozen dependency/Mypy/server-wheel와 Rust format [38051585556](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38051585556) PASS.
- 앞선 전체 Windows 패키징 `654a0df` [38039857220](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38039857220) 및 legacy 제거 소스 `51fe4f1` [38040154914](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38040154914) PASS. NSIS setup, portable EXE, ZIP과 파일 해시·소스 기록을 생성했다. 이후 리뷰 수정분의 전체 패키지는 별도 source/build ID로 확인한다.
- 복구된 Linux 작업 환경: production `cargo check --workspace --bins --locked`, workspace/Tauri `cargo fmt --check`, `uv lock --check`, frozen sync, 전체 Ruff lint, 수정된 Python 7파일 format, Windows 대상 Mypy 95소스 및 React/TypeScript production build PASS. Linux desktop native 패키지는 빌드하지 않았다.

### 실행 정책과 판단 범위

사용자는 테스트만 보류하고 전체 구현·구 코드 제거·`master` 커밋·푸시를 명시했다. 이를 따라 기존 테스트 후 삭제 조건을 대체했다. 단순 build/format/type/packaging 확인을 기능 동등성·100개 RPC 실기 통과로 표기하지 않는다. Gateway 전체 검증을 새로 실행하지 않았다. 루트 Python format의 기존 서버/테스트 20파일 차이와 Linux 기본 Mypy의 Windows-only symbol 5오류는 별도 기준선 문제로 남기고, 수정된 migration 파일과 Windows 대상 검사 결과를 구분한다.

패키지는 unsigned 개발용이다. 자동 테스트, GUI/native smoke, clean install·upgrade/uninstall, 실제 2-PC, macOS/Linux native 빌드는 보류한다. 클라우드 단절 중 로컬 수정은 stash로 보존한 뒤 원격에서 재구성한 커밋을 fast-forward했다. 기존 산출물과 사용자 상태를 유지하며, 재시도에도 버전 0.1.21을 재사용한다.


<a id="rust-client-master-build-0121"></a>
## v0.1.21 master 최종 빌드와 전달 (2026-10-10)

전체 구현과 legacy 제거, 독립 리뷰 수정 및 동시 변경 보존을 완료하고 `master`에 fast-forward로 커밋·푸시했다. 실제 빌드 소스는 `df533e2861df6f69c41c11f825b7e478d5abd52f`다. 아래 후속 기록은 문서만 변경하며 실행 코드·lock·버전은 그대로다.

| 확인 | 실행 | 결과 |
| :--- | :--- | :--- |
| Windows Client production/NSIS/portable/ZIP | [38051887473](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38051887473), build ID `38051887473-1` | PASS, 18분 31초 |
| Windows Rust Agent release/Chromium ZIP | [38051887491](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38051887491) | PASS |
| Windows frozen dependency/Mypy/server wheel/Rust format | [38051887485](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38051887485) | PASS |

[Client 설치형·포터블 패키지 다운로드](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38051887473/artifacts/11670720903)에는 다음 세 산출물과 `build-manifest.json`이 들어 있다.

| 산출물 | 바이트 | SHA-256 |
| :--- | ---: | :--- |
| `RACP-Client-0.1.21-win-x64-setup.exe` | 395658589 | `8f6f309a6d758d15d38f40cec90dee75a2fa74adca9f8d766e01377f0fd1a9db` |
| `RACP-Client-0.1.21-win-x64-portable.exe` | 1194465545 | `55f899e3bbed3a4ad6b235752c03766efb471979f917619624fb48e958dfb865` |
| `RACP-Client-0.1.21-win-x64.zip` | 543463885 | `4598f9ffe80b6e26de91aabc13b8a3962baf46e436a9edb2452774f7eb22cf93` |

[Windows Agent 다운로드](https://github.com/tlsdbcjs/Remote-Desktop-Commander/actions/runs/38051887491/artifacts/11669564667)에서 브라우저 기능까지 실행하려면 Chromium을 포함한 내부 `racp-agent-0.1.21-win-x64.zip`을 사용한다. 내부 ZIP SHA-256은 `c8c92e00ed30a17550038d0967f600f8e0c46edbf2f0c4efa612a8518e589080`이다. Actions 외부 archive SHA-256과 구분한다: Agent 외부 archive `14ccd525a8e050a8a10dd8bd955c608357fe924ee9eb974e74ac16c28f9993a0`, Client 외부 archive `ac0b0d4cfa4dff0f4db0f44caf4a50f4e8e1c838cf9b7e9c8c247451611399b9`.

최종 Client manifest는 Tauri/Rust, CPython·Node 불필요, unsigned 개발용, Chromium revision `1243`, fixed WebView2 `154.0.4258.62`를 기록한다. CAB SHA-256은 `e8f55a4bde27c7f82512402b56a58539b5ec8928be4e500e077b6f66c9ef4668`이다. Cargo/타우리/pnpm lock SHA-256도 manifest에 포함되어 있다.

사용자 지시에 따라 자동 테스트·native/GUI smoke·실제 설치/업데이트/제거·2-PC acceptance는 실행하지 않았다. 테스트 job은 SKIPPED이며 이 빌드를 기능 동등성 통과로 판정하지 않는다. 실제 Agent 실행 절차는 [Rust Agent 가이드](../guides/rust-agent-guide.md)를 따른다.

### 구현 중 결정 원장

완료한 계획의 임시 원장을 정리하기 전에 `Ruling:` 항목을 발생 순서대로 보존했다. 초기 결정을 대체한 사용자 지시도 함께 남긴다. 실행 중 독립 리뷰의 Minor 보류 항목은 없었다.

1. Ruling: Use the task's dedicated managed /workspace checkout on non-main branch work — no shared user checkout or main branch exists here — cost if wrong: branch isolation only.
2. Ruling: Cloud skill scripts are not filesystem-backed; use equivalent git-ignored ledger and task briefs from approved plan — no dependency on inaccessible bundled scripts — cost if wrong: bookkeeping only.
3. Task 1: Ruling: Keep validated serde_json Values at protocol boundaries rather than hand-duplicating 78 typed payload enums — published schemas plus strict integer and relational validators prevent drift — cost if wrong: compile-time payload checks are replaced by runtime boundary validation.
4. Task 1: Ruling: Fixture output must use validate_payload, not bare model_dump — filesystem copy/move intentionally remove a null destination workspace — no protocol change.
5. Ruling: Create a draft PR to obtain the required Windows native evidence using repository WRITE permission — Linux cannot establish DPAPI correctness — cost if wrong: reversible remote review branch and CI runs, no merge/publish.
6. Task 3: Ruling: Use a per-process retention-clock identity and Instant; legacy clocks are conservatively reset for a full interval instead of assuming Python/Rust monotonic epochs match — preserves minimum retention across upgrades — cost if wrong: retention may be extended on restart, never shortened.
7. Ruling: User explicitly defers all test execution/addition and prioritizes Agent Rust implementation plus an actual Windows Agent build. Continue implementation and production-target compilation, defer automated/native acceptance (including CI tests); do not claim tested parity or delete legacy providers until real acceptance later. Existing v0.1.11 batch version is reused.
8. Ruling: Latest user requires ALL migration implementation and legacy removal while deferring tests; this overrides prior test-before-deletion gate. Implement and compile every provider/host/package, remove client Python once references are migrated; acceptance remains deferred, never claim tested parity.
9. Ruling: Final integration/push target is existing master per user correction, not main. Preserve concurrent master c0fe694 changes via merge, including 100-operation registry and granular permissions. Incoming SSOT 0.1.20 supersedes previous 0.1.11; unified integration batch is 0.1.21, reused for all build retries.
10. Ruling: During cloud outage, preserve local dirty source and reconstruct authorized updates through GitHub commits/Windows CI. On recovery, stash local edits and fast-forward to4fc1256; reconstructed source contains the changes plus ownership safeguards. Cost if wrong: retained stash supports recovery.
11. Ruling: Native Windows is the supported production build target; Linux default Mypy reports5 existing Windows-only symbol errors, while --platform win32 and WindowsCI pass95sources. Root Python format reports20 preexisting retained server/test formatting differences; changed migration Python7files are formatted. Do not expand this migration into unrelated Gateway formatting or test work. Cost if wrong: preexisting Linux lint/format limitations remain visible.
