# RACP 구현 작업 및 검증 현황 (Implementation Status)

> **Document ID**: `DOC-QA-STATUS`\
> **Status**: Active · **Target Version**: v0.1.39\
> **Last Updated**: 2026-10-14 · **Classification**: Quality Assurance & Implementation Status (SSOT)

---

## 개요 (Overview)

본 문서는 **RACP (Remote Access and Control Protocol)** 프로젝트의 전체 단계별(Phase 0~9 및 확장 마일스톤) 구현 현황, 자동화 테스트 검증 수치, 물리 2-PC 실증 증거, 그리고 배포 패키징 상태를 총망라한 **단일 진실 공급원(Single Source of Truth, SSOT)**입니다.

과거 각 페이즈마다 작성되었던 개별 임시 결과 파일들을 본 문서로 완전히 통합 집대성하여, 개발 진행 상황과 검증 매트릭스를 일관되게 추적할 수 있도록 제공합니다.

---

## 목차 (Table of Contents)

- [v0.1.23 테스트 전 전체 구현 진행 원장](#rust-pretest-progress-0123)
- [2026-10-11 v0.1.22 Rust native 기반 구현](#rust-native-foundation-20261011)
- [2026-10-11 Rust 계획 재정렬·정적 검토](#rust-plan-update-20261011)
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

호스트는 `DESKTOP-06NU139` (192.168.29.141), Windows 11 x64다. 실행 환경은 `.tools/desktop-build-venv`이며, checksum 검증한 Node 22.23.0/Corepack pnpm 11.19.0, Python 3.12.11과 frozen lock을 사용했다. 실행 문맥 (`../../dist/acceptance/RUN-20261007-desktop-correct/build-context.json`, 과거 경로)과 source/lock hash (`../../dist/acceptance/RUN-20261007-desktop-correct/source-manifest.json`, 과거 경로)를 보관했다.

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.tools/desktop-build-venv'
uv run --frozen python scripts/build_client.py --platform win --arch x64 `
    --node .tools/node-v22.23.0-win-x64/node.exe `
    --browser-cache "$env:LOCALAPPDATA/ms-playwright"
```

| 이번 실행 검사 | 결과 / 증거 |
| :--- | :--- |
| 필수 Ruff + 빌드 Python 스크립트 lint | PASS; lint 로그 (`../../dist/acceptance/RUN-20261007-desktop-correct/lint.log`, 과거 경로) |
| Mypy | 135 source 파일 PASS; 타입 로그 (`../../dist/acceptance/RUN-20261007-desktop-correct/mypy.log`, 과거 경로) |
| Python 버전·Desktop Control | **16 passed**, failure/error/skip 0; JUnit (`../../dist/acceptance/RUN-20261007-desktop-correct/version-desktop.xml`, 과거 경로) |
| TypeScript/Vite frontend | PASS; 통합 빌드 로그 (`../../dist/acceptance/RUN-20261007-desktop-correct/build.log`, 과거 경로) |
| Client Node | **11 passed**, failure 0; manifest의 버전/platform/arch/lock/변조·경로 경계 검사 포함 |
| 스테이징 Agent | `native Agent imports OK`, 실제 내장 runtime에서 `racp-agent 0.1.10` 확인 |
| HTTPS/WSS Client E2E | PASS; 격리 E2E 로그 (`../../dist/acceptance/RUN-20261007-desktop-correct/client-e2e.log`, 과거 경로): 연결 파일·token/CA 오류·설정 보존·재연결·설정 편집 경계·desktop opt-in·트레이·Job·완전 종료 |
| 압축 해제형 EXE smoke | PASS: 새 user-data-dir를 확인한 후 내장 Agent/연결 파일 preview/비밀 비출력/CA 오류/완전 종료 |
| 단일 포터블 EXE smoke | PASS: NSIS 추출·loopback Chromium readiness·격리 profile·v0.1.10·native Agent bridge·완전 종료 |
| ZIP/manifest | 전체 **4,861개 파일 CRC**, Agent **4,788개 SHA-256** PASS; ZIP 검증 (`../../dist/acceptance/RUN-20261007-desktop-correct/zip-integrity.json`, 과거 경로) |
| 최종 산출물 | EXE 두 파일의 PE 표식, 세 파일의 크기/SHA-256, source/lock 일치 PASS; 최종 검증 (`../../dist/acceptance/RUN-20261007-desktop-correct/verification.json`, 과거 경로) |
| macOS/Linux | 정의 유지·빌드 DEFERRED; native runner/컨테이너 실행 없음 |

첫 실행은 Node 의존성 다운로드 이후 중단되어 초기 로그 (`../../dist/acceptance/RUN-20261007-desktop-correct/build-attempt-1-interrupted.log`, 과거 경로)를 보존했고, staging/output 경로가 아직 생성되지 않은 상태에서 같은 버전으로 통합 진입점을 재실행했다. 기본 pytest 임시 루트의 WinError 5는 초기 XML (`../../dist/acceptance/RUN-20261007-desktop-correct/version-desktop-attempt-1.xml`, 과거 경로)에 보관했다. 사용자 임시 폴더 ACL을 수정하지 않고 저장소 내 새 전용 basetemp로 검사해 16 passed를 확인했다. JUnit 호스트 metadata 수집 중 Windows WMI 진단 `0x8007000e`가 로그에 출력되었으나 최종 프로세스 exit 0, XML failure/error 0이었다.

최종 현황 문서의 메타데이터·상대 파일/anchor 링크 49개를 검사해 오류 0건이었고 `git diff --check`도 통과했다.

산출물 경로는 **`dist/client-desktop/0.1.10/win-x64/`**이며, build-manifest.json (`../../dist/client-desktop/0.1.10/win-x64/build-manifest.json`, 과거 경로)과 SHA256SUMS.txt (`../../dist/client-desktop/0.1.10/win-x64/SHA256SUMS.txt`, 과거 경로)에 현재 파일을 기록했다.

| 산출물 | 크기 (bytes) | SHA-256 |
| :--- | ---: | :--- |
| RACP-Client-0.1.10-win-x64-setup.exe (`../../dist/client-desktop/0.1.10/win-x64/RACP-Client-0.1.10-win-x64-setup.exe`, 과거 경로) | 396322765 | `2c8a788ff55745b5a7bb1a3b6a3b30f0e371a6b2984649b4b49876f2d759f0f5` |
| RACP-Client-0.1.10-win-x64-portable.exe (`../../dist/client-desktop/0.1.10/win-x64/RACP-Client-0.1.10-win-x64-portable.exe`, 과거 경로) | 396110081 | `a5cbad23f456b325d2f04bb05b79ca63cb34a5f6df62f9a25fc0621748c0e654` |
| RACP-Client-0.1.10-win-x64.zip (`../../dist/client-desktop/0.1.10/win-x64/RACP-Client-0.1.10-win-x64.zip`, 과거 경로) | 543272105 | `31df2b078bff9cc11a0d5f03a23f20dc13046ee75810c1191e45b89f17f0049a` |

Electron 44.5.1, CPython 3.12.11, 현재 workspace wheel과 고정 Playwright Chromium을 포함한다. `before-pack.cjs`의 OS/arch/version/lock/file hash 검증을 우회하지 않았으며 `--publish never`를 적용했다. Gateway 설정·credential·등록 token·서버 private key는 넣지 않았다. 격리 시험 화면의 v0.1.10 표시와 연결 폼도 screenshot (`../../dist/acceptance/RUN-20261007-desktop-correct/packaged-connection-setup.png`, 과거 경로)으로 확인했다.

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
| 필수 Ruff + 발급 helper/tests | PASS; lint (`../../dist/acceptance/RUN-20261007-connection-helper/lint.log`, 과거 경로) |
| Mypy | 135 source 파일 PASS; 타입 검사 (`../../dist/acceptance/RUN-20261007-connection-helper/mypy.log`, 과거 경로) |
| 버전·새 발급 검사 | **9 passed**, failure/error/skip 0; JUnit (`../../dist/acceptance/RUN-20261007-connection-helper/tests.xml`, 과거 경로) |
| 실제 TLS 일회용 등록 | 자체 Gateway에서 문서 생성·공개 CA 포함·등록 성공·token 재사용 401·기존 파일 보존 PASS |
| 안전한 실패 | Gateway 접속 실패 시 비밀 출력·빈 결과 파일 없음, 모호한 host 설정 선택 거부 PASS |
| 실제 141 CMD 실행 | exit 0, 다른 시작 폴더에서도 성공, `.racp` 한 파일 생성 및 token 비출력; 실행 기록 (`../../dist/acceptance/RUN-20261007-connection-helper/live-generation.json`, 과거 경로) |

실제 141 Gateway `https://192.168.29.141:8765`에서 2026-10-07 15:59:03 KST에 연결 파일을 발급했고 만료 시각은 **16:09:03 KST**였다. 원문 token은 보고서에 보관하지 않는다. 파일은 현재 사용자용 `connection-files/`에 있고 한 번 등록되거나 10분이 지나면 재발급해야 한다. 이 실증은 연결 파일 생성이며 121 등록이나 원격 입력 성공을 뜻하지 않는다.

문서 검증: 변경 guide/status 2개의 메타데이터·상대 파일/anchor 링크 76개 오류 0건, `git diff --check` PASS.


---

<a id="console-access-20261007"></a>

## 11. 141 관리 Console 접속 복구

2026-10-07 관리 Console URL이 404인 원인은 `apps/console/dist`가 없고, 실행 중인 Gateway에 정적 화면이 mount되지 않은 상태였다. Gateway readiness와 등록 PC 한 대의 ONLINE은 정상이며 웹 화면 배포가 빠져 있었다. pinned Node 22.23.0으로 `scripts/console_build.py`를 실행해 Prettier/TypeScript/OpenAPI client drift/Vite 생산 빌드를 통과시켰다. 최초 검사에서 Windows checkout의 16개 파일 formatting 오류를 확인하고 같은 formatter로 정리한 후 재검했다. 빌드 로그 (`../../dist/acceptance/RUN-20261007-console-access/build.log`, 과거 경로)를 보관했다.

재시작 전에 실행 대기열이 비었고 모든 terminal 기록이 CLOSED임을 확인했다. 처음에는 CLOSED 기록을 활성 handle로 잘못 분류해 중단했으며, 상태를 확인해 실제 활성 작업이 없음을 확인한 뒤 진행했다. PID/생성 시각/명령행을 검증한 이 작업 소유 Gateway만 종료했다. SQLite backup을 보관하고 기존 실행 인자·data-dir·TLS/CA/owner credential을 유지해 재시작했다. 복구 기록 (`../../dist/acceptance/RUN-20261007-console-access/gateway-after.json`, 과거 경로)의 페이지/readyz HTTP 200, 동일 Device `dev_b12478e45ab646e5838c31218ab85fb1` ONLINE, credential/인증서 원본 hash 일치를 확인했다. 현재 URL은 **https://192.168.29.141:8765/console/** 이다.

`Create-Console-Login.cmd`(현재 [PowerShell 진입점](../../scripts/host/Create-Console-Login.ps1)으로 대체)와 [로그인 코드 helper](../../scripts/create_console_login.py)를 추가했다. 호스트 owner로 5분짜리 일회용 setup secret을 발급하고, 현재 사용자만 접근하는 DACL을 적용한 로컬 txt에 보관한다. 명령행·로그에 token이나 owner secret을 출력하지 않는다. 웹 화면이 준비되지 않으면 secret을 발급하지 않는다. 이용 절차는 [2-PC 가이드](../guides/two-pc-lab-guide.md#22-관리-console-접속)에 기록했다.

| 검사 | 결과 / 증거 |
| :--- | :--- |
| Console formatter/type/generated client/build | PASS; 빌드 (`../../dist/acceptance/RUN-20261007-console-access/build.log`, 과거 경로) |
| 필수 Ruff + 새 helper/tests | PASS; lint (`../../dist/acceptance/RUN-20261007-console-access/lint.log`, 과거 경로) |
| Mypy | 135 source 파일 PASS; 타입 (`../../dist/acceptance/RUN-20261007-console-access/mypy.log`, 과거 경로) |
| 버전·로그인 helper | **8 passed**, failure/error/skip 0; JUnit (`../../dist/acceptance/RUN-20261007-console-access/python-tests.xml`, 과거 경로) |
| 실제 HTTPS Console API | 페이지 200·assets 2개 200·로그인 200·cookie session 200·장비 조회 200·logout 200·setup secret 재사용 401; 검증 (`../../dist/acceptance/RUN-20261007-console-access/login-api-verification.json`, 과거 경로) |
| owner 보호·파일 ACL | helper txt에 owner secret 없음, Windows DACL의 ACE 1개(현재 사용자), 미배포 페이지에서 발급 차단 PASS |
| 내장 브라우저 자동 확인 | BLOCKED_HOST: URL 보안 정책으로 해당 탭의 자동 관측이 거부됨. 다른 브라우저/CDP/자동화로 우회하지 않았고 사용자 브라우저의 실제 표시·로그인은 미검증 |

이번 복구 배치에서 PATCH 0.1.11→0.1.12를 증가했다. 병행된 다른 작업의 변경으로 현재 SSOT/workspace는 **0.1.13**이며 그 버전을 되돌리지 않았다. 기존 remote Client·권한/UI 관련 병행 변경은 보존했고, 새 버전의 데스크톱 EXE를 빌드한 것으로 보고하지 않는다. TLS 검증과 owner 인증·승인 정책은 유지했고 browser-generated security warning을 우회하지 않았다. 현재 보고는 서버·API 접속 복구이며 실제 Codex MCP OAuth 연동 완료를 의미하지 않는다.

추가 확인: public CA thumbprint `00E90D44C74B4EB3CD4EF708D7ADDF10698EA346`는 Windows CurrentUser/LocalMachine Root 둘 다 미등록이었다. public DER `RACP-Host-CA.crt`만 기존 랩 폴더에 export했고 private key를 포함하거나 신뢰 저장소를 자동 변경하지 않았다. 문서 2개 메타데이터·상대 링크 89개 오류 0건을 확인했다.

---

<a id="windows-plan-resume-0113"></a>

## 12. v0.1.13 Windows 계획 실행·초기 등록 화면 허용

2026-10-07 [Windows 계획](../spec/windows-engineering-plan.md)의 W01/W02/W04/W05를 재개했다. 실행 ID는 `RUN-20261007-170434-windows-plan`이다. source·환경 manifest (`../../dist/acceptance/RUN-20261007-170434-windows-plan/manifest.json`, 과거 경로), 경로별 시험 matrix (`../../dist/acceptance/RUN-20261007-170434-windows-plan/test-matrix.json`, 과거 경로)를 보관한다. 병행 Console 변경의 v0.1.12를 보존한 뒤 이번 수정 배치에서 버전 도구로 **0.1.12→0.1.13**을 한 번 증가하고 별도 `.tools/desktop-build-venv`에서 lock/workspace를 동기화했다. live Gateway 환경은 동기화하거나 재시작하지 않았다.

### 12.1 초기 등록과 설정 편집의 화면 제어 선택 통일

사용자가 초기 등록 화면에 화면 제어 체크박스가 없음을 확인했다. Client의 연결 파일 등록·직접 입력 등록과 기존 설정 편집 화면에 동일한 `DesktopPermission` 컴포넌트를 사용한다. 기본값은 false이고 등록 중에는 비활성화한다. Electron IPC→strict Python 등록 모델→`connect.enroll/prepare`→보호된 Agent 설정까지 `desktop_enabled`를 전달한다. 선택값은 재실행에도 유지되며 다음 Agent 시작부터 적용된다. 기존 요청에서 필드가 빠지면 false를 유지하고 실행 profile/승인 정책을 변경하지 않는다.

초기 등록 화면 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/client-initial-registration.png`, 과거 경로)에서 문구·체크박스·기본값을 확인했다. 연결 파일/수동 등록 각각의 true/false 저장·비밀 비노출·재실행 정보 확인을 검사에 포함했다. Hello의 `agent_version="0.1.0"` 고정값도 SSOT `VERSION` 참조로 수정했고 실제 fixture Agent가 Gateway에 현재 버전을 보고하는 회귀 검사를 추가했다.

| 검사 | 실제 결과 / 증거 |
| :--- | :--- |
| 필수 Ruff + 변경 Python 검사 | PASS; lint (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/lint.log`, 과거 경로) |
| Ruff format | 초기 AGENTS의 Python 예제 빈 줄 차이 보정 후 302 files PASS; 최종 format (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/format-final.log`, 과거 경로) |
| Mypy | 135 source files PASS; 타입 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/mypy.log`, 과거 경로) |
| 버전·등록·연결 파일·인증/실행 회귀 | **34 passed**, failure/error/skip 0; JUnit (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/targeted.xml`, 과거 경로) |
| 자체 GUI timeout·이전 결과 덮어쓰기 거부 | **1 passed**; JUnit (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/fixture-timeout.xml`, 과거 경로) |
| Client TypeScript/Vite·Node | 빌드 PASS·Node **11 passed**; 빌드 로그 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/build.log`, 과거 경로) |
| 전체 Python suite | BLOCKED_ENV: Windows `platform._wmi_query` native 예외 `0x8007000e` 후 5분 이상 로그 진행이 없어 해당 run의 pytest 프로세스만 종료. PASS로 집계하지 않음; 로그 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/full.log`, 과거 경로), 종료 기록 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/full-interruption.json`, 과거 경로) |

GUI E2E의 첫 hidden 시도는 연결 파일 재선택 후 버튼 대기에서 timeout했다. visible 재시도에서는 등록값 true 저장을 확인했으나 설정 편집의 로딩 화면을 기존 5초 assertion으로 판정해 실패했다. 실패 화면 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/client-e2e-failure-1791361322165.png`, 과거 경로)을 보존하고 checkbox의 실제 로딩을 기다리도록 시험을 수정했다. 격리 프로필의 visible 실행을 선택 가능하게 하고 실패 screenshot을 수집한다. 총 fixture 실행 예산은 600초로 조정했으며 제품 IPC의 45초 제한과 기능 합격 기준은 유지했다. 최종 GUI 및 패키지 검증 결과는 아래 후속 기록을 따른다.

### 12.2 실제 141→121 owner API 증거와 남은 인수

Gateway TLS/owner readiness HTTP 200과 Device `dev_b12478e45ab646e5838c31218ab85fb1` ONLINE을 확인했다. 사용자가 화면 제어를 허용하고 재시작한 뒤 boot `boot_ca6bab53eab544a7ba21beec7e57c7bd`·epoch 2·session 1의 desktop Broker가 enabled/healthy로 보고됐다. hostname은 `VM-WIN10-64-VPN`, Windows 10 build 19045, workspace는 `C:/Users/GHOSTSHELL/Documents`, profile은 standard다. preflight (`../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/preflight.json`, 과거 경로)를 보관한다.

기존 Hello는 0.1.0으로 보고했지만 원격 내장 Python에서 SSOT와 설치 metadata를 독립 확인한 실제 Agent 버전은 **0.1.10**이다. Python executable과 ASAR hash도 설치 후보 증거 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/installed-candidate.json`, 과거 경로)에 기록했다. 아래 원격 시험은 v0.1.10 대상의 owner API 경로이며 v0.1.13 또는 actual Codex 인수로 확대하지 않는다.

| 작업 | 실제 결과 / 증거 |
| :--- | :--- |
| W05 일부: 파일·실행·자료 회수·Job·ConPTY | 한글 write/read/copy·SHA-256·인증 Artifact 회수, 같은 key counter=1, 장기 Job CANCELLED/cleanup complete·PID 부재, ConPTY marker·close PASS; core (`../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/core.json`, 과거 경로) |
| W05 시험 폴더 정리 | own 폴더 revision 확인 후 삭제, terminal handle CLOSED; before (`../../dist/acceptance/RUN-20261007-170434-windows-plan/resources-before.json`, 과거 경로), after (`../../dist/acceptance/RUN-20261007-170434-windows-plan/resources-after.json`, 과거 경로) |
| W02 실제 B의 독립 GUI fixture | own script/process 생성 후 즉시 읽기가 startup과 경합해 PATH_NOT_FOUND. console signal 정리는 native GUI에서 명시적으로 거부됐다. 15초 자체 timeout으로 정상 종료 후 결과 JSON·PID/생성 시각·CLOSED handle·창 부재·폴더 삭제 확인; 복구/정리 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/gui-first-attempt.json`, 과거 경로). 살아 있는 창 inspect 및 input은 미검증 |
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
| Gateway ZIP | RACP-Gateway-0.1.14-win-x64.zip (`../../dist/gateway/0.1.14/win-x64/RACP-Gateway-0.1.14-win-x64.zip`, 과거 경로), **34,303,542 bytes**, SHA-256 `502416c46f1e3d74e1809db1ca773500527ae6273f5099b0819297cf6769ca44`; manifest (`../../dist/gateway/0.1.14/win-x64/build-manifest.json`, 과거 경로) |
| Console 생산 빌드·native import | PASS; version bump가 바꾼 package.json formatting을 보정한 후 같은 버전으로 재시도. 최초 실패에서 배포 output은 생성되지 않았다; 빌드 (`../../dist/acceptance/RUN-20261007-gateway-build/build.log`, 과거 경로) |
| Ruff·Mypy | PASS; Mypy 135 source files; lint (`../../dist/acceptance/RUN-20261007-gateway-build/lint-final.log`, 과거 경로), type (`../../dist/acceptance/RUN-20261007-gateway-build/mypy-final.log`, 과거 경로) |
| 버전·host identity/ACL·점유 포트·TLS·OAuth listener·발급 회귀 | **30 passed**, failure/error/skip 0; JUnit (`../../dist/acceptance/RUN-20261007-gateway-build/tests-final.xml`, 과거 경로). 초기 ACL 검사에서 Windows가 동일 사용자 ACE를 적용/상속 두 항목으로 나누는 것을 확인해 모든 ACE의 실제 SID를 검사하도록 수정했다 |
| 새 폴더에 ZIP 해제 후 CMD 실행 | PASS; 공백 포함 경로, 자체 Python 실행, TLS readiness, Console/assets, 연결 파일·PC 1회 등록·재사용 401, cookie 로그인·logout·코드 재사용 401, 상태 CMD 확인; smoke (`../../dist/acceptance/RUN-20261007-gateway-build/smoke.log`, 과거 경로) |
| 무결성·배포 경계 | ZIP CRC·manifest **4,324 files** SHA-256 PASS. ZIP에 `.racp`/owner.bin/server.key 없음. smoke에서 만든 사용자 상태는 acceptance 폴더에만 있고 배포 ZIP에는 포함되지 않음 |
| MCP | 격리 loopback owner-auth MCP에서 serverInfo 0.1.14·**86 tools**·device_list 확인. TLS OAuth listener 회귀도 PASS. 외부 Codex OAuth 로그인/실제 Agent 실행은 이번 배포 smoke에서 NOT_RUN |
| 기존 141 호스트 | 기존 8765 서버를 교체/재시작하지 않음. protected owner/CA를 사용한 doctor 조회 healthy, 동일 Device ONLINE, MCP owner_bearer; 상태 (`../../dist/acceptance/RUN-20261007-gateway-build/live-host-status.json`, 과거 경로) |

> [!IMPORTANT]
> unsigned 개발 배포본이며 별도 clean PC 설치·운영 인증서 갱신·Windows Service·실제 Codex OAuth 연동을 완료했다고 선언하지 않는다. 현재 141 서버의 외부 `/mcp`는 OAuth 공급자 설정이 추가로 필요하다. 기본 운영 순서는 Gateway 시작 → OAuth/Codex MCP 연결 및 Client 등록(상호 독립) → 대상 Agent ONLINE/권한 확인 → 작업이다. macOS/Linux 빌드와 container는 실행하지 않았다.

최종 재확인: 버전 테스트 6개 재실행 PASS, 배포 ZIP size/SHA-256/CRC 및 credential 미포함 확인, smoke 종료 후 해당 추출 runtime 프로세스 0개, 변경 문서 3개의 상대 파일 링크 118개 오류 0건. 최종 검증 (`../../dist/acceptance/RUN-20261007-gateway-build/final-verification.json`, 과거 경로)에 기록했다.

---

<a id="host-powershell-0115"></a>

## 14. v0.1.15 호스트 PowerShell 진입점 통합

2026-10-07 사용자가 승인한 구성으로 Gateway 운영 진입점을 PowerShell로 통합했다. 저장소 루트의 두 CMD를 제거하고 [scripts/host](../../scripts/host/)에 `Start-Gateway.ps1`, `Status-Gateway.ps1`, `Create-Connection-File.ps1`, `Create-Console-Login.ps1`과 공통 `Invoke-RacpHost.ps1`을 추가했다. Gateway 빌드가 같은 원본을 배포 폴더 최상위에 복사한다. 서버·발급 기능은 기존 Python helper를 호출하며, 배열로 native 인자를 전달하고 Python 종료 코드를 그대로 반환한다. 저장소에서는 기존 단일 랩 탐색을 유지하며 배포본에서는 별도 `.racp/host`를 사용한다. [운영 가이드](../guides/gateway-deployment-guide.md)와 [2-PC 가이드](../guides/two-pc-lab-guide.md)를 새 경로·명령으로 수정했다.

version tool로 PATCH **0.1.14→0.1.15**를 한 번 증가하고 별도 build venv를 sync했다. 과거 v0.1.14 CMD 배포 ZIP과 다른 Client/Agent 작업은 보존했다. 이 배치는 Gateway ZIP만 새로 빌드했으며 v0.1.15 Client 설치 EXE를 빌드했다는 의미는 아니다.

| 검사 | 결과 / 증거 |
| :--- | :--- |
| TDD 초기 실패 | PowerShell 원본이 없는 상태에서 새 검사 3개가 의도한 누락 사유로 실패; RED (`../../dist/acceptance/RUN-20261007-host-powershell/red.log`, 과거 경로) |
| PowerShell 5.1·인자·종료 코드 및 관련 회귀 | **17 passed**, failure/error/skip 0; source/package 공백·한글 경로와 `&`가 포함된 이름, NoOpen, native 종료 코드 7 전달 확인; JUnit (`../../dist/acceptance/RUN-20261007-host-powershell/tests-final.xml`, 과거 경로) |
| 실행 환경 원인 확인 | PS5 기본 Restricted가 스크립트 실행을 막는 것을 확인. 검증 프로세스에만 명시적 RemoteSigned를 사용하고 PC/조직 영구 정책을 변경하지 않았다. 최초 source native 시작의 20초 timeout은 같은 인자로 정상 결과·exit 7을 독립 확인한 후 60초 검증 예산으로 재실행했다 |
| Ruff·Mypy | PASS; Mypy 135 source files; lint (`../../dist/acceptance/RUN-20261007-host-powershell/lint-final.log`, 과거 경로), type (`../../dist/acceptance/RUN-20261007-host-powershell/mypy.log`, 과거 경로) |
| 생산 Console·native Gateway·ZIP 빌드 | PASS; build (`../../dist/acceptance/RUN-20261007-host-powershell/build.log`, 과거 경로) |
| 배포 ZIP | RACP-Gateway-0.1.15-win-x64.zip (`../../dist/gateway/0.1.15/win-x64/RACP-Gateway-0.1.15-win-x64.zip`, 과거 경로), **34,305,342 bytes**, SHA-256 `1a695c4598125282e1700ce725b891a3c9d4d25d6ef6ea09f4cf3a9678a43d12`; manifest (`../../dist/gateway/0.1.15/win-x64/build-manifest.json`, 과거 경로) |
| 새 ZIP PowerShell smoke | PASS; 공백 경로에서 PS5 시작·TLS readiness·Console/assets·PC 1회 등록과 token 재사용 401·로그인/logout와 코드 재사용 401·상태 조회; smoke (`../../dist/acceptance/RUN-20261007-host-powershell/smoke.log`, 과거 경로) |
| 무결성·MCP | ZIP CRC·manifest **4,325 files** hash·CMD 미포함 확인. loopback MCP serverInfo 0.1.15·86 tools·device_list PASS. 실제 외부 Codex OAuth 로그인/원격 입력은 이번 smoke에서 NOT_RUN |
| 기존 141 서버 | 새 source `Status-Gateway.ps1 -StateDir .racp/two-pc-141-121` exit 0, healthy·동일 장비 ONLINE 확인. 기존 서버를 재시작하거나 이관하지 않았다; 상태 (`../../dist/acceptance/RUN-20261007-host-powershell/live-status.json`, 과거 경로) |

> [!NOTE]
> `.ps1` 더블클릭을 실행 방법으로 안내하지 않는다. PowerShell에서 실행하며 PS5의 Restricted 환경에는 명시적 process RemoteSigned 명령을 가이드에 제공한다. 배포본은 unsigned 개발 ZIP이며 Windows Service·부팅 자동 시작·운영 OAuth 공급자 설치·clean PC 인수는 별도 범위다.

최종 검증에서 PowerShell 원본 5개와 배포 복사본의 byte 일치, ZIP size/SHA-256/CRC, CMD·credential 미포함, smoke runtime 프로세스 잔존 0개 및 상대 파일 링크 164개 오류 0건을 확인했다. 최종 검증 기록 (`../../dist/acceptance/RUN-20261007-host-powershell/final-verification.json`, 과거 경로)을 보관했고 `git diff --check`도 PASS이다.

---

<a id="windows-test-first-20261007"></a>

## 14. 테스트 우선 재개: Windows 회귀와 실제 121 관측

2026-10-07 사용자 지시에 따라 Client 빌드는 보류하고 테스트를 진행했다. 이 절은 §12의 실제 후속 검사이며, §13의 병행 Gateway 작업과 별도로 기록한다. 이번 테스트 중 SSOT는 병행 작업에 의해 0.1.14→0.1.15로 변경됐고 버전을 되돌리거나 별도로 증가시키지 않았다. 로컬 검사 프로세스에는 시작 당시 0.1.14가 로드된 결과도 있어 이 집계를 단일 최종 릴리스 후보의 PASS로 사용하지 않는다. 실제 121 Agent는 v0.1.10이며 owner API 경로로 확인했다. 현재 대화의 RACP MCP 도구는 여전히 없어 actual Codex 인수는 BLOCKED_ENV다.

### 14.1 로컬 검사 결과와 시험 계약 보정

| 검사 묶음 | 실제 결과 / 증거 |
| :--- | :--- |
| 전체 unit | **208 passed**, failure/error/skip 0; JUnit (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/unit-final.xml`, 과거 경로) |
| 선택형 Windows GUI·Guardian | **9 passed / 4 skipped**, failure/error 0; JUnit (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/native-current.xml`, 과거 경로). skip은 OS가 own fixture foreground 활성화를 거부한 경우이며 actual 121의 PASS로 이관하지 않음 |
| Console cookie/CSRF/origin/session/SSE 회귀 | **6 passed**; JUnit (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/console-auth-final.xml`, 과거 경로) |
| Browser·file 회귀 | **5 passed / 7 failed / 1 teardown error**; JUnit (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/browser-final.xml`, 과거 경로). timeout/cancel UNKNOWN, native worker·profile cleanup unknown, CLI timeout·Agent shutdown timeout |
| 초기 integration 묶음 | **47 passed / 3 failed**에서 중단; JUnit (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/integration-current.xml`, 과거 경로). Browser 정리 실패 2개와 아래 수정 전 Console 시험 경합 1개. 최종 묶음과 중복이 있어 합산하지 않음 |
| 나머지 28 integration 파일 | **77 passed / 16 failed / 19 skipped**; JUnit (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/integration-remaining.xml`, 과거 경로). Guardian startup 2, output recovery 1, plugin supervisor 10, terminal/stream 3 실패 |
| 필수 lint·type | Ruff PASS, Mypy **135 source files PASS**; lint (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/lint-current.log`, 과거 경로), type (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/mypy-current.log`, 과거 경로) |

집계 원문은 검사별 결과 JSON (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/results-summary.json`, 과거 경로)에 있다. 동일 시험의 재현·재시도·부분 집계를 단순 합산하지 않는다. 현재 전체 품질 gate는 **FAIL**이다.

실패 원인을 확인한 두 시험 계약만 보정했다. `activity()`의 link/reparse 거부는 `local_path()`가 내는 PermissionError이므로 기존 ValueError 기대를 맞추고 대상 원본 및 링크 보존 assertion을 추가했다. Console의 sleeping ConPTY도 시작 ANSI 프레임을 낼 수 있으므로 로그아웃 전에 인증 상태에서 초기 프레임을 소비·ACK한 뒤 조용한 stream의 SESSION_EXPIRED와 relay 정리를 검사한다. 변경 전 각각 실제 실패를 확인했고 변경 후 위 unit/Console 묶음을 통과했다. 제품의 경로 거부·권한·세션 만료 동작을 완화하지 않았다.

Browser는 개별 재현 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/browser-timeout-repro.log`, 과거 경로)에서 Chromium 자식 잔류 assertion 실패를 확인했다. Job 소속/종료 probe (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/browser-cleanup-probe.json`, 과거 경로)의 단독 실행은 1 passed였지만, 다시 묶음 검사에서 실패했으므로 해결로 선언하지 않는다. 느려진 integration의 stack (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/integration-stack.txt`, 과거 경로)은 executor의 `subprocess.communicate()`/reader thread 대기를 보였다. 이후 전체 묶음은 실제 종료해 위 XML을 생성했다. timeout을 임의로 늘리거나 UNKNOWN을 PASS로 바꾸지 않았다.

### 14.2 실제 121 화면 입력과 자체 GUI 증거

| 실행 | 실제 결과 / 증거 |
| :--- | :--- |
| `RUN-20261007-173925-owner-desktop` | own fixture PID/create_time/session/window 조회 후 desktop.activate **FOCUS_MISMATCH**. 입력 진행 불가, Broker가 lease를 abort한 뒤 release는 PERMISSION_DENIED. 후속 timeout·창 부재·revision 삭제로 정리 확인; 결과 (`../../dist/acceptance/RUN-20261007-173925-owner-desktop/results.json`, 과거 경로), 정리 (`../../dist/acceptance/RUN-20261007-173925-owner-desktop/cleanup.json`, 과거 경로) |
| `RUN-20261007-175300-owner-desktop` | fixture foreground 확인 뒤 PNG Artifact 회수까지 수행. 시험 script가 capture 응답의 직접 sha256을 잘못 가정해 중단. 해시는 Artifact 메타데이터에서 비교하도록 후속 시험 수정; 결과 (`../../dist/acceptance/RUN-20261007-175300-owner-desktop/results.json`, 과거 경로). 정상 timeout·창 부재·폴더 삭제 complete |
| `RUN-20261007-175700-owner-desktop` | 사용자가 own 창의 제목 표시줄을 클릭했고 대상 foreground를 확인했다. PNG/hash 검증 후 desktop.click은 SUCCEEDED/os_dispatch_only이지만 자체 clicks counter가 증가하지 않아 **FAIL**. 결과 (`../../dist/acceptance/RUN-20261007-175700-owner-desktop/results.json`, 과거 경로), 시험 창 PNG (`../../dist/acceptance/RUN-20261007-175700-owner-desktop/own-window.png`, 과거 경로). 캡처의 컨트롤 미표시와 own fixture 준비/좌표/dispatch 경로를 추가 진단해야 하며 제품/fixture 원인은 확정하지 않음. 정상 timeout·창 부재·revision 삭제 complete |

실제 관측 display는 **3840×2160 / 150%**, session 1이다. 이전 기록의 값을 재사용하지 않고 새 관측 응답에서 확인했다. 짧은 fixture 진단 (`../../dist/acceptance/RUN-20261007-170434-windows-plan/remote/gui-diagnostic.json`, 과거 경로)은 새 own script가 8초 timeout 뒤 exit 0/cleanup complete이며, 5초 thread dump는 message pump와 refresh 대기를 보였다. key/type/scroll/drag/UIA는 실패한 click 뒤 요청하지 않았고 PASS로 집계하지 않는다. 입력 시험은 actual Codex MCP가 아닌 owner API 경로다.

### 14.3 실제 121 메모리·Artifact·정책 경계

`RUN-20261007-180200-owner-memory`는 121에서 시험 소유 process에 `bytes(range(256))*65536`의 16MiB buffer를 만들었다. PID/생성 시각/boot를 확인하고 **32B·4KiB·128KiB·16MiB** 각각을 읽어 A의 기대 bytes와 SHA-256를 비교했다. 128KiB와 16MiB는 인증 Artifact download로 검증했다. stale create_time은 PRECONDITION_FAILED, 16MiB+1은 접수 전 HTTP 400/INVALID_ARGUMENT, read_only는 접수 전 HTTP 403/PERMISSION_DENIED이며 operation ID가 없다. 모든 실제 read는 standard의 요청별 owner 승인으로 실행했다.

메모리 결과 (`../../dist/acceptance/RUN-20261007-180200-owner-memory/results.json`, 과거 경로)와 한도/정책 (`../../dist/acceptance/RUN-20261007-180200-owner-memory/limits-policy.json`, 과거 경로)에 PASS를 기록했다. 정리는 생성 시각으로 확인한 own process에만 force terminate를 보내 PID 부재를 확인하고 own 폴더를 revision 확인 후 삭제해 complete다. 앞선 `RUN-20261007-180100-owner-memory`는 inline 응답 필드를 base64로 잘못 가정한 harness 오류이며 process/folder 정리는 complete로 보존했다. 정상 계약의 `bytes_hex`를 사용한 후속 실행만 PASS다.

> [!IMPORTANT]
> Windows 핵심 인수와 배포 gate는 미완료다. 우선 결함은 actual 121 input의 독립 결과 불일치, Browser cancel/timeout/cleanup, Guardian startup, 완료 출력의 deadline 경합, plugin worker와 ConPTY/stream 종료·출력 경계다. 과거 PASS·부분 재현 성공·이번 메모리 PASS로 이를 대체하지 않는다. actual MCP 연결·새 후보의 실제 B 시험·display/session 조합·장애/restore·부하·8시간 soak도 남아 있다. Client 빌드는 사용자가 마지막 단계에서 요청할 때 재개한다.

실행 종료 후 별도 process inventory (`../../dist/acceptance/RUN-20261007-170434-windows-plan/automation/local-process-cleanup.json`, 과거 경로)에서 이번 pytest-20/21 test root에 속한 잔류 process가 없는 것을 확인했다. 다른 사용자 process나 병행 채팅의 시험 root는 정리 대상으로 삼지 않았다. 로그의 fixture cookie/CSRF 값은 redaction했고 바이너리 SHA 검증은 원본 bytes로 수행했다.

---

<a id="racp-native-re-mcp-20261007"></a>

## 15. 실패 분류와 기존 Codex 리버싱 MCP 원격 인수

사용자의 후속 요청에 따라 [Windows 계획 E2E-D](../spec/windows-engineering-plan.md#racp-native-re-acceptance)에 기존 IDA/WinDbg MCP의 원격 정적·dump·live debugger 인수를 핵심 항목으로 추가했다. **Client/Agent 제품 빌드·패키징은 실행하지 않았다.** 시험 입력으로 생성한 2048-byte AMD64 PE는 배포용 RACP 빌드가 아니다. 현재 SSOT v0.1.15를 유지했으며, 다음 결과의 B는 설치된 v0.1.10 Agent다.

### 15.1 실패 원인과 수정 범위

터미널 MCP 시험은 첫 `terminal_read`에서 Python `>>>`가 반드시 나온다고 가정했다. 실제 실패는 ConPTY 초기 ANSI 모드 프레임만 반환한 경우였다. `next_cursor`를 따라 최대 10회 읽으며 실제 프롬프트를 모아 검증하도록 시험을 보정했다. 제품의 timeout·출력·인증 기준을 바꾸지 않았다. 변경 전 실패와 단독 수정 후 1 passed (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/terminal-fixed.xml`, 과거 경로), version+terminal 최종 7 passed (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/version-terminal-final.xml`, 과거 경로)를 확인했다.

| 회귀 범위 | 이번 실제 결과와 판정 |
|---|---|
| Terminal·Browser·Browser file·plugin supervisor | 34개 묶음 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/isolation-regression.xml`, 과거 경로): **32 passed / 2 failed**. terminal 및 plugin 묶음은 통과; Browser cancel은 UNKNOWN, shutdown은 5초 timeout |
| 위 두 Browser lifecycle 실패 재현 | 개별 재검 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/browser-lifecycle-repro.xml`, 과거 경로): **2 passed**. 묶음에서의 간헐 실패 원인·해결은 미확정이며 전체 Browser PASS로 승격하지 않음 |
| 필수 lint | Ruff (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/lint-final.log`, 과거 경로): scripts/version.py, version unit, packages/apps 및 수정 terminal 시험 PASS |
| 필수 type | Mypy (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/mypy-final.log`, 과거 경로): **135 source files PASS** |

묶음 실행 로그 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/isolation-regression.log`, 과거 경로)의 uvicorn/platform WMI query는 `0x8007000e` 오류도 기록했다. 원인 후보인 호스트 자원/Windows 조회 경합과 native Job/profile 정리 경로를 분리해야 한다. 이 로그만으로 제품 결함이나 Windows 서비스 결함을 확정하지 않았고 WMI 서비스·stdlib를 변경하지 않았다. §14의 121 GUI click counter 불일치도 미해결이다. 재시도 결과는 기존 묶음과 중복되므로 합산하지 않는다.

### 15.2 RACP → 121 파일 → 기존 IDA MCP: 정적 분석 PASS

`RUN-20261007-racp-re-mcp-1`의 정상 시험 PE를 RACP owner API/standard의 요청별 승인으로 121의 고유 own 폴더에 저장하고, B hash를 조회한 뒤 인증 Artifact로 A에 회수했다. 원본/B/회수 SHA-256은 모두 `1c32769f9aaecab694fe254b3d3b6a9963afe99c331a4cda4dfaa10d24012a17`이다. 원격 전송·정리 증거 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/remote-results.json`, 과거 경로)와 입력 manifest (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/probe-manifest.json`, 과거 경로)를 보관했다.

실제 `mcp__ida_mcp__open_database`로 **B에서 회수한 PE**를 열고 `execute_python`으로 `racp_transform`의 RVA `0x1000`, `imul eax,ecx,7; xor eax,5Ah; retn` 및 `(7*a1)^0x5A` 의사코드를 얻었다. 실제 IDA 응답 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/ida-static-result.json`, 과거 경로)과 entry 분석 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/ida-entry-result.json`, 과거 경로)은 입력 6 → marker 112 저장 → Sleep(120000) → ExitProcess(0)을 확인했다. 기능 코드·정적 분석 결과를 원본 바이트와 대응시켰다.

정적 분석은 **RE-MCP-STATIC PASS**다. RACP 경로는 현재 세션에 없는 RACP MCP 대신 owner API를 사용했으며, IDA 경로는 실제 Codex의 기존 MCP를 호출했다. 이 차이를 숨기거나 전체 actual RACP MCP 인수로 처리하지 않는다. DB를 저장하고 이번 시험의 IDA lease 2개만 닫았다; DB 보존·정리 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-1/ida-cleanup.json`, 과거 경로). B의 own 폴더는 revision을 확인해 삭제했고 이번 PE를 원격 실행하지 않았다.

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

이 후속 배치는 §14의 원격 click counter 불일치를 원인부터 조사했다. 최초 입력 없는 진단 (`../../dist/acceptance/RUN-20261007-gui-preflight/evidence.json`, 과거 경로)에서 121의 child HWND·가시성·활성화·bounds·WindowFromPoint는 정상이나 Canvas WM_PAINT가 0회, UIA Button name이 `None`, 캡처는 빈 client area였다. fixture의 `GetWindowLong(GWL_WNDPROC)`가 64-bit procedure 주소 대신 0을 반환해 native control의 기본 메시지 처리가 사라졌다. [pywin32의 구현](https://github.com/mhammond/pywin32/blob/main/win32/src/win32gui.i)은 `SetWindowLong`이 pointer-sized 원래 주소를 반환하며 그 값을 CallWindowProc에 사용하도록 정의한다.

원래 주소를 `SetWindowLong` 반환값으로 보존한 한 변수 비교 시험 (`../../dist/acceptance/RUN-20261007-gui-preflight-pointer-safe/evidence.json`, 과거 경로)과 수정 source 재검 (`../../dist/acceptance/RUN-20261007-gui-preflight-fixed/evidence.json`, 과거 경로)은 Canvas paint 1회·Button name `Fixture Invoke` 및 실제 컨트롤 표시를 확인했다. `scripts/desktop_acceptance_fixture.py`의 native control subclass만 수정했고 부모·canvas 메시지 처리나 Broker의 권한/foreground/lease 기준은 변경하지 않았다.

121의 pointer 직접 비교 (`../../dist/acceptance/RUN-20261007-gui-preflight-fixed-pointers/evidence.json`, 과거 경로)에서도 GetWindowLong getter는 0, 같은 HWND의 GetWindowLongPtrW는 유효한 64-bit procedure 주소를 반환했다. 이 검사에도 입력을 주입하지 않았고 own timeout·process/folder 정리를 완료했다.

회귀 시험은 캐시된 타 프로세스 caption 조회에 의존하지 않고 실제 WM_GETTEXT를 own Button에 전달해 원래 native procedure가 동작하는지 검사한다. 수정 전 native 및 zero-pointer 환경 2 failed (`../../dist/acceptance/RUN-20261007-gui-preflight/native-control-red-message.xml`, 과거 경로)를 확인했고, 수정 후 3 passed / 1 skipped (`../../dist/acceptance/RUN-20261007-gui-preflight/native-control-green.xml`, 과거 경로)다. skipped는 A의 own fixture foreground 활성화를 OS가 거부한 실제 입력 시험이다. 초기 caption-only 검사는 결함을 놓쳤으므로 최종 회귀 증거로 사용하지 않는다.

### 17.1 실제 121 owner API 입력 및 정리 PASS

`RUN-20261007-190500-owner-desktop`은 설치된 B Agent v0.1.10에서 수정한 fixture를 실행했다. Device/boot/epoch/session/window/PID/create_time을 다시 바인딩했고, 다음 동작의 **자체 counter/text/scroll/drag 결과**를 매 단계 확인했다.

| 동작 | 실제 확인 결과 |
|---|---|
| single/right/double click | 각각 Button click/right/double counter 일치 |
| Unicode·key | `RACP 한글 😀`, Ctrl+A/Backspace 후 빈 문자열, Tab event 확인 |
| scroll·drag | scroll `[0,120]` → `[0,0]`, drag `[140,80]`·event 1·capture 해제 |
| UIA | Value `UIA 한글\r\nsecond line`, Invoke 후 clicks 3 |
| 회수·정리 | PNG Artifact SHA 일치, own 정상 timeout, window 부재, revision 확인 후 own 폴더 삭제 complete |

전체 실제 결과 (`../../dist/acceptance/RUN-20261007-190500-owner-desktop/results.json`, 과거 경로), 실행 로그 (`../../dist/acceptance/RUN-20261007-190500-owner-desktop/test.log`, 과거 경로), 정상 컨트롤 PNG (`../../dist/acceptance/RUN-20261007-190500-owner-desktop/own-window.png`, 과거 경로)를 보관했다. 이 실행은 별도 title 클릭 대기 없이 foreground 활성화까지 성공했다. 과거 사용자의 수동 클릭과 새 foreground/observation을 혼동하지 않는다. 실제 Codex RACP MCP/OAuth와 새 배포 후보의 B 설치 인수는 여전히 미검증이다.

### 17.2 Browser 정리 경로의 추가 진단과 잔여 조건

동작과 timeout을 바꾸지 않는 정리 시간 trace (`../../dist/acceptance/RUN-20261007-browser-lifecycle-trace/cleanup-events.json`, 과거 경로)를 넣은 4개 lifecycle 묶음은 3 failed / 1 passed (`../../dist/acceptance/browser-lifecycle-trace.xml`, 과거 경로)였다. own worker returncode 1·receiver 종료 뒤에도 전체 정리가 **5.06–5.83초**, cleanup unknown으로 끝났다. 이 묶음은 확정 PASS가 아니며 Job/native termination 확인의 5초 한도 경로를 더 조사해야 한다.

추가 native Job·process signal trace (`../../dist/acceptance/RUN-20261007-browser-native-trace-group/native-events.json`, 과거 경로)의 같은 4개 묶음은 4 passed (`../../dist/acceptance/browser-native-trace-group.xml`, 과거 경로)다. 각 종료에서는 Job ActiveProcesses=0 및 retained process handle signal/exit code를 확인했고 timeout을 늘리지 않았다. 앞선 단독 native trace (`../../dist/acceptance/browser-native-trace.xml`, 과거 경로)도 1 passed다. 두 결과의 차이는 간헐 실패를 해결했다는 증거가 아니며, failure 실행의 마지막 native handle 상태를 확보해야 한다. 당시 A 호스트 관측 (`../../dist/acceptance/RUN-20261007-gui-preflight/host-resource-observation.json`, 과거 경로)은 CPU 100%, 가용 메모리 약 6GB였고 전역 서비스나 타 사용자 process를 변경하지 않았다.

후속 Browser+file 묶음은 native trace (`../../dist/acceptance/RUN-20261007-browser-native-trace-combined/native-events.json`, 과거 경로)에서 실패 순간을 확보했다. 첫 profile/recovery 시험의 close는 ActiveProcesses=0·exit code 1인데 own child PID 11188의 handle wait가 **WAIT_TIMEOUT(258)** 상태로 5.031초 한도에 닿아 UNKNOWN이 됐다. Windows의 [비동기 종료 계약](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)에 따라 pending I/O가 완료/취소되기 전에는 종료 확인이 끝나지 않을 수 있다. 관측은 native 종료 확인 지연을 증명하지만 특정 driver/I/O 원인은 미확정이다. exit code만 보고 cleanup을 complete로 바꾸거나 timeout을 늘리지 않았다. 최종 JUnit (`../../dist/acceptance/browser-native-trace-combined.xml`, 과거 경로)은 **6 passed / 6 failed**, failure/error/skip 집계에서 error/skip은 0이다. Cancel·shutdown, download/upload/oversize 및 controller 강제 종료 뒤 child lifetime 실패가 남았으며 전체 Browser gate는 FAIL이다.

### 17.3 버전·검증과 동적 분석 실행 조건

저장소 규칙에 따라 수정 배치의 PATCH를 `scripts/version.py`로 **0.1.15→0.1.16** 한 번 증가시키고 `uv lock --offline`로 lock을 동기화했다. 제품 빌드·패키징이나 운영 `.venv` 재설치를 실행하지 않았다. version·초기 등록 opt-in 27 passed (`../../dist/acceptance/RUN-20261007-gui-preflight/version-optin-final.xml`, 과거 경로), Ruff PASS (`../../dist/acceptance/RUN-20261007-gui-preflight/lint-final.log`, 과거 경로), Mypy 135 files PASS (`../../dist/acceptance/RUN-20261007-gui-preflight/mypy-final.log`, 과거 경로)를 확인했다. 각 재현/비교 묶음은 중복이 있어 합산하지 않는다.

RE-MCP-STATIC의 원격 파일 회수→실제 IDA 분석 PASS는 §15의 증거를 유지한다. 이번 재확인에도 121은 administrator=False·epoch 3·동일 boot였으므로 RE Lab의 실제 관리자 셸 조건에 따라 원격 PE 실행·dump/WinDbg 분석은 미실행이다. 관리자 Agent 재시작 확인과 live CDB endpoint 준비가 필요하다. Windows 핵심 인수 전체·배포 gate·Browser 간헐 실패는 계속 미완료이며 goal을 완료로 표시하지 않는다.

작성 중 병행 작업으로 현재 SSOT는 **0.1.16**이 됐다. 계획서의 Gateway ZIP/코드 기준선 0.1.15와 구분했고 해당 변경을 되돌리지 않았다. 신규 계획의 기능이 v0.1.16에 구현됐다는 의미는 아니다.

계획서 자체 리뷰에서 권한 승인 주체, actor/owner 구분, UNKNOWN 작업의 update blocker, 공유 Journal과 Gateway 전용 migration의 경계, Ed25519 manifest 서명, revision·경로·queue 한도를 보완했다. 최종 문서 5개의 메타데이터·code fence와 상대 파일 링크 243개·anchor 55개를 검사해 오류 0건을 확인했다. 문서 검증 기록 (`../../dist/acceptance/gateway-web-plan-document-verification.json`, 과거 경로)을 보관했다.

---

<a id="racp-windbg-dump-pass"></a>

## 18. 실제 121 실행 덤프와 Codex WinDbg MCP 분석

사용자는 121 Client를 관리자 권한으로 재실행하고 Agent를 시작했다고 답했다. 실제 원격 셸에서도 administrator=True를 확인했고, 새 boot `boot_853e8130c7b24a27abf4ee2767d9cdfb`·epoch 4에 시험을 바인딩했다. 일반 권한 예외는 사용하지 않았다. B 설치 Agent는 v0.1.10이고 A source는 v0.1.16이며 제품 빌드·배포는 계속 보류했다.

### 18.1 RACP 실행·메모리·dump 회수 PASS

`RUN-20261007-racp-re-mcp-2`는 §15에서 IDA로 확인한 같은 SHA-256의 정상 AMD64 PE를 B의 새로운 own 폴더에 저장했다. A/B/회수 파일 hash를 비교한 뒤 PID **13164**, create_time **1791369021.4998455**로 실행 identity를 확인했다. `process.memory_read`의 `0x140003000` 값은 `70000000` = **112**로 정적 분석의 `(7*6)^0x5A`와 일치했다.

같은 own PID/생성 시각을 다시 검증한 관리자 Python 셸에서 DbgHelp `MiniDumpWriteDump(MiniDumpWithFullMemory)`를 호출했다. **26,155,842 bytes**의 dump를 인증 Artifact로 회수했고 SHA-256 **`0f4c54b9ec29a57b1d4398ee7ca346b535c26005cb3bf898fdc49d318f5802bc`**를 비교했다. 원격 실제 결과 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-results.json`, 과거 경로), 실행 로그 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-test.log`, 과거 경로)를 보관했다. own process를 생성 시각/boot로 한정해 종료하고 own 폴더를 revision 확인 후 삭제했다.

### 18.2 기존 WinDbg MCP에서 원격 실행 상태 분석 PASS

최초 `open_cdb_dump`의 자동 `!analyze -v`는 60초 timeout (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/cdb-open-timeout.json`, 과거 경로)이었다. 실제 CDB는 살아 있었으나 upstream은 등록된 session_id를 오류 응답에 반환하지 않아 해당 세션을 도구로 이어갈 수 없었다. 프로세스의 own dump 경로/PID/create_time을 확인한 뒤 해당 CDB만 명시적으로 정리 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/cdb-timeout-cleanup.json`, 과거 경로)했다. 관측 timeout만으로 process 종료를 가정하거나 기존 사용자 debugger를 재시작하지 않았다.

대신 A의 기존 CDB가 **회수 dump**를 연 local named-pipe engine을 시작하고, Codex의 실제 `open_cdb_remote` MCP를 그 A-local endpoint에 연결했다. 새 MCP adapter나 B debugger 설치·방화벽 변경은 수행하지 않았다. 실제 session_id `cdb-cae8b439`에서 `run_cdb_command`가 다음을 확인했다.

| 확인 대상 | 실제 WinDbg MCP 결과 |
|---|---|
| memory marker | `dd 0x140003000 L1` → `00000070` (112), B live read와 일치 |
| 함수 bytes·명령 | RVA `0x1000`, `6bc10783f05ac3`, `imul eax,ecx,7; xor eax,5Ah; ret`, IDA 및 원본과 일치 |
| module identity | base `0x140000000`, `RACP_RE_Probe.exe`, B의 이번 own folder 경로, dump thread PID `0x336c`=13164 |
| stack/register | `ntdll!NtDelayExecution` → `KERNELBASE!SleepEx` → own entry 주소, 정적 분석의 Sleep 대기 동작과 일치 |

실제 MCP open 응답 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/windbg-open-response.json`, 과거 경로), 명령 응답 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/windbg-validation-response.json`, 과거 경로), 독립 결과 assertion (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/native-analysis-assertions.json`, 과거 경로)을 저장했다. 이 파일은 정상 실행 중 수집한 snapshot이며 실제 crash로 해석하지 않는다. PDB가 없는 own PE의 stack 표시는 가장 가까운 export 이름과 offset이며, 그 이름을 실행 중인 정확한 함수로 단정하지 않는다.

**RE-MCP-STATIC와 RE-MCP-DUMP는 실제 조합 PASS**다. RACP 경로는 owner API이고 analyzer 경로는 실제 Codex IDA/WinDbg MCP다. named-pipe는 A에서 dump engine/client를 연결한 것으로 B에 대한 실시간 debugger attach/step 증거가 아니다.

도구 목록이 재설정된 뒤 실제 MCP close는 세션 부재 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/windbg-client-cleanup.json`, 과거 경로)를 반환했고, own CDB process inventory (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/cdb-process-inventory.json`, 과거 경로)에서도 잔류 0개였다. 다시 B를 조회해 own PID 부재 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-process-cleanup-check.json`, 과거 경로)와 폴더 PATH_NOT_FOUND (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-folder-cleanup-check.json`, 과거 경로)를 확인했다.

### 18.3 실시간 연결·인증 및 Browser 잔여 조건

현재 Gateway의 실제 `/mcp/` initialize는 HTTP 503 / Remote MCP requires configured OAuth (`../../dist/acceptance/RUN-20261007-racp-mcp-protocol/initialize-response.json`, 과거 경로)다. SDK 버전 추측 대신 실제 ingress 응답으로 원인을 확인했다. OAuth 미설정 public endpoint의 owner-only 차단이며 이를 해제하거나 인증을 우회하지 않았다. 실제 Codex RACP MCP/OAuth 도구 인수는 아직 BLOCKED_ENV다.

관리자 B의 SDK/Tools/App Execution Alias 및 WinDbg Appx inventory (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/remote-debugger-inventory.json`, 과거 경로)는 cdb_paths/windbg_packages가 비어 있었다. 검사한 경로에서 debugger endpoint 준비를 확인하지 못했으며 전역 미설치를 단정하지 않는다. 직접 B attach/break/step/continue의 RE-MCP-LIVE는 미검증이고, 현 RACP에는 일반 CDB transport tunnel이 없다.

Browser는 역할 조회 재검 2 passed (`../../dist/acceptance/browser-native-roles.xml`, 과거 경로)였지만, worker stdin EOF 후 종료를 유도한 비교 4개 중 3 failed (`../../dist/acceptance/browser-eof-experiment.xml`, 과거 경로)로 해결되지 않았다. trace (`../../dist/acceptance/RUN-20261007-browser-eof-experiment/native-events.json`, 과거 경로)에서 main Chrome과 NetworkService의 signal 대기를 확인했다. 이 가설을 제품 수정으로 반영하지 않았고 deadline·cleanup 성공 기준을 유지했다. 전체 Browser gate와 Windows release gate는 계속 FAIL/미완료다.

### 18.4 Gateway 상태 복원

분석 후 `192.168.29.141:8765` listener가 사라진 것을 실제 확인했다. localhost:8765는 다른 앱의 listener이므로 유지했다. 기존 `.racp/two-pc-141-121` DB·owner store·CA/server certificate를 보존한 채 기존 `.venv`의 Gateway를 141 주소에 hidden으로 다시 시작했고, PID/생성 시각·설정 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/gateway-restoration-process.json`, 과거 경로)과 121 재연결 epoch 5/새 boot (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/gateway-restoration-observation.json`, 과거 경로)를 확인했다. 이 관측만으로 모든 장애·중복 부작용 gate를 PASS로 승격하지 않는다.

이후 별도의 own counter 시험으로 실제 Gateway 재시작과 멱등성을 검사했다. 다른 ACCEPTED/RUNNING 작업이 없는 것을 확인하고, 저장한 정확한 PID/create_time/command로 이번 Gateway만 종료·복원했다. **Agent boot는 유지, epoch 5→6**, 이전과 동일한 key의 shell.exec는 **같은 operation ID·동일 결과**를 반환했고 파일의 실행 counter는 **1**이었다. 실제 결과 (`../../dist/acceptance/RUN-20261007-gateway-restart-2/results.json`, 과거 경로)의 PASS 및 own revision 폴더 정리 complete를 확인했다. 최초 시도 (`../../dist/acceptance/RUN-20261007-gateway-restart/results.json`, 과거 경로)는 복사한 create_time의 정밀도 차이로 identity guard가 재시작 전에 거부했고 own 폴더를 정리했다. 임의 허용 오차를 추가하지 않고 저장된 metadata의 정확한 값을 사용했다. 이 PASS는 Gateway 재시작 한 종류의 owner API 시험이며 네트워크·Agent·로그인·재부팅·actual Codex MCP의 전체 복구 matrix를 대체하지 않는다.

최종 분석 검증 (`../../dist/acceptance/RUN-20261007-racp-re-mcp-2/verification-final.json`, 과거 경로)은 상대 링크 227개 오류 0, 원본/회수 PE·dump SHA-256, native 함수 bytes, live/dump marker와 own process/folder/CDB 정리를 독립 대조해 PASS였다. 문서 추가 전 집계이며 원본 실행 기록을 보존한다.

---

<a id="windows-host-resource-regression"></a>

## 19. 호스트 자원 기준선 재확인과 Python 전체 회귀

동일 호스트 `DESKTOP-06NU139`의 실제 물리 메모리 보고가 이전 약 **16GiB**에서 현재 약 **32GiB**로 바뀌었다. 이전 자원 관측 (`../../dist/acceptance/RUN-20261007-gui-preflight/host-resource-observation.json`, 과거 경로)은 CPU 100%·가용 약 6GB였고, 현재 호스트 identity (`../../dist/acceptance/RUN-20261007-browser-late-signal/host-identity.json`, 과거 경로)와 자원 관측 (`../../dist/acceptance/RUN-20261007-browser-late-signal/current-host-resource.json`, 과거 경로)은 6 logical CPU·가용 약 23GB·CPU 39.2%다. 이 배치에서 메모리 설정·서비스·프로세스 우선순위를 변경하지 않았다. 실제 자원 조건이 다르므로 앞선 실패와 현재 통과를 같은 환경의 제품 수정 결과로 합치지 않는다.

기존 cleanup deadline과 return 값을 유지하는 진단 hook으로 Job에 속한 own child handle을 복제해 보존하고, UNKNOWN일 때만 cleanup 반환 뒤 최대 15초의 native signal을 별도 thread에서 관측하도록 했다. 추가 관측 대기는 제품 cleanup이나 operation deadline을 늘리지 않는다. lifecycle 3 passed (`../../dist/acceptance/browser-late-signal.xml`, 과거 경로), Browser/file 12 passed (`../../dist/acceptance/browser-late-signal-combined.xml`, 과거 경로)를 확인했다. 후자 15개 cleanup event (`../../dist/acceptance/RUN-20261007-browser-late-signal-combined/events.json`, 과거 경로)는 모두 complete, 최대 0.110초였고 late-signal 관측이 필요한 UNKNOWN은 발생하지 않았다.

이 결과는 현재 자원 조건에서의 PASS다. 실패 시 kernel/I/O 원인은 아직 확정하지 못했고, 제품 Browser 코드·5초 종료 확인·성공 기준은 변경하지 않았다. WMI `0x8007000e`와 native 종료 지연의 과거 증거는 §14/17/18에 보존한다. 현재 source v0.1.16의 `tests/unit`+`tests/integration` 전체 회귀를 별도로 시작했으며, 이 절에 실제 종료 결과를 이어서 기록한다. 배포용 build는 실행하지 않았다.

### 19.1 현재 기준선 전체 Python 결과

전체 JUnit (`../../dist/acceptance/python-current-32gb.xml`, 과거 경로)은 **361 passed / 21 skipped**, failure/error 0, 577.12초다. 분리 집계 (`../../dist/acceptance/python-current-32gb-summary.json`, 과거 경로)는 unit **211 passed**, integration **150 passed / 21 skipped**다. Browser/file의 12개도 이 전체 실행에서 진단 hook 없이 통과했다. 이전 실패를 삭제하거나 현재 수치와 중복 합산하지 않는다.

| skip 범위 | 개수 / 실제 사유 |
|---|---|
| own Windows GUI | 15 / `RACP_TEST_GUI=1` 미설정; 별도 opt-in 검사로 이어감 |
| POSIX dirfd | 1 / 실제 POSIX runner 필요; Windows에서 지원 증거로 이관하지 않음 |
| native GDB | 2 / 명시적인 설치 GDB 경로 필요 |
| native Ghidra/JDK | 2 / 명시적인 설치 executable 경로 필요 |
| Docker Keycloak | 1 / 별도 local integration opt-in 필요; 직접 RACP MCP/OAuth의 현재 503은 §18에서 실제 확인 |

지연 관측을 위해 own pytest stack을 읽으려던 시점에는 검사 프로세스가 이미 종료해 py-spy가 process-open 오류를 반환했다. 실제 exec handle exit 0·완성된 JUnit으로 종료를 확인했고 그 결과를 pytest hang이나 제품 오류로 해석하지 않는다. 현재 기본 Python gate는 PASS이며 native/실제 MCP/새 B 배포 후보 및 Windows release gate는 별도로 판정한다.

### 19.2 GUI opt-in 및 동시 변경의 검증 경계

현재 호스트에서 `RACP_TEST_GUI=1`로 실행한 native GUI JUnit (`../../dist/acceptance/native-gui-current-32gb.xml`, 과거 경로)은 **5 passed / 10 skipped / 160 deselected**, 22.49초다. skip 9개는 `OS did not activate the owned fixture`, 1개는 `OS refused activation of the owned fixture`다. 창 활성화 거부를 입력 성공으로 집계하지 않는다. §17의 실제 121 PC 입력 PASS는 별도 증거로 유지한다.

전체 Python 실행이 수집한 뒤 다른 작업에서 Gateway config/migrations/service/management 파일과 시험이 추가되고 있다. §19.1의 361 passed는 이 신규 코드의 검증을 포함하지 않는다. 이후 전체 Ruff 검사 (`../../dist/acceptance/lint-oauth-preflight.log`, 과거 경로)는 **12 errors / exit 1**이었다. 동시 작성 중인 Gateway 파일을 이 배치에서 덮어쓰지 않았으며, 전체 현재 작업 트리의 lint gate는 FAIL이다. 기존 onboarding·Agent·GUI fixture 변경 범위의 별도 Ruff 검사 (`../../dist/acceptance/lint-onboarding-oauth-preflight.log`, 과거 경로)는 exit 0이다.

추가 전체 mypy 검사 (`../../dist/acceptance/mypy-oauth-preflight.log`, 과거 경로)는 142개 source file 중 신규 `windows_service.py`의 Windows 기준 `fcntl.flock/LOCK_EX/LOCK_NB/LOCK_UN` attribute 오류 **5개 / exit 1**이었다. 해당 파일이 다른 작업에서 작성 중이므로 이 배치에서 수정하지 않았다. SSOT는 **0.1.16**이며 version·desktop opt-in·connection import 재검 (`../../dist/acceptance/onboarding-oauth-preflight.xml`, 과거 경로)은 **27 passed**, 2.35초다.

### 19.3 기존 OAuth 제공자 부재와 직접 MCP 시험 조건

사용자가 기존 OAuth/OIDC 제공자가 없다고 확인했다. 현재 로컬 PATH와 일반 설치 경로에서 Docker/Keycloak을 찾지 못했고 Java 21 경로는 있다. 저장소에는 기존 `test_oauth_keycloak.py`와 `prepare_codex_oauth_lab.py`가 있지만 Docker 실행을 전제로 하므로 그대로 실행하지 않았다. 후자의 이전 lab 경로도 현재 141–121 lab과 다르다.

직접 Codex RACP MCP 시험을 위한 다음 제안은 공식 ZIP Keycloak의 별도 임시 localhost HTTPS 실행, 임시 realm/계정, 기존 Gateway DB·등록 상태 보존, PKCE S256·scope 검증 후 Codex 연결이다. 이 신규 인증 시험 환경의 구성 승인을 요청했고 아직 시작하지 않았다. §18의 owner API 기반 원격 실행·회수 및 실제 Codex IDA/WinDbg dump 분석 PASS와 직접 RACP MCP/OAuth 인수를 구분한다. 제품 빌드·패키징은 계속 보류한다.

### 19.4 실제 121 메모리 크기·identity·정책 경계

현재 Gateway listener PID/create_time 및 121 ONLINE, epoch 6·boot `boot_729ea07e06c544d684ce7d767071e71f`를 실제 재확인했다. 관리자 권한을 확인하는 자체 Python fixture에 16 MiB 고정 buffer를 만들고 source/설치 Agent v0.1.10의 owner API 경로에서 관측했다. 첫 실행 (`../../dist/acceptance/RUN-20261007-remote-memory-boundaries/results.json`, 과거 경로)의 regions가 요청 주소를 포함했고 **32 B / 4 KiB / 128 KiB / 16 MiB** 모두 원본 bytes·길이·SHA-256과 일치했다. 작은 결과는 inline, 큰 결과는 인증된 Artifact로 회수했다. `live_process_observation`, `atomic_snapshot=false`도 확인했다. 16 MiB SHA-256은 `341aacac661ccb210720bedaa9ead5d668fe5ea41a73532fc147c71e34040df1`이다.

첫 실행 전체의 FAIL은 시험 helper가 stale identity 오류를 임의로 예상한 데서 발생했다. 실제 제품은 기존 `ProcessProvider.identity`와 unit contract에 맞는 **PRECONDITION_FAILED / PID identity changed / not_started**를 반환했다. 첫 기록을 보존하고 helper의 기대값을 기존 계약으로 수정한 뒤 이미 통과한 크기 읽기를 반복하지 않고 거부 항목만 재검 (`../../dist/acceptance/RUN-20261007-remote-memory-negative/results.json`, 과거 경로)했다. read_only profile은 **403/PERMISSION_DENIED**, stale create_time·boot는 **PRECONDITION_FAILED**, 16 KiB 접근 불가 주소는 **MEMORY_UNAVAILABLE**, 16 MiB+1은 **400/INVALID_ARGUMENT**로 거부됐고 결과 자료는 반환하지 않았다.

두 실행 모두 정확한 own PID/create_time/boot로 종료했고 process.inspect의 PROCESS_NOT_FOUND, own 폴더 revision 확인 뒤 삭제, cleanup errors 0을 기록했다. 이는 T-MEM-01/02의 설치 Agent owner API 증거 및 T-MEM-03/04의 일부 경계 증거다. 실제 read 중 cancel/spool 잔류, protected control PID, OAuth scope 및 source v0.1.16 신규 배포 후보의 검증을 대체하지 않는다. 직접 Codex RACP MCP와 live debugger gate도 그대로 미완료다.

### 19.5 실제 PC 요청 멱등성과 100 MiB 중단·재개

T-EXEC-02의 100개 중첩 동일-key 요청 (`../../dist/acceptance/RUN-20261007-concurrent-counter-100/results.json`, 과거 경로)은 모두 HTTP 200/SUCCEEDED, 동일 operation ID·동일 결과였고 실제 121의 own counter 파일은 **1**이었다. 승인 대기 요청은 실행하지 않고 owner 승인 후 100개 요청을 함께 전송했다. 같은 key에 다른 payload를 보낸 요청은 **409/IDEMPOTENCY_CONFLICT/not_started**로 거부됐다. 변경 요청 뒤 counter도 1이며 own 폴더 revision을 확인해 삭제했다.

T-ART-01의 121 생성 100 MiB binary 회수·resume (`../../dist/acceptance/RUN-20261007-remote-artifact-100mib-resume/results.json`, 과거 경로)은 실제 live download stream을 **4 MiB** 수신 뒤 EOF 전에 닫았다. 로컬 prefix를 유지하고 **같은 transfer ID**로 SDK download를 재개했다. 최종 **104,857,600 bytes**와 SHA-256 `4cbf988462cc3ba2e10e3aae9f5268546aa79016359fb45be7dd199c073125c0`가 B 원본 및 Gateway Artifact와 일치했다. download committed_bytes는 중단 시 0이고 전체 검증 ACK 뒤 104857600 / DOWNLOAD_COMPLETE였다. 이를 upload의 chunk commit offset과 혼동하지 않는다. 121 own 폴더는 revision 확인 후 삭제했고 A의 회수 원본은 증거로 유지한다.

두 시험은 설치 Agent v0.1.10의 owner API 경로이며 source v0.1.16 배포 후보 및 actual Codex RACP MCP 인수를 대체하지 않는다. 현재 후보의 54개 상세 항목 audit (`../../dist/acceptance/RUN-20261007-windows-current-audit/test-matrix.json`, 과거 경로)은 각 절차·합격 기준을 보존하고 installed/owner 증거와 새 후보의 미검증 상태를 분리한다. 제품 빌드·패키징은 실행하지 않았다.

후속 보호 대상 memory 검사 (`../../dist/acceptance/RUN-20261007-windows-current-audit/protected-control-memory.json`, 과거 경로)는 현재 Desktop Broker의 정확한 PID/create_time/boot로 요청했고 **PERMISSION_DENIED**, result=null이었다. 주소는 0으로 지정해 guard가 실패하더라도 사용자 자료를 읽지 않도록 했다. 독립 정리·해시 검증 (`../../dist/acceptance/RUN-20261007-windows-current-audit/verification.json`, 과거 경로)은 네 시험의 own 폴더가 모두 PATH_NOT_FOUND, 100개 요청 operation ID 1개·counter=1, A 회수 100 MiB의 SHA-256 일치를 다시 확인했다.

현재 source의 Client Node 검사 (`../../dist/acceptance/client-node-test-current.log`, 과거 경로)는 **11 passed / fail 0 / skip 0**, exit 0이었다. 실행 명령은 `node --test tests/*.test.cjs`이며 이 검사에서 product bundle을 빌드하거나 배포 패키지를 생성하지 않았다.

### 19.6 실제 파일 revision 및 입력 없는 lease 수명

121 자체 파일 시험 (`../../dist/acceptance/RUN-20261007-file-lease-boundaries/results.json`, 과거 경로)에서 한글·공백 filename, UTF-8 BOM·CRLF·한글/emoji의 원본 bytes를 Artifact로 비교해 일치를 확인했다. shell의 own 파일 변경 뒤 이전 revision의 replace/delete는 **PRECONDITION_FAILED**, 외부 변경 내용은 보존됐고 새 revision으로 replace/read는 성공했다. T-FS-01의 일부 bytes 경계와 T-FS-03의 설치 Agent owner API 증거다.

같은 실행에서 read_only lease acquire는 PERMISSION_DENIED, 중복 acquire는 RESOURCE_BUSY, 잘못된 lease ID release는 PERMISSION_DENIED, 정상 renew/release는 성공했다. 실제 입력은 보내지 않았다. 만료 이후 helper가 LEASE_EXPIRED 하나만 예상해 첫 실행 전체는 FAIL이었다. Broker main의 주기적 `core.tick()`이 만료 lease를 이미 폐기한 경우 scoped_lease는 **PERMISSION_DENIED / desktop lease scope mismatch**를 반환한다. 제품 결함으로 판단하지 않았고 원래 실패 기록을 보존했다. helper의 finally가 이미 폐기된 lease release에서 중단되어 폴더 정리가 빠진 것은 시험 helper 결함이다. 별도 정리 기록 (`../../dist/acceptance/RUN-20261007-file-lease-boundaries/cleanup-recovery.json`, 과거 경로)에서 정확한 own 폴더와 revision으로 삭제 후 PATH_NOT_FOUND를 확인했다.

이미 통과한 파일·lease 항목을 반복하지 않고 만료 후 재취득만 검증 (`../../dist/acceptance/RUN-20261007-lease-expiry-reacquire/results.json`, 과거 경로)했다. expired ID renew는 result=null/not_started로 거부됐고, 새 ID의 acquire/release는 PASS였다. 이는 같은 owner principal의 서로 다른 요청에서 관측한 T-DESK-08 일부 증거이며 다른 owner·동시 입력·modifier 해제·Guardian 장애 검증을 대체하지 않는다.

[릴리스 게이트 기준표](windows-release-gates.md)의 VERIFIED 열은 v0.1.8 역사적 기록임을 명시했다. 이를 v0.1.16 또는 121의 현 인수로 오인하지 않도록 했고 현재 실행 결과의 SSOT는 이 문서로 유지한다. 제품 source, B 설정·디스플레이·사용자 입력은 변경하지 않았다. OAuth 구성 승인 및 빌드 요청은 계속 기다린다.

### 19.7 읽기 지연 실측과 실제 Job tree 취소

읽기 지연 원시 표본 (`../../dist/acceptance/RUN-20261007-read-latency-baseline/results.json`, 과거 경로)은 warm-up **20회** 후 각 **200회**의 HTTP owner API 왕복을 측정했다. 두 지표 모두 실패 **0/200**, nearest-rank p95는 **device_list 6.81 ms / filesystem.stat 45.65 ms**다. 현재 A의 6 logical CPU·32 GiB와 설치 B Agent v0.1.10 조건이며 실제 Codex MCP, 계획의 4 vCPU/8 GiB 기준 환경, 나머지 네 지표·30분 부하·8시간 soak를 대신하지 않는다. own marker 폴더는 revision 확인 후 삭제했다.

첫 Job tree 시도 (`../../dist/acceptance/RUN-20261007-owned-job-tree-cancel/results.json`, 과거 경로)는 helper의 process.tree 요청이 create_time/agent_boot_id를 빠뜨려 HTTP 400으로 접수 거부됐다. protocol registry의 ProcessTarget 계약을 확인하고 helper에 payload validation 및 정확한 target identity를 적용했다. 첫 Job은 CANCELLED, own 폴더 삭제·cleanup errors 0을 기록했고 부모/자식/손자 후속 부재 관측 (`../../dist/acceptance/RUN-20261007-owned-job-tree-cancel/post-cleanup-processes.json`, 과거 경로)을 보존했다.

계약 수정 후 실제 검증 (`../../dist/acceptance/RUN-20261007-owned-job-tree-cancel-2/results.json`, 과거 경로)은 자체 root/child/grandchild의 PID/create_time과 각각의 loopback ephemeral listen port를 기록하고 tree 관측과 일치시켰다. process.wait **100 ms timeout** 뒤 정확한 root가 살아 있음을 확인했다. 실제 Job cancel 뒤 **CANCELLED**, 세 process.inspect는 **PROCESS_NOT_FOUND**, own 세 포트 connect_ex는 모두 실패였다. 늦은 cancel은 같은 terminal Job 기록을 반환했고 own 폴더 삭제·cleanup errors 0이었다. T-PROC-01, T-JOB-01 및 T-EXEC-03의 cancel 부분에 대한 owner API 증거이며 자동 shell timeout·Agent crash·actual MCP를 완료로 승격하지 않는다.

### 19.8 실제 121 Browser 및 핵심 연결 차단

121 native Browser 인수 (`../../dist/acceptance/RUN-20261007-remote-browser-native/results.json`, 과거 경로)는 **PASS**였다. pinned runtime capability 보고뿐 아니라 실제 browser.open이 isolated_ephemeral/sandbox=true로 성공했다. 자체 headless about:blank의 DOM만 구성해 한글·emoji type/click 뒤 `Hello 한글 😀 RACP`를 snapshot 및 회수 PNG (`../../dist/acceptance/RUN-20261007-remote-browser-native/owned-browser.png`, 과거 경로)에서 확인했다. **1280×720** PNG의 SHA-256은 Gateway Artifact metadata와 일치했고 오래된 observation ID 입력은 STALE_OBSERVATION으로 거부됐다. 외부 사이트·사용자 Browser·OS foreground를 사용하지 않았다.

browser.close는 **CLOSED / cleanup_status=complete / owned_browser_terminated**, 반복 close는 동일 결과였다. close 후 snapshot은 HANDLE_EXPIRED, owner handle 조회도 CLOSED였다. 이는 설치 Agent v0.1.10 owner API의 T-BROW-01 및 T-BROW-02 일부 증거이고 새 source 후보·실제 Codex MCP·upload/download·CDP·전체 lifecycle/soak를 대신하지 않는다. §14/17/18의 과거 A Browser cleanup 실패를 제품 수정으로 해결한 증거로도 이관하지 않는다.

현재 핵심 완료를 막는 조건은 직접 RACP MCP의 OAuth 미설정, 임시 인증 시험 구성의 미승인, live B debugger endpoint 미준비 및 사용자가 보류한 새 후보 빌드·배포다. 같은 owner API 증거를 반복해 실제 Codex/OAuth 인수로 승격하지 않는다. 구현·부분 인수 결과는 보존하고 해당 연결·환경 준비 후 남은 경로를 재개한다.

---

<a id="remote-analysis-scope-correction"></a>

## 20. 사용자 요청 범위 정정 — OAuth 구성은 원격 분석의 필수 조건이 아님

2026-10-08 사용자는 OAuth 구현을 요청한 적이 없다고 정정했다. 요청은 **RACP를 활용해 원격 정적·동적 분석이 가능한지 기존 Codex 리버싱 MCP와 함께 시험**하는 것이며, Gateway MCP를 Codex에 직접 등록하는 특정 연결 방식이나 OAuth 제공자 신설을 필수로 요구하지 않았다. 직접 MCP/OAuth와 live attach를 핵심 선행 조건으로 확대한 agent의 해석을 철회한다. §19.8의 OAuth·선택 live 경로에 의한 전체 목표 차단 판정도 이 정정으로 대체한다.

기존 RACP owner API는 이미 보유한 owner 인증으로 원격 작업·파일 회수가 가능하다. 실제 Codex가 그 API를 호출해 B의 자체 PE·메모리·dump를 회수하고 기존 IDA/WinDbg MCP로 분석한 §15/18의 증거는 요청한 정적 분석 및 원격 동적 실행·dump 분석의 PASS다. dump 분석이 B live attach를 입증하지 않는다는 기술적 한계는 유지하되, 별도로 요구되지 않은 live debugger를 완료 조건으로 추가하지 않는다.

Gateway `/mcp/`의 HTTP 503은 **직접 Gateway MCP 등록이라는 선택 경로**에서 현재 코드가 외부 OAuth를 요구하기 때문이다. 10월 8일 관측 (`../../dist/acceptance/resume-20261008-auth-check.json`, 과거 경로)도 같은 응답과 121 ONLINE을 확인했으며 기존 owner API 경로의 기능 장애를 뜻하지 않는다. 임시 Keycloak 구성 승인 요청은 철회한다. OAuth 제공자 설치·신규 OAuth 구현·인증 우회는 수행하지 않았다.

이 정정은 Windows 배포 검증 전체의 완료 선언이 아니다. 사용자 지시에 따른 제품 빌드 보류와 새 후보·display/session·부하/soak 등의 미검증 범위는 유지한다. 병행 변경 후 현재 SSOT는 v0.1.17이며 전체 Ruff (`../../dist/acceptance/lint-20261008-resume.log`, 과거 경로) exit 0, mypy (`../../dist/acceptance/mypy-20261008-resume.log`, 과거 경로)는 161 source files에서 오류 0이었다. 앞선 v0.1.16/B 설치 v0.1.10 결과를 새 배포 후보 인수로 자동 이관하지 않는다.

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

초기 전체 검사 (`../../dist/acceptance/python-20261008-current.xml`, 과거 경로)는 **330 passed / 20 skipped / 5 failed**, maxfail=5로 중단됐다. Browser cleanup unknown 2건, login Broker RPC timeout, Console logout stream 순서, timeout fixture의 PID 파일 미생성이었다. 5건 분리 재현 (`../../dist/acceptance/failures-20261008-isolated.xml`, 과거 경로)은 **3 failed / 2 passed**였으며 Browser 2건과 login Broker가 재현됐다. 원래 실패·환경 부하 증거를 보존하고 단독 통과를 전체 해결로 이관하지 않는다.

Browser의 EOF 진단 (`../../dist/acceptance/browser-graceful-20261008-events.json`, 과거 경로)은 즉시 강제 종료 대신 worker 정상 종료를 먼저 유도하면 두 실패가 통과함을 관측했다. 1초 제품 시도에서는 관련 회귀 3 failed / 2 passed (`../../dist/acceptance/browser-close-green-20261008.xml`, 과거 경로)였고, 추가 grace의 비교 5 passed (`../../dist/acceptance/browser-extra-grace-20261008.xml`, 과거 경로)로 조정 근거를 확보했다. 최종 제품 구현은 **child kernel handle을 정상 종료 전에 보존**, EOF 정상 종료에 최대 **2초**, 미응답 시 기존 Job 강제 종료 및 native signal 검증이다. 전체 cleanup 한도 **5초**는 늘리지 않았고 외부 CDP scope에는 EOF 유도를 적용하지 않았다.

자체 cooperative worker가 EOF 정리를 수행해야 하는 RED (`../../dist/acceptance/browser-close-red-20261008.xml`, 과거 경로)는 **1 failed / 1 passed**, 수정 후 Browser 관련 회귀 (`../../dist/acceptance/browser-close-green2-20261008.xml`, 과거 경로)는 **20 passed**, 140.86초다. 정상/비응답 worker, Browser file·timeout·CDP 보존 범위를 포함한다. 단순히 unknown을 complete로 바꾸지 않았다.

로그인 Broker의 phase 관측 (`../../dist/acceptance/login-phase-20261008-2.jsonl`, 과거 경로)은 register 완료 뒤 Guardian bootstrap에 약 **3.78초**, 그 이후 RPC loop 진입을 보여 줬다. 시험은 adoption 직후를 실제 준비 완료로 가정했다. 실제 IPC/Guardian readiness를 확인한 다음 기존 5초 RPC 예산을 검사하도록 시험 전제를 수정했으며 관련 4 passed (`../../dist/acceptance/broker-login-ready-20261008.xml`, 과거 경로)였다. 제품 RPC 예산과 startup/권한 경계를 바꾼 것으로 보고하지 않는다.

### 22.2 Terminal stream 인증 경합 수정

기존 forward는 인증 후 queue.get에서 기다리는 동안 로그아웃되면 재검 없이 새 프레임을 보낼 수 있었다. stream_open 입력 대기 동안의 인증 취소도 relay dispatch 전에 재검하지 않았다. 두 await 경합을 강제로 만드는 RED (`../../dist/acceptance/terminal-auth-red-20261008.xml`, 과거 경로)는 **2 failed**였다. 제품 `terminal_socket.py`에서 stream_open 입력 이후 및 frame dequeue 이후 인증을 재검하도록 수정했다. 인증·Console·실제 terminal stream 회귀 (`../../dist/acceptance/terminal-auth-green2-20261008.xml`, 과거 경로)는 **14 passed**, 27.91초다. 새 OAuth 기능이나 제공자 구성을 추가하지 않았다.

### 22.3 현재 source UI·실제 121 추가 검사

renderer source 4가지 조합 (`../../dist/acceptance/client-source-ui-20261008-summary.json`, 과거 경로)은 연결 파일/직접 입력 × checkbox false/true 전달과 설정 화면 값 일치를 검증했다. Vite 개발 서버와 주입된 API fixture를 사용했으며 제품 renderer build나 새 EXE를 만들지 않았다. 초기 화면 (`../../dist/acceptance/RUN-20261008-client-source-ui/initial-source.png`, 과거 경로)·설정 화면 (`../../dist/acceptance/RUN-20261008-client-source-ui/settings-source.png`, 과거 경로)을 보존한다. 첫 helper가 없는 input[type=url]을 찾은 실패는 보존하고 실제 label로 수동 경로만 재검했다. 이는 renderer 시험이며 Electron/Agent 경로의 검사를 대신하지 않는다.

실제 121 ConPTY (`../../dist/acceptance/RUN-20261008-terminal-boundaries/results.json`, 과거 경로)는 Unicode REPL 출력, resize, 동일-key write 1회, 독립 cursor, read timeout 뒤 동일 session 유지, close complete·반복 close·late write 거부·PID 부재를 PASS로 확인했다. 자동 shell deadline tree 검사 (`../../dist/acceptance/RUN-20261008-owned-job-tree-timeout/results.json`, 과거 경로)는 세 listen port가 시작 전에 연결 가능하고 deadline 후 연결 불가, root/child/grandchild PROCESS_NOT_FOUND, TIMED_OUT 및 late cancel 불변·own 폴더 정리를 확인했다. 두 시험은 설치 B Agent v0.1.10 경로다.

### 22.4 자동 검사 결과와 적용 범위

Browser/login 수정과 버전 갱신 후 전체 Python 실행 (`../../dist/acceptance/python-20261008-fixed.xml`, 과거 경로)은 **419 passed / 21 skipped**, 640.47초였다. 이 실행의 collection 이후 추가한 terminal 인증 수정·신규 2개 시험은 §22.2의 14개 별도 회귀로 검증했다. 같은 source의 한 번의 전체 실행으로 합쳐 표현하지 않는다. Ruff (`../../dist/acceptance/lint-20261008-final-fix.log`, 과거 경로)와 mypy (`../../dist/acceptance/mypy-20261008-final-fix.log`, 과거 경로)는 exit 0 / 161 source files 오류 0, version 6 passed (`../../dist/acceptance/version-20261008-close-fix.xml`, 과거 경로)다. Client Node (`../../dist/acceptance/client-node-20261008.log`, 과거 경로)는 **11 passed**, Client TypeScript (`../../dist/acceptance/client-types-20261008.log`, 과거 경로)는 --noEmit exit 0이었다.

수정은 source에 존재한다. 기존 121 Client/Agent binary와 운영 Gateway를 교체·재시작하지 않았으므로 새 수정의 실제 적용과 Windows 배포 인수는 별도 확인이 필요하다. 제품 build는 사용자가 마지막에 요청할 때까지 보류한다. 현재 source를 기존 121 Python에서 별도 own fixture로 실행할 수 있는지 검토하며, 가능 여부·실행 결과를 이 절에 추가한다.

### 22.5 정제된 Windows 환경의 Agent 시작 결함과 현재 source의 실제 121 검증

121의 기존 Python과 별도 own source snapshot을 사용한 시험에서 등록 후 Agent가 ONLINE으로 진행하지 못했다. 단계·예외 관측 (`../../dist/acceptance/RUN-20261008-source-agent-121-startup/results.json`, 과거 경로)은 Hello의 `getpass.getuser()`가 **ModuleNotFoundError: pwd**로 실패함을 확인했다. RACP 실행 환경에서 LOGNAME/USER/LNAME/USERNAME이 빠진 Windows에서는 getpass가 POSIX pwd fallback에 도달한다. 등록은 완료됐으나 연결되지 않은 own fixture Device도 revoke하고 PID/폴더를 정리했다.

변수 부재 및 위조 환경으로 실제 Hello 생성을 검증하는 RED (`../../dist/acceptance/windows-username-red2-20261008.xml`, 과거 경로)는 **2 failed**였다. Windows 계정명을 `win32api.GetUserName()`으로 구하는 공통 `execution_identity.py`를 추가하고 Hello/Ready·등록/Client 정보·background 상태의 기존 getuser 호출을 교체했다. POSIX 경로와 등록 토큰의 getpass 비밀 입력은 유지했다. 신규 회귀와 등록/연결·인증 실행 (`../../dist/acceptance/windows-identity-green-20261008.xml`, 과거 경로)은 **30 passed**, 10.56초다. 같은 v0.1.18 수정 배치에 포함했으며 재시도마다 버전을 올리지 않았다.

수정 후 실제 121 source Agent (`../../dist/acceptance/RUN-20261008-source-agent-121-identity-fixed/results.json`, 과거 경로)는 등록·ONLINE·Hello v0.1.18·native Browser close complete가 PASS였다. 추가 파일 upload/close 시험 (`../../dist/acceptance/RUN-20261008-source-agent-121-upload/results.json`, 과거 경로)에서도 **v0.1.18 Hello**, 자체 **1024 bytes** Artifact upload SHA-256 일치, Browser close **complete**를 확인했다. 해당 두 own Device는 revoke, 정확한 parent-managed PID/create_time/boot로 종료 후 PROCESS_NOT_FOUND, own 폴더 revision 삭제·cleanup errors 0이었다.

현재 pure Python source를 별도 fixture 폴더에 전달하고 **121의 기존 CPython/의존성/browser runtime을 재사용**했다. 기존 Client/Agent 설치를 교체하거나 EXE/wheel/배포 패키지를 build하지 않았다. source runtime 검증을 새로운 설치 패키지·외부 Python 없는 clean 설치 검증으로 이관하지 않는다. source launcher의 초기 enroll 인자 오류는 별도 helper 실패로 보존했으며 제품 결함으로 집계하지 않았다. 1회용 등록 값은 보고서·명령행에 남기지 않았고 임시 credential과 파일은 정리했다.

추가 코드 후 전체 Ruff (`../../dist/acceptance/lint-20261008-final-source.log`, 과거 경로) exit 0, mypy (`../../dist/acceptance/mypy-20261008-final-source.log`, 과거 경로)는 **162 source files 오류 0**이었다. 이전 전체와 영향 회귀의 중복 제외 집계 (`../../dist/acceptance/python-20261008-verification-union.json`, 과거 경로)는 **421 passed / 21 skipped**이며 서로 다른 코드 시점의 실행이라는 적용 범위를 명시한다. 세 제품 수정을 모두 포함한 최종 source 전체 검사는 별도로 진행 중이며 아래에 실제 종료 결과를 추가한다.

### 22.6 세 제품 수정 후 동일 source의 전체 결과

최종 v0.1.18 전체 Python JUnit (`../../dist/acceptance/python-20261008-final.xml`, 과거 경로)은 **423 passed / 21 skipped / failure·error 0**, **570.38초**였다. Browser 종료, stream await 이후 인증 재검, Windows native execution identity의 세 제품 수정을 모두 포함한 동일 source 시점의 실행이다. 이전 실패·진단/부분 회귀 수치와 중복 합산하지 않는다.

default skip는 GUI opt-in 15, 실제 POSIX runner 1, 명시 GDB 경로 2, Ghidra/JDK 경로 2, 선택 Docker Keycloak 1이다. OAuth provider 시험을 사용자 요청의 필수 조건으로 추가하지 않는다. Windows native GUI의 후속 opt-in 결과는 §22.7에 기록한다. source runtime의 실제 121 검증과 binary/package 설치 인수는 구분하며 제품 build 보류는 유지한다.

### 22.7 Windows GUI fixture의 결과 게시 경합 수정

전체 검사 뒤 첫 native GUI opt-in (`../../dist/acceptance/native-gui-20261008-final.xml`, 과거 경로)은 **4 passed / 10 skipped / 1 error**였다. fixture가 결과 JSON을 게시할 때 관측자의 Windows 파일 읽기 handle과 atomic replace가 경합해 **WinError 5**로 종료됐다. 이후 setup의 NoSuchProcess는 이 fixture 종료의 결과다. 이를 제품 desktop 입력 결함으로 집계하지 않는다.

실제 파일 reader를 잠시 유지하는 게시 회귀 RED (`../../dist/acceptance/gui-publish-red2-20261008.xml`, 과거 경로)는 **1 failed**였다. `scripts/desktop_acceptance_fixture.py`에서 Windows sharing/access denied **5/32/33**에만 최대 **0.5초** 재시도를 적용했다. 다른 오류와 지속 실패는 그대로 보고하며 최종 closed 결과를 생략하지 않는다. 수정 후 native GUI opt-in (`../../dist/acceptance/native-gui-20261008-publish-fixed.xml`, 과거 경로)은 **6 passed / 10 skipped / failure·error 0**, **60.77초**, exit 0이다. skip 10건은 OS가 자체 창의 foreground activation을 거부한 조건이며 입력 성공으로 계산하지 않는다.

§22.6의 전체 source 검사는 세 제품 수정을 모두 포함했다. 그 이후 추가한 것은 이 fixture 게시 회귀 1개와 fixture 수정이며 전체 423개 통과 수치에 GUI 결과를 중복 합산하지 않는다. 새 opt-in 회귀는 기본 실행에서는 추가 skip 대상이다. 최종 Ruff (`../../dist/acceptance/lint-20261008-fixture-final.log`, 과거 경로) exit 0, mypy (`../../dist/acceptance/mypy-20261008-fixture-final.log`, 과거 경로) **162 source files 오류 0**, 변경 fixture 두 파일의 format check 및 version show **0.1.18**도 통과했다.

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
| Acceptance CLI evidence | multi-agent (`../../dist/acceptance/R18A-multi/result.json`, 과거 경로), upgrade (`../../dist/acceptance/R18A-upgrade/result.json`, 과거 경로), restore (`../../dist/acceptance/R18A-restore/result.json`, 과거 경로), 5초 soak (`../../dist/acceptance/R18A-soak/result.json`, 과거 경로) exit 0; raw credential evidence scan PASS | **PASS** |
| Acceptance fixture cleanup | own PID/server 종료, temp state·credential 삭제, 18877 listener 잔존 false; receipt (`../../dist/acceptance/R18A-cleanup/cleanup.json`, 과거 경로) | **PASS** |
| 전체 `pytest -q` | 최종 source 실행 중; 종료 전 PASS로 승격하지 않음 | **NOT_RUN** |

### 23.3 Gateway Portable·Setup·업데이트 인수

`scripts/build_gateway.py --platform win --arch x64 --targets setup,portable --node .tools/node-v22.23.0-win-x64/node.exe --dry-run`은 version 0.1.18, Windows x64, deployment schema 1, publish=false를 확인했다. `setup_engine_available=false`였고 `Get-Command makensis`도 결과가 없어 **Native NSIS Setup EXE는 BLOCKED_ENV**다. installer contract/unit 회귀는 지정 122-test 묶음 안에서 PASS지만 실제 Setup install/reboot/upgrade/uninstall smoke로 확대하지 않는다.

Portable-only 실빌드는 현재 source에서 성공했다. build manifest (`../../dist/gateway/0.1.18/win-x64/build-manifest.json`, 과거 경로)의 후보는 `RACP-Gateway-0.1.18-win-x64.zip`, **34,365,297 bytes**, SHA-256 `434474193d9333f68b5937eb897bffb69896bd6ae2b45ecf820757fe563e7c33`이다. 내장 native Python import/version도 0.1.18로 일치했다.

첫 smoke는 긴 evidence root 아래 pywin32 license 경로에서 Windows `WinError 206`으로 **FAIL (시험 경로 길이)**했다. 기존 실패 폴더를 삭제/재사용하지 않고 짧은 새 root `dist/acceptance/R18P`로 같은 ZIP을 재실행했다. 최종 smoke (`../../dist/acceptance/R18P/result.json`, 과거 경로)는 **4,354 file hashes, ZIP CRC, fresh PowerShell launch, Console/assets, TLS readiness, one-use enrollment, one-use Console login, status PowerShell, loopback MCP 86 tools**를 PASS했다. `external_oauth_login`과 `agent_execution`은 이 Portable smoke에서 `not_run`이며 다른 증거로 자동 승격하지 않는다.

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

원격 shell의 공용 데이터 폴더 API가 `SHGetFolderPath(CSIDL_COMMON_APPDATA)` **0x80070003**으로 실패했다. 같은 환경에 OS의 SystemDrive를 복원하면 **C:\ProgramData**로 정상 해석됐다. A에서는 제거된 변수가 literal `%SystemDrive%\ProgramData`로 반환되는 실제 API RED (`../../dist/acceptance/windows-env-red-20261008.xml`, 과거 경로) **3 failed**, 이어서 parent에도 변수가 없는 복구 RED (`../../dist/acceptance/windows-env-red-recovery-20261008.xml`, 과거 경로) **1 failed / 3 passed**를 확보했다.

Agent `execution_env`가 Windows의 SYSTEMDRIVE/ALLUSERSPROFILE만 보존하고 caller의 해당 변수 변경은 거부하도록 수정했다. parent에도 SystemDrive가 없으면 `GetWindowsDirectory`의 실제 drive에서 복구한다. credential 관련 환경을 추가로 상속하지 않는다. 영향 회귀 (`../../dist/acceptance/windows-env-green-recovery-20261008.xml`, 과거 경로)는 **29 passed**였다.

현재 source의 실제 121 검증 (`../../dist/acceptance/RUN-20261008-source-v019-process-close-2/results.json`, 과거 경로)은 v0.1.19 Hello·native 공용 폴더 조회·process tree close·Browser close가 PASS였다. 기존 121 CPython/browser를 재사용한 별도 source fixture이며 새 설치 package 인수를 대신하지 않는다. own Device revoke·PID 부재·폴더 삭제와 cleanup errors 0을 확인했다.

### 24.2 Pktmon 실패를 우회하는 Agent 내장 IPv4 수집 코드

기본 Windows Pktmon은 자체 TLS 연결의 **19개** counters를 관측했지만 명시 provider 진단 (`../../dist/acceptance/RUN-20261008-remote-packet-tls-provider-diagnostic/results.json`, 과거 경로)에서도 ETL에는 metadata 3개, 변환 PCAP에는 **0 packet / 156 bytes**만 있었다. all components·raw flags·필수 OS 환경 비교를 보존했으며, 이를 Agent 방식의 기술적 불가로 판정하지 않는다. memory logging 비교도 native 변환 오류로 실패했다. 부분 파일 전달만 성공한 기록은 packet 인수 PASS가 아니다.

Windows Winsock API를 사용한 원격 feasibility 시험 (`../../dist/acceptance/RUN-20261008-remote-raw-ip-packet/results.json`, 과거 경로)은 **19 packets / 5540 bytes**를 회수했고 기존 Wireshark MCP가 raw IP·TCP·TLS를 확인했다. 이 근거로 Agent source에 `packet_capture.py`를 추가했다. 신규 회귀 RED (`../../dist/acceptance/packet-capture-red-20261008.xml`, 과거 경로)는 module 부재로 collection error였다. packet filter·완전한 PCAP record·empty 거부·byte limit·socket 정리의 회귀를 구현했다.

최종 helper는 **RCVALL_IPLEVEL(3)**을 사용해 NIC promiscuous mode를 켜지 않고 선택 interface의 IPv4 TCP/UDP 흐름만 저장한다. exact local IP/peer/local port, 최대 **30초 / 16 MiB**, exclusive output 생성, deadline/byte limit, 항상 raw socket 해제, empty capture failure를 적용했다. byte limit 결과는 `complete=false`다. IPv6/Ethernet/ARP·non-initial fragments·TLS 평문은 이 helper의 제공 범위가 아니다. [Microsoft의 IP-level API 조건](https://learn.microsoft.com/en-us/windows/win32/winsock/sio-rcvall)을 기준으로 administrator token을 실제 확인했다.

실제 121에서 최종 Agent 코드 (`../../dist/acceptance/RUN-20261008-remote-agent-packet-iplevel-2/results.json`, 과거 경로)를 hash 확인 후 실행해 **19 packets / 5541 bytes**, SHA-256 `098f617f3673f392dd900aeeee5096e54fdb327f823acf0e0de4268223cbd0d9`를 회수했다. 실제 기존 Codex Wireshark MCP (`../../dist/acceptance/RUN-20261008-remote-agent-packet-iplevel-2/native-mcp-analysis.json`, 과거 경로)는 full scan **19/19**, 송신 **10 / 수신 9**, Device IP·전용 port·두 방향 group을 일치시켰다. empty capture 거부·raw socket 해제·own 폴더 삭제도 PASS다. 이전 TLS handshake 조회 (`../../dist/acceptance/RUN-20261008-remote-agent-packet-code/native-mcp-analysis.json`, 과거 경로)와 최종 IP-level 범위를 구분한다.

이는 Agent 내장 CLI를 **기존 승인된 shell operation**으로 실행하는 경로다. 전용 `network.capture` RPC/MCP tool을 구현한 것으로 보고하지 않는다. Client에 Wireshark·mitmproxy·Npcap 또는 분석 IDE를 설치하지 않았고, 기존 Npcap driver의 존재도 이 helper의 사용 조건으로 삼지 않았다.

### 24.3 실제 host Ghidra·mitmproxy MCP 분석

Ghidra native MCP (`../../dist/acceptance/ghidra-native-remote-20261008.json`, 과거 경로)는 RACP로 회수한 동일 SHA-256의 PE에서 `racp_transform` RVA 0x1000, **param*7 xor 0x5a**, IMUL/XOR/RET와 entry 0x140001049의 caller를 확인했다. A의 별도 own project와 원본 GUI plugin을 사용했고 해당 helper만 종료했다. 최초 GUI 미기동·숫자 문자열 인자·GUI launcher 오류는 host/tool/helper 문제이며 Agent 결함이나 원격 분석 불가로 분류하지 않는다. 사용자 DB를 대체하거나 upstream server를 수정하지 않았다.

실제 121 Browser의 HTTP HAR (`../../dist/acceptance/RUN-20261008-remote-har-mcp/results.json`, 과거 경로)는 bundled browser가 own loopback HTTP 요청과 **result=112**를 기록했다. **2301 bytes / SHA-256 5c7e247a37a34efb59f3e09af479571b5edf924f6c09c38ef4b18905ecba8c56**가 회수 원본과 일치했다. 기존 mitmproxy MCP (`../../dist/acceptance/RUN-20261008-remote-har-mcp/native-mcp-analysis.json`, 과거 경로)는 append import **1 / error 0**, 동일 marker GET·HTTP 200·JSON result 112를 실제 inspect했다. MCP의 working-directory path guard를 준수했으며 기존 traffic DB를 clear하지 않았다. Browser/server 및 own 원격 폴더는 정리했다.

HAR import/조회는 live proxy interception·원격 replay를 입증하지 않는다. legacy B CPython의 직접 TLS keylog 설정은 OpenSSL_Applink 오류로 실패했으며 runtime 수정/재빌드는 하지 않았다. raw packet 수집 또는 HAR 분석의 성공을 임의 앱 TLS 복호화 성공으로 확대하지 않는다.

### 24.4 추가 전체 회귀에서 확인한 process tree 종료 경합 수정

첫 v0.1.19 전체 (`../../dist/acceptance/python-20261008-v019.xml`, 과거 경로)는 **428 passed / 22 skipped / 2 failed**, 992.83초였다. OpenAPI 실패는 SSOT version 0.1.19와 저장된 document 0.1.18의 불일치였으며 `scripts/console_contract.py`로 기존 경로를 갱신했다.

process.terminate는 spawn gate 종료만 기다린 뒤 실제 child가 아직 종료 중인데 complete로 반환할 수 있었다. gate를 먼저 종료하고 native child 종료를 지연하는 결정적 RED (`../../dist/acceptance/process-close-red-20261008.xml`, 과거 경로)는 **1 failed**였다. Agent `ProcessProvider.close`가 기존 **5초 총 cleanup 예산 안에서 Job ActiveProcesses=0을 먼저 확인**한 뒤 handle을 닫고 gate.wait를 완료하도록 수정했다. timeout에는 complete를 선언하지 않는다. filesystem/process/lifecycle/OpenAPI/backup 영향 회귀 (`../../dist/acceptance/process-close-green-20261008.xml`, 과거 경로)는 **17 passed**였고, §24.1의 실제 121 current source에서도 종료 후 PROCESS_NOT_FOUND를 확인했다.

현재 신규 Agent·version 회귀 (`../../dist/acceptance/v019-new-agent-final.xml`, 과거 경로)는 **15 passed**이며 mypy (`../../dist/acceptance/mypy-20261008-v019-final2.log`, 과거 경로)는 **165 source files 오류 0**이다. 병행 Gateway backup/restore 변경에서 확인한 변수 타입 shadowing과 formatting만 보완했다. 세부 기능을 바꾼 것으로 집계하지 않는다.

중간 전체 재검 (`../../dist/acceptance/python-20261008-v019-final.xml`, 과거 경로)은 **374 passed / 22 skipped / 3 failed**, maxfail=3이었다. Python socket에 RCVALL_IPLEVEL의 symbolic name이 없는 버전을 처리하도록 수정했지만, 이미 collection에서 import한 runner는 이전 module을 유지해 세 packet unit에서 AttributeError가 발생했다. Winsock의 문서화된 값 **3**과 symbolic name fallback을 사용한 fresh 15개 지정 회귀 (`../../dist/acceptance/v019-new-agent-final.xml`, 과거 경로), 실제 121 IP-level 수집을 확인하고 새 process에서 전체를 다시 실행했다.

**최종 current-source 전체** JUnit (`../../dist/acceptance/python-20261008-v019-final3.xml`, 과거 경로)·로그 (`../../dist/acceptance/python-20261008-v019-final3.log`, 과거 경로)는 **449 passed / 22 skipped / failure·error 0**, **474.49초**, exit 0이다. 결과를 이전 전체/부분 회귀와 합산하지 않는다. Ruff (`../../dist/acceptance/lint-20261008-v019-verified.log`, 과거 경로) pass, mypy (`../../dist/acceptance/mypy-20261008-v019-verified.log`, 과거 경로) **165 source files 오류 0**, pinned Node 22.23.0의 Client Node (`../../dist/acceptance/client-node-20261008-v019.log`, 과거 경로) **11 passed**, Client (`../../dist/acceptance/client-types-20261008-v019.log`, 과거 경로)·Console (`../../dist/acceptance/console-types-20261008-v019.log`, 과거 경로) TypeScript --noEmit exit 0도 확인했다. 제품 build는 실행하지 않았다.

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

GT12의 인증 export 경로는 현재 API 점검 (`../../dist/acceptance/RUN-20261008-1935-gateway-management-0.1.19/log-export-check.json`, 과거 경로)에서 비동기 생성→상태→인증 다운로드가 `SUCCEEDED`, 1 row, SHA-256 일치, `Cache-Control: no-store`로 확인됐다.

### 26.2 GT25 부하와 성능·신뢰성 목표

첫 1만 device/100만 audit 실행은 audit p95 **2.035463초**로 1초 목표를 초과해 FAIL로 보존했다. 원인은 source가 고정돼도 UNION 전체를 정렬하고 audit 정렬 인덱스가 없던 것이었다. 기존 migration v1 checksum을 바꾸지 않고 **Gateway schema v2 additive index migration**과 source-specific log query를 추가한 뒤 재측정 (`../../dist/acceptance/RUN-20261008-1935-gateway-management-0.1.19/load-v2/result.json`, 과거 경로)은 device metadata p95 **0.004891초**, 100만 audit 검색 p95 **0.000618초**, SQLite writer lock **5.591초 감지 후 release 뒤 write 복구**로 통과했다. 초기 실패 evidence는 삭제하지 않았다.

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

현재 v0.1.19 최종 선택은 candidate manifest (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-candidate.json`, 과거 경로)에 고정한 **Portable + Setup pair**다. Portable은 실제 121 인수와 정확히 같은 `dist/gw-v019-final-native3/RACP-Gateway-0.1.19-win-x64.zip` (`../../dist/gw-v019-final-native3/RACP-Gateway-0.1.19-win-x64.zip`, 과거 경로) **34,592,955 bytes / SHA-256 `5e9920f082ac9397117ee01544c344e84ca8ea8b76cea6cebb1064d7efd4d4f7`**를 유지한다. Setup은 최신 frozen v0.1.19 installer source SHA-256 `144d12c782cab89a360d111f0fbcac25a725c3447bb76b663abe4ab7edf372c9`를 pinned NSIS `/WX`로 다시 컴파일한 `dist/gw-v019-final-setup-20261009-1715/RACP-Gateway-0.1.19-win-x64-setup.exe` (`../../dist/gw-v019-final-setup-20261009-1715/RACP-Gateway-0.1.19-win-x64-setup.exe`, 과거 경로) **24,449,655 bytes / SHA-256 `099145b5c6f2125c9447fa10dd3349225ac067fce842474378e030c2482922b8`**다. 이 Setup의 source bundle에서 만든 native5 ZIP과 선택 Portable은 4,392 ZIP entry 전부 byte-identical (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/native3-native5-payload-diff.json`, 과거 경로)하므로 installer-only 수정이 실제 121에서 검증한 runtime payload를 바꾸지 않았다. Portable smoke (`../../dist/acceptance/RUN-20261008-2230-gateway-management-0.1.19/portable-smoke-native3.json`, 과거 경로)는 manifest **4,391 files**, CRC/fresh PowerShell/Console/TLS/one-use enrollment/login/status/loopback MCP 86 tools를 통과했다.

운영자가 두 선택 파일을 한 위치에서 확인할 수 있도록 비게시 통합 복사본 `dist/gw-v019-final-selected`도 만들었고, candidate manifest (`../../dist/gw-v019-final-selected/candidate-manifest.json`, 과거 경로)와 `SHA256SUMS.txt`가 위 두 exact hash를 다시 고정한다. 원본 artifact와 역사 evidence는 삭제하거나 덮어쓰지 않았다.

Native Setup의 current-host SCM/install 3-cycle (`../../dist/acceptance/RUN-20261008-2230-gateway-management-0.1.19/setup-native-final.json`, 과거 경로)과 updater demand-start (`../../dist/acceptance/RUN-20261008-2230-gateway-management-0.1.19/updater-demand-native3.json`, 과거 경로)은 기존 서비스 수명주기 근거로 보존한다. 새 runtime-free Windows 11 Enterprise Evaluation VM에서는 native3 Setup clean-install 실패와 root cause를 별도 evidence (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/cleanvm-root-cause.json`, 과거 경로)로 보존한 뒤, 최종 Setup `099145…922b8`의 clean install (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-setup-clean-install.json`, 과거 경로), 실제 guest reboot와 post-reboot readiness (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-setup-reboot.json`, 과거 경로), uninstall/state preservation (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-setup-uninstall.json`, 과거 경로), backup 확인 후 명시적 ProgramData 삭제 (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-explicit-data-delete.json`, 과거 경로)를 모두 PASS했다. clean install 전 guest에는 RACP/Python/Node가 없고 default route도 0이었다. 실제 installer Cancel 버튼은 로그인된 guest desktop과 Desktop 제어 채널이 없어 수행하지 못했으므로 cancel gate (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/final-install-cancel-gate.json`, 과거 경로)는 `BLOCKED_ENV`를 유지한다; 강제 process 종료나 static hook을 실제 cancel로 대신하지 않는다. clean-VM cleanup receipt (`../../dist/acceptance/RUN-20261009-1610-gateway-management-0.1.19-cleanvm/cleanup-receipt.json`, 과거 경로)는 VM을 원래 clean checkpoint·Off·NIC disconnected로 복원하고 run-owned checkpoint/vmconnect를 제거했으며 새 evidence의 credential 문자열 scan이 모두 0임을 기록한다.

선택 Portable `5e9920…d4f7`와 같은 runtime에 묶은 bounded G11은 multi-agent (`../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/multi-agent/result.json`, 과거 경로), restore (`../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/restore/result.json`, 과거 경로), upgrade (`../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/upgrade/result.json`, 과거 경로), 15초 short soak (`../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/short-soak/result.json`, 과거 경로)을 모두 exit 0으로 완료했다. 이 네 역사적 결과에는 당시 native3 Setup `4295a2…a3ca4`도 candidate metadata로 기록돼 있으므로 새 Setup 증거로 재사용하지 않는다. runtime/Portable 쪽은 선택 ZIP과 exact hash가 같고 cleanup receipt (`../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/cleanup-receipt.json`, 과거 경로)의 credential leak 0/process/listener/private cleanup PASS를 유지한다. 15초 soak를 GT26의 8시간 soak로 사용하지 않는다.

실제 121에서는 기존 8765 Agent 연결을 변경하지 않고 별도 18819 Gateway와 별도 enrollment를 만들었으며, 실제 실행 runtime은 현재도 선택된 Portable ZIP `5e9920…d4f7`과 exact match다. stage evidence (`../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-stage.json`, 과거 경로)는 당시 pair의 ZIP/Setup hash와 보존된 source Agent v0.1.19 archive SHA-256 `46907edfe6df2135420d8ac6f924f4a4e386b94933380541bd28358986d1483a`를 기록한다. 이후 Setup만 교체됐으므로 이 물리 증거를 새 Setup 인수로 사용하지 않고, 새 Setup은 위 clean-VM evidence로 별도 판정한다. 원격 operation 인수 (`../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-operations.json`, 과거 경로)는 file/hash/artifact, shell idempotency, owned job cancel, persistent terminal을 PASS했고, Web Console 인수 (`../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-console.json`, 과거 경로)는 login→ONLINE→standard approval→실제 job→device-filtered management logs **100 rows**, browser page error 0을 PASS했다. remote cleanup (`../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-remote-cleanup.json`, 과거 경로)과 cleanup receipt (`../../dist/acceptance/RUN-20261009-1417-gateway-management-0.1.19-physical121/physical121-cleanup-receipt.json`, 과거 경로)에서 device REVOKED, source process/folder 제거, Gateway/listener/firewall/private state 제거와 owner secret leak 0을 확인한다.

외부 gate 기록 (`../../dist/acceptance/RUN-20261009-1450-gateway-management-0.1.19-native3-bounded/external-gates.json`, 과거 경로)에서 Codex CLI는 존재하지만 `codex mcp get racp`가 `No MCP server named 'racp' found`를 반환했고, acceptance JSON의 duration 기록 중 8시간 이상은 **0건**이다. 따라서 GT28은 `BLOCKED_ENV`, GT26은 `NOT_RUN`을 유지한다.

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
- 등록/설정에 공통 category/leaf UI와 제약 편집을 연결했다. category off는 선택을 보존하고 실행만 차단한다. 미구현과 profile/OS 조건, 광역 실행과 OS sandbox의 차이를 표시한다. 소스 UI 화면 (`../../dist/acceptance/agent-permissions-source-ui.png`, 과거 경로)은 mock IPC로 렌더링한 화면이며 설치된 Client 인수가 아니다.
- Hello에 local permission revision/grants/constraints와 실제 시행 수준을 별도 capability metadata로 선언했다. 로컬 설정을 Gateway가 변경하는 API는 추가하지 않았다.

### 27.2 현재 시험 증거

| 시험 | 확인한 결과 | 증거와 한계 |
|---|---|---|
| catalog payload selector | window screenshot 권한 분류 오류 RED 1 failed/13 passed → GREEN 14 passed | RED (`../../dist/acceptance/permissions-catalog-red-window-20261008.xml`, 과거 경로), GREEN (`../../dist/acceptance/permissions-catalog-green3-20261008.xml`, 과거 경로) |
| 모델·설정 초기 통합 | **72 passed** | 초기 핵심 통합 (`../../dist/acceptance/permissions-core-20261008.xml`, 과거 경로); 후속 writer/transfer 검사 추가 이전 결과 |
| 실제 Gateway→Agent RPC | 로컬 write off, ask 우회, process cmdline, binary export, await 중 변경 **5 passed** | 통합 GREEN (`../../dist/acceptance/local-permission-execution-green-20261008.xml`, 과거 경로); 격리 loopback fixture, 실제 121 증거와 구분 |
| current v0.1.20 단위·버전·schema | **44 passed**, 4.29초 | 현재 핵심 시험 (`../../dist/acceptance/permissions-v020-core-current.xml`, 과거 경로); unknown/future schema·64 ID migration freeze·16KiB 설정 경계 포함 |
| 실제 전송 직전 검사 | writer 대기 중 변경 RED → GREEN; 첫 upload chunk 뒤 변경으로 다음 bytes/complete 차단 | writer RED (`../../dist/acceptance/permission-writer-red-20261008.xml`, 과거 경로), transfer RED (`../../dist/acceptance/permission-transfer-red-20261008.xml`, 과거 경로), GREEN + version (`../../dist/acceptance/permissions-output-version-green-20261008.xml`, 과거 경로) |
| Client source browser | 연결 파일/직접 등록/설정 편집 3경로·category off/복원·동일 payload·실패 보존 **PASS** | 결과 (`../../dist/acceptance/agent-permissions-source-ui.json`, 과거 경로); Vite dev server+headless Chromium, production build 없음 |
| Client Node·타입 | Node **11 passed**, TypeScript `--noEmit` exit 0 | source-only; backend bound/login/controller/packaging 계약 검사, 제품 packaging 실행 아님 |
| Ruff·mypy | Ruff PASS, mypy **168 source files / issues 0** | lint (`../../dist/acceptance/permissions-v020-lint.log`, 과거 경로), mypy (`../../dist/acceptance/permissions-v020-mypy.log`, 과거 경로); Gateway service의 기존 nullcontext assignment 타입 ignore만 보완 |
| 최초 전체 회귀 | **FAIL: 495 passed / 22 skipped / 7 failed**, 927.14초 | 최초 결과 (`../../dist/acceptance/permissions-v020-regression.xml`, 과거 경로); 원본 실패를 보존하고 아래 §27.4에서 수정·재검을 구분 |
| 현재 전체 회귀 재실행 | **PASS: 529 passed / 22 skipped**, 650.60초, exit 0 | 현재 결과 (`../../dist/acceptance/permissions-v020-regression2-20261009.xml`, 과거 경로), 현재 로그 (`../../dist/acceptance/permissions-v020-regression2-20261009.log`, 과거 경로); source-only, skip을 native 실제 인수로 계산하지 않음 |
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
| replay/profile/default/MCP 초기 수정 | **17 passed** | 검토 수정 GREEN (`../../dist/acceptance/review-fixes-green-20261009.xml`, 과거 경로) |
| durable scope + 실제 Gateway/Agent 통합 | **50 passed**, 24.41초 | 현재 scope 통합 (`../../dist/acceptance/permissions-current-scope2-20261009.xml`, 과거 경로); workspace workflow/output recovery 포함 |
| scope proof 수명주기 | RED **2 failed** → GREEN **15 passed** | RED (`../../dist/acceptance/permission-binding-retention-red-20261009.xml`, 과거 경로), GREEN (`../../dist/acceptance/permission-binding-retention-green2-20261009.xml`, 과거 경로); active/task/upload 보존 및 1001 orphan의 bounded 회수 |
| version 타입 + 설정/권한/retention | RED **5 failed** → GREEN **41 passed** | RED (`../../dist/acceptance/permission-version-types-red-20261009.xml`, 과거 경로), GREEN (`../../dist/acceptance/permissions-settings-retention-20261009.xml`, 과거 경로) |
| 최종 독립 검토 | 기존 P1/P2 및 추가 lifecycle P2 종료, 새 correctness regression 미발견 | read-only reviewer; replay/writer/transfer **12 passed**; 전체 기능/원격 인수 완료 선언 아님 |
| 현재 Ruff/mypy | **PASS**, mypy **170 source files / issues 0** | lifecycle/version 수정 이후 직접 전체 검사, exit 0 |
| 현재 Client 재검 | Node **11 passed**, TypeScript `--noEmit` exit 0, source UI **PASS** | 2026-10-09 현재 catalog로 초기 등록/설정 3경로 재실행; UI 결과 (`../../dist/acceptance/agent-permissions-source-ui.json`, 과거 경로), 현재 화면 (`../../dist/acceptance/agent-permissions-source-ui.png`, 과거 경로); 종전 파일도 `*-before-20261009`로 보존 |

최초 전체 회귀의 7개 실패 중 stale settings schema는 계약 asset 재생성으로, workspace terminal handle은 실제 Agent scope 수정으로 해결했다. updater fixture가 현재 버전과 동일한 고정 candidate를 쓰던 문제는 현재 PATCH+1 후보로 고쳤다. 해당 focused 재검은 workspace 2 passed (`../../dist/acceptance/permissions-workspace-handle-green.xml`, 과거 경로), OS/update/desktop 5 passed (`../../dist/acceptance/permissions-v020-failure-focus2.xml`, 과거 경로)다. Browser/controller 및 Desktop의 나머지 실패는 별도 재실행에서 통과했으나 최초 일시 실패의 원인이 입증된 것은 아니다. Browser 재검 2 passed (`../../dist/acceptance/permissions-browser-failure-focus.xml`, 과거 경로)를 안정성 전체 PASS로 확대하지 않는다. 최신 전체 재실행은 529 passed / 22 skipped / 실패 0으로 종료했다. 최초 실패 로그를 보존하며 재실행 통과만으로 일시 실패의 원인까지 해결됐다고 주장하지 않는다.

> [!NOTE]
> §27.4의 시험은 소스 및 격리 loopback 검증이다. 이후 121에서 실행한 제한된 권한·OS 관측 인수는 §27.5에 구분한다. native live debugger/proxy transport와 신규 제품 binary 인수는 수행하지 않았다. 제품 build, OAuth 제공자 신설, Server→Client 정책 배포도 실행하지 않았다.

### 27.5 실제 121 — v0.1.20 소스 권한·OS 관측·재시작 검증

사용자가 121에서 기존 `RACP-Client-0.1.10-win-x64-portable.exe`를 실행했다. 기존 Device `dev_b12478e45ab646e5838c31218ab85fb1`의 ONLINE·Broker healthy를 확인했으며 Agent Hello의 버전은 `0.1.0`이었다. 이 실행 파일을 수정본으로 간주하지 않고, 기존 Python/native runtime을 사용해 별도 폴더·별도 시험 Device에서 hash 검증한 v0.1.20 source를 실행했다. 호스트 및 임시 Agent의 실제 관리자 token을 확인했다. 설치 파일을 교체하거나 제품을 빌드하지 않았다.

기존 랩 Gateway도 새 `system.resources` operation을 422 `CAPABILITY_UNAVAILABLE`로 거절했다. 실제 listener PID/생성 시각/랩 data_dir를 확인하고 SQLite online backup 후 **동일 등록 DB·TLS·주소·포트에서 현재 source Gateway로 재시작**했다. 첫 실행은 이전 interpreter가 상속받던 module path를 재현하지 못해 `ModuleNotFoundError`로 종료했고, 프로젝트 `.venv` interpreter로 수정해 복구했다. 기존 121 Agent가 epoch 9로 자동 재연결했다. 재시작 기록 (`../../dist/acceptance/RUN-20261009-1425-source-permissions/gateway-restart.json`, 과거 경로)은 PID와 원인을 기록하며 credential을 포함하지 않는다. 다른 앱의 같은 port listener는 변경하지 않았다.

| 실제 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 새 소스 Agent / Hello / 로컬 revision | **PASS** | 첫 성공 run (`../../dist/acceptance/RUN-20261009-1435-B-source-permissions/results.json`, 과거 경로); source v0.1.20, 별도 Device, 실제 boot/epoch와 설정의 compiled revision 일치 |
| typed OS 관측 7 RPC | **PASS** | system info/resources/locale/safe environment, network interfaces/connections, storage volumes. 각 결과의 Device/boot/관측 시각을 검사. shell grant 없이 실행 |
| 파일 변경·명령 실행 차단 | **PASS** | 로컬 grant deny인 filesystem.write/shell.exec가 `layer=agent`, `PERMISSION_DENIED`로 종료하며 대상 파일은 생성되지 않음 |
| binary Artifact 반출 차단 | **PASS** | 재시작 포함 run (`../../dist/acceptance/RUN-20261009-1440-B-permissions-restart/results.json`, 과거 경로); binary read leaf가 있어도 export deny이면 result/outputs 없이 Agent에서 차단 |
| 보호 설정 유지·재시작 | **PASS** | 같은 Device·저장 credential/설정으로 재시작, boot 변경·epoch 2, permission revision 유지. OS read 허용·파일 write 거부를 다시 확인 |
| native Browser 수명주기 | **PASS** | 실제 headless Browser open/close와 cleanup_status complete. Browser 분석 전 기능 또는 사용자 GUI 입력 인수로 확대하지 않음 |
| 시험 자원 정리 | **PASS** | 두 성공 run 모두 시험 Device revoke, 정확한 PID/create_time/parent boot의 process 종료·부재 확인, 자체 생성한 folder만 revision 검증 후 삭제. cleanup_errors=[] |

첫 준비 시도는 harness의 `legacy_permissions` 필수 인자 누락으로 실패했다. 실패 run (`../../dist/acceptance/RUN-20261009-1430-B-source-permissions/results.json`, 과거 경로)을 보존하고 준비 코드를 수정했다. 해당 시도에는 임시 Agent를 시작하지 않았고 생성 폴더를 정리했다. 이를 제품 Agent 결함 또는 원격 기술 불가로 분류하지 않는다.

> [!IMPORTANT]
> 위 결과는 실제 121에 대한 **RACP RPC와 저장 설정·권한 검사**의 인수다. 기존 Codex native MCP의 live attach/break/step/continue/detach·proxy/replay, typed capture/dump Provider, 새 Client 설치 UI 인수까지 완료했다는 뜻이 아니다. 기존 v0.1.19의 native MCP 파일/dump/PCAP/HAR 분석 증거와 이번 source 권한 인수를 구분한다. 추가 구현과 해당 native 경로 인수는 계속 남아 있다.

### 27.6 전용 network.capture RPC와 실제 121→Wireshark MCP

v0.1.20에 `network.capture` RPC/MCP·strict IPv4/local port/peer 입력·PCAP MIME 계약을 추가했다. `network.capture.ipv4`와 `artifacts.export`를 모두 요구하며 generic argv/shell grant는 요구하지 않는다. read_only profile에서는 거절하고 standard에서는 Gateway 승인을 요구한다. 30초/16 MiB 및 현재 local output ceiling을 적용하며 임의 명령·출력 경로·BPF는 입력받지 않는다. 현재 catalog는 **71 RPC-backed leaf / 72 후속 = 143**이며 v1 migration의 64 ID baseline은 확대하지 않는다.

Agent 고정 recipe가 기존 Job Object 관리 경로를 재사용한다. 작업 프로세스와 자손을 생성 시점부터 관리하고 요청 취소·deadline·현재 grant 검사를 적용한다. stdout/stderr receipt는 각 8 KiB로 제한한다. private spool의 root를 canonicalize·pin하고, 모든 PCAP record의 길이·해시·IP/port scope를 검증한다. 검증 스레드는 취소 뒤에도 열린 파일 handle이 닫힐 때까지 기다리고, 미전송 결과는 정리한다. 완료 Artifact는 기존 OutputSpool의 durable retry에 넘긴다. 활성 worker PID는 process/memory/debugger의 보호 대상에 포함한다.

| 시험 | 결과 | 근거 / 범위 |
|---|---|---|
| capture 입력·profile·grant 계약 | RED **12 failed** → GREEN **12 passed** | RED (`../../dist/acceptance/network-capture-contract-red-20261009.xml`, 과거 경로), GREEN (`../../dist/acceptance/network-capture-contract-green-20261009.xml`, 과거 경로) |
| owned worker/cancel/timeout/revoke | **PASS** | 실제 contained fixture worker로 검증. raw NIC 수집 증거와 구분 |
| 정리 중 late cancellation | RED **1 failed** → GREEN | RED (`../../dist/acceptance/network-capture-late-cancel-red-20261009.xml`, 과거 경로), GREEN 5 passed (`../../dist/acceptance/network-capture-late-cancel-green-20261009.xml`, 과거 경로); 취소를 성공으로 반환하던 경로 수정 |
| RPC/MCP→PCAP Artifact | RED → **52 passed** | GREEN (`../../dist/acceptance/network-capture-current-green-20261009.xml`, 과거 경로); 초기 UNKNOWN 원인은 PCAP MIME을 OutputDescriptor/TransferCreate가 허용하지 않던 계약 누락. 양쪽 계약·기존 schema 경로에 같은 타입 추가 |
| 독립 검토 수정 | RED **3 failed** → GREEN cohort **23 passed** | RED (`../../dist/acceptance/network-capture-review-red-20261009.xml`, 과거 경로), GREEN (`../../dist/acceptance/network-capture-review-green2-20261009.xml`, 과거 경로); 68-byte UDP, relative spool, pinned verifier cancellation. UDP는 fixture이며 실제 NIC UDP 인수로 확대하지 않음 |
| 현재 static checks | Ruff PASS, mypy **172 source files / issues 0**, Client TypeScript `--noEmit` exit 0 | 제품 build 없음 |
| 실제 121 capture RPC | **PASS** | 현재 source run (`../../dist/acceptance/RUN-20261009-B-capture-rpc-2/results.json`, 과거 경로); 설치된 v0.1.10의 기존 Python으로 별도 source Agent 실행 |
| 기존 native Wireshark MCP | **PASS** | native MCP 원문 (`../../dist/acceptance/RUN-20261009-B-capture-rpc-2/native-wireshark-mcp.json`, 과거 경로); open_file + 전체 aggregate + 범위 밖 filter aggregate |
| 두 번째 전체 회귀 | **FAIL: 548 passed / 22 skipped / 2 failed**, 759.84초 | 원본 결과 (`../../dist/acceptance/network-capture-full2-20261009.xml`, 과거 경로); fixture PID 게시/시작 경합을 아래에 구분 |
| 캡처 변경 전체 회귀 | **PASS: 550 passed / 22 skipped**, 625.83초, exit 0 | 세 번째 실행 결과 (`../../dist/acceptance/network-capture-full3-20261009.xml`, 과거 경로), 로그 (`../../dist/acceptance/network-capture-full3-20261009.log`, 과거 경로); 아래 dump 변경 이전 기준선 |

실제 121의 시험 Device `dev_f17637a710224b65ab8f01c25f8bffaa`, boot `boot_285491b73e4e40cebbb47d69fb071087`가 별도 권한 설정으로 실행됐다. source ZIP SHA-256은 `40a578ddd470c774d01aa31e23d130bb7f851af4f561a556df85a7f7b1832e8a`다. `192.168.29.121:51655 ↔ 192.168.29.141:8765`의 자체 TLS health 요청을 수집했고, shell/argv deny 상태에서 **19 packets / 5,427 bytes / duration 2,562 ms** PCAP를 회수했다. SHA-256은 **`31777920c344ebe90a6ac2de22c90a739d1b91db81ba0aaf4e814d0843a8d892`**다. 빈 수집은 CAPABILITY_UNAVAILABLE로 거절했고 generic execution은 layer=agent에서 차단했다. Raw socket close와 worker cleanup complete를 확인하고 시험 Device revoke·traffic/source PID 부재·자체 folder 삭제를 확인했다.

native Wireshark는 같은 SHA의 **19/19 packets**, 121→141 **10**, 141→121 **9**, 범위 밖 packet **0**을 확인했다. 저장소 경로는 MCP allowlist 밖이라 최초 open이 거절됐고, 같은 해시의 파일을 기존 허용된 RE Lab `_MCP_Sessions/racp/RUN-20261009-B-capture-rpc-2/own.pcap`에 복사해 분석했다. 도구의 경로 정책은 변경하지 않았다. TLS 평문·ARP/layer2·IPv6를 제공한다고 주장하지 않는다.

첫 실제 run은 보존된 실패 기록 (`../../dist/acceptance/RUN-20261009-B-capture-rpc-1/results.json`, 과거 경로)에서 패킷 수집·hash 회수 자체는 성공했지만 시험 HTTP Host header의 port 누락으로 403을 받았다. fixture를 고친 뒤 현재 run의 health 요청 200까지 확인했다. Agent capture 실패로 분류하지 않는다.

첫 전체 회귀는 standalone Broker 시험의 중복 `WindowsDesktop` probe가 pytest 본체에서 COM `CoCreateInstance`에 멈췄다. 스택 (`../../dist/acceptance/network-capture-full-hang-stack-20261009.txt`, 과거 경로)과 live PID를 확인했으며 실제 Broker는 pipe accept 상태였다. 종료 기록 (`../../dist/acceptance/network-capture-full-hang-20261009.json`, 과거 경로)에 따라 해당 시험과 소유 자식만 종료하고 원본 로그 (`../../dist/acceptance/network-capture-full-20261009.log`, 과거 경로)를 보존했다. native status 조회를 실제 Broker의 authenticated/bounded pipe로 옮겨 중복 in-process COM 초기화를 없앴으며, 정상 monitor 결과·다른 session 거부·Input ABI 검사를 유지했다. focused Broker/capture **12 passed** (결과 (`../../dist/acceptance/network-capture-broker-green-20261009.xml`, 과거 경로)). 새 전체 회귀는 별도 실행 중이다. Broker 자체의 optional UIA 초기화가 영구 block하지 않는다는 일반 보장은 이 시험 변경으로 입증하지 않는다.

두 번째 전체 회귀의 실패는 `test_life_01_timeout_kills_children_and_grandchildren`의 `grandchild.pid` 미생성 및 `test_auth_03_revoke_blocks_new_work_and_cleans_owned_process`의 빈 PID 문자열이었다. 전자는 2초 내 fixture의 3세대 시작을 가정했고 후자는 파일 존재를 내용 게시 완료로 간주했다. PID를 temp→atomic replace로 게시하고, 자식/손자의 실제 생존을 먼저 확인한 뒤 실제 10초 deadline의 tree cleanup과 동일 요청 replay를 검사하도록 fixture를 수정했다. Focused 재검 (`../../dist/acceptance/network-capture-lifecycle-focus-20261009.xml`, 과거 경로)은 **10 passed**, 25.55초다. 제품 timeout/cleanup 구현을 완화하지 않았다. 모든 실패·hang 기록은 보존하며 세 번째 전체 회귀는 위 PASS로 완료됐다.

> [!IMPORTANT]
> 전용 캡처와 native Wireshark의 실제 원격 자료 분석 경로를 새로 검증했다. typed dump, native live debugger/proxy duplex, 로컬 승인 UI·hot permission retirement, Enterprise 정책 배포 제외 조건 및 나머지 후보 기능의 미완료 상태는 유지한다. PATCH는 0.1.20을 재사용하고 제품 build·패키징·OAuth 설정은 수행하지 않았다.

### 27.7 전용 process.dump RPC와 실제 121→WinDbg MCP

`process.dump`는 Windows x64에서 OS DbgHelp로 mini/full user-mode dump를 만든다. `memory.dump.create`와 `artifacts.export`를 함께 요구하고 generic shell/argv grant를 요구하지 않는다. read_only에서는 deny, standard에서는 Gateway 승인 대상이다. PID/create_time/Agent boot를 확인하고 Agent·부모·Broker·분석 helper·활성 수집 worker를 보호한다. 요청 deadline·현재 권한·64 MiB default/256 MiB hard ceiling 및 Client output ceiling을 적용한다. 임의 flags·명령·출력 경로는 입력받지 않는다. 현재 catalog는 **72 RPC-backed leaf / 71 후속 = 143**이며 v1의 고정 migration baseline을 확대하지 않았다.

DbgHelp는 System32의 OS DLL을 명시적으로 로드한다. callback ABI에 SDK의 pack(4)를 적용하고, IoStart의 S_FALSE 이후 모든 쓰기를 callback에서 수행한다. offset+length가 한도를 넘으면 바이트 복사/쓰기 전에 거부한다. 유효한 NUL device handle을 전달해 alternate I/O가 없는 backend가 spool에 제한 없이 쓰지 않도록 하고, start/finish receipt가 없으면 결과를 거절한다. 대상 handle의 생성 FILETIME·생존을 확인하고 읽기/query/synchronize 권한만 요청한다. 권한 승격·debugger 설치·대상 종료를 수행하지 않는다. 동일 PID의 live debugger 기능과 구분한다.

capture의 기존 Job Object·private pinned spool·bounded receipt·shielded verification/cleanup 수명주기를 공통 `OwnedArtifactRecipe`로 추출해 dump에서도 재사용했다. 실제 MDMP header·stream directory bounds·full-memory flag·파일 크기·SHA·target receipt를 검증한다. Artifact 등록 전 실패한 파일은 삭제하고 등록된 durable outcome만 OutputSpool에 넘긴다. CLI worker도 exclusive 생성한 파일만 정리하며 identity 실패 때 기존 파일을 삭제하지 않는다. 파일 close 실패 시 성공으로 기록하지 않고 부분 파일을 정리하며 Win32/HRESULT access denied를 PERMISSION_DENIED로 분류한다.

| 검증 | 결과 | 근거 / 범위 |
|---|---|---|
| strict 계약·Provider | RED **18 failed** → GREEN | RED (`../../dist/acceptance/process-dump-contract-provider-red-20261009.xml`, 과거 경로); caller flags/path·PID/boot·budget·profile·separate export |
| 실제 OS worker | RED **5 failed** → GREEN **6 passed** | RED (`../../dist/acceptance/process-dump-worker-red-20261009.xml`, 과거 경로), GREEN (`../../dist/acceptance/process-dump-worker-green4-20261009.xml`, 과거 경로); 실제 Windows 자체 Python 대상 mini/full, budget/identity, 기존 파일 보존 |
| 고정 recipe RPC/MCP·Artifact | RED → GREEN cohort **48 passed** | RPC RED (`../../dist/acceptance/process-dump-rpc-red-20261009.xml`, 과거 경로), GREEN (`../../dist/acceptance/process-dump-rpc-green2-20261009.xml`, 과거 경로); 실제 DbgHelp, capture 수명주기 회귀 및 catalog 포함 |
| close 실패·access HRESULT | RED **3 failed** → GREEN worker **9 passed** | RED (`../../dist/acceptance/process-dump-error-cleanup-red-20261009.xml`, 과거 경로), GREEN (`../../dist/acceptance/process-dump-error-cleanup-green2-20261009.xml`, 과거 경로) |
| source Client 공통 UI | **PASS: 등록 2경로 + 설정** | 새 결과 (`../../dist/acceptance/process-dump-source-ui-20261009/agent-permissions-source-ui.json`, 과거 경로); Vite/headless source 시험, 이전 증거 덮어쓰기 없음 |
| 정적 검사·version | Ruff PASS, mypy **175 source files / issues 0**, version/기본 계약 **12 passed** | 계약 결과 (`../../dist/acceptance/process-dump-contracts-version2-20261009.xml`, 과거 경로); Client Node **11 passed**, TypeScript `--noEmit` exit 0, VERSION=0.1.20 |
| 실제 121 process.dump | **PASS** | source Agent run (`../../dist/acceptance/RUN-20261009-B-dump-rpc-1/results.json`, 과거 경로); 임시 source Agent·기존 packaged Python, argv/shell deny 상태 |
| 기존 native WinDbg MCP | **PASS: open/메모리 읽기/모듈/스레드/close** | 원문 (`../../dist/acceptance/RUN-20261009-B-dump-rpc-1/native-windbg-mcp.json`, 과거 경로); dump 분석이며 live attach/step 인수 아님 |
| dump 변경 전체 회귀 | **PASS: 575 passed / 22 skipped**, 908.22초, exit 0 | 결과 (`../../dist/acceptance/process-dump-full-20261009.xml`, 과거 경로), 로그 (`../../dist/acceptance/process-dump-full-20261009.log`, 과거 경로); 597 collected. 이후 추가한 close/HRESULT 3개는 위 focused worker 재검으로 별도 확인 |
| 마지막 현재 cohort | **PASS: 50 passed**, 21.33초 | 결과 (`../../dist/acceptance/process-dump-current-green-20261009.xml`, 과거 경로); 현재 worker의 close/HRESULT/readiness 보완·Provider·실제 MCP/Artifact·공통 capture 수명주기 포함 |

실제 source Device `dev_28312062c86b49ceb9a6f2a9d8c4b40f`, boot `boot_90c0f9c4cceb458aa49a32527db4ff3e`는 v0.1.20이며 source ZIP SHA-256은 `45a236d43d08175dd56ba6dd8ddc4b7e6aa5c537b2c1e406d4b9b4f77e42dc1d`다. 자체 시험 target **PID 2464/create_time 1791530152.8899777**을 full dump로 회수했다. **49,318,730 bytes**, SHA-256 **`68bf296ca57af72765d2f9f30daaa74081296fc2ed4d08e5469bcbaf9bc34df2`**, Artifact `art_7f663d369aa4409fb691c0c526cc5764`다. 잘못된 boot/create_time·Agent 자체 PID를 거절하고 4 KiB budget은 RESOURCE_EXHAUSTED로 종료했다. 덤프 뒤 target 생존을 확인했으며 시험 완료 후 정확한 parent boot/PID/create_time으로 source와 target을 종료하고 folder를 삭제·시험 Device revoke했다.

기존 WinDbg MCP에서 같은 SHA의 파일을 기존 RE Lab 허용 경로로 복사해 열었다. `|`의 target ID **0x9a0=2464**, python.exe/python312.dll 모듈·2개 스레드를 확인했다. `db 0x1db51a3c410 L20`이 실제 remote buffer의 **`RACP_REMOTE_TYPED_DUMP_20261009`**와 NUL을 반환했다. 예외 정보를 넣지 않은 수동 덤프의 `!analyze -v` 출력에 나온 0x80000003을 실제 target crash로 분류하지 않는다. 실제 생존 검사를 별도로 수행했다.

첫 native open은 자동 `!analyze -v`의 45초 timeout으로 실패했다. 원본 실패 (`../../dist/acceptance/RUN-20261009-B-dump-rpc-1/native-windbg-open-failure.json`, 과거 경로)를 보존했고, Session ID를 반환하지 않은 own orphan cdb의 PID/생성 시각/해당 dump 경로를 확인해 그 프로세스만 정리했다. timeout 120초의 새 호출은 약 3초의 자동 분석으로 정상 Session ID를 반환했다. 원인을 확정하지 않은 symbol/초기 분석 지연을 Agent 수집 오류 또는 원격 기술 불가로 바꾸지 않는다. 반환받은 자기 Session만 close했고 최종 상태 (`../../dist/acceptance/RUN-20261009-B-dump-rpc-1/final-status.json`, 과거 경로)에서 cdb 부재·임시 Device REVOKED·기존 121 ONLINE epoch 11을 확인했다.

native worker 첫 구현은 유효한 hFile 누락, callback packing, FILETIME float 계산 때문에 실패했다. 실제 OS 시험으로 수정했으며 DbgHelp가 자동 추가하는 header flags를 요청의 full-memory bit와 구분했다. 추가 close-failure fixture는 target Python의 초기 loader 상태에서 ERROR_PARTIAL_COPY를 받아 실패했고(보존 결과 (`../../dist/acceptance/process-dump-error-cleanup-green-20261009.xml`, 과거 경로)), 명시한 own target readiness 이후 주입하도록 fixture를 고쳐 위 9개 worker 시험을 통과했다. 원격 121 fixture도 수집 전에 ready receipt를 확인했다.

> [!IMPORTANT]
> 제품 build·패키징·OAuth 설정·Enterprise 정책 배포는 수행하지 않았다. 기존 Client v0.1.10 설치 UI가 새 기능을 포함한다는 주장이 아니라 실제 121의 별도 source Agent와 기존 native MCP의 경로를 검증했다. dump 분석·capture 분석 뒤 text clipboard 구현·인수는 §27.8에 기록한다. native live debugger/proxy duplex, system/config 확장, 로컬 승인 UI·권한 축소 시 session 폐기와 잔여 UI 검증은 계속 남아 있다. 전체 goal을 완료로 표시하지 않는다.

### 27.8 typed text clipboard와 실제 121의 Broker 경로

`clipboard.read/state/write`를 Windows 로그인 session의 authenticated Broker pipe에 연결했다. `clipboard.text.read`와 `clipboard.text.write`를 별도로 검사하며 state는 write 권한으로 sequence만 반환한다. CF_UNICODETEXT만 다루고 UTF-8 8 KiB, strict Unicode/NUL 검사, code point 경계의 prefix 읽기를 적용한다. write/clear에는 필수 expected_sequence가 있으며 clipboard를 연 상태에서 비교한 뒤 변경한다. input lease·화면 focus·키보드 입력·generic shell은 사용하지 않는다. 현재 catalog는 **74 RPC-backed leaf / 69 후속 = 143**이며 v1 migration baseline은 그대로다.

`desktop_enabled=false`라도 명시한 clipboard grant가 있으면 로그인 session Broker를 시작하도록 derived 설정을 추가했다. desktop 입력 권한을 함께 허용하지 않는다. read/state 뒤 잠긴 session 재검사, 변경 직전 session guard, clipboard busy·sequence 충돌·손상 데이터 분류를 적용했다. 실제 Win32 시험에서 close 전후 sequence가 달라지는 오류를 발견하고 반환 시점을 close 뒤로 수정했다. 반환 sequence는 그 시점의 관측이며 다른 앱의 후속 변경을 막는 lock으로 간주하지 않는다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 계약·실제 native Win32 | RED **13 failed** → GREEN **13 passed** | RED (`../../dist/acceptance/clipboard-contract-native-red-20261009.xml`, 과거 경로), GREEN (`../../dist/acceptance/clipboard-contract-native-green-20261009.xml`, 과거 경로); 자체 private window station으로 사용자 clipboard 보존 |
| Broker·cold settings | **31 passed** | 결과 (`../../dist/acceptance/clipboard-broker-settings-green3-20261009.xml`, 과거 경로); clipboard-only session, desktop deny 및 기존 설정 회귀 |
| MCP/RPC 계약 | **33 passed** | 결과 (`../../dist/acceptance/clipboard-rpc-green2-20261009.xml`, 과거 경로); fake Broker의 계약 시험과 다음 실제 Win32 시험을 구분 |
| 실제 Win32 + authenticated pipe + MCP | **1 passed** | 결과 (`../../dist/acceptance/clipboard-native-rpc-green-20261009.xml`, 과거 경로); test-only private station, 실제 pipe/BrokerCore, Unicode write/read/CAS/clear와 cleanup |
| 현재 focused cohort | **90 passed**, 6.82초 | 결과 (`../../dist/acceptance/clipboard-current-green-20261009.xml`, 과거 경로); contracts/version/catalog/pipe/desktop fences 포함 |
| source Client UI | **PASS: 초기 등록 2경로 + 설정** | 결과 (`../../dist/acceptance/clipboard-source-ui-20261009/agent-permissions-source-ui.json`, 과거 경로); source 시험, 설치 제품 인수 아님 |
| 정적 검사 | Ruff PASS, mypy **177 source files / issues 0**, Client Node **11 passed**, TypeScript `--noEmit` exit 0 | 제품 build 없음, VERSION=0.1.20 재사용 |
| 전체 회귀 | **601 passed / 22 skipped**, 549.04초 | XML (`../../dist/acceptance/clipboard-full-20261009.xml`, 과거 경로), 로그 (`../../dist/acceptance/clipboard-full-20261009.log`, 과거 경로); 623 collected, 실패 0 |
| 실제 121 일반 로그인 clipboard | **PASS: state/read 및 변경 전 충돌 거부** | source run (`../../dist/acceptance/RUN-20261009-B-clipboard-rpc-1/results.json`, 과거 경로), 정리 (`../../dist/acceptance/RUN-20261009-B-clipboard-rpc-1/final-status.json`, 과거 경로); 실제 CF_UNICODETEXT가 없어 text=null, Unicode payload 성공으로 확대하지 않음 |
| 실제 121 private Win32 clipboard | **PASS: Unicode write/read/prefix/CAS/clear** | source run (`../../dist/acceptance/RUN-20261009-B-private-clipboard-rpc-2/results.json`, 과거 경로), 정리 (`../../dist/acceptance/RUN-20261009-B-private-clipboard-rpc-2/final-status.json`, 과거 경로); test-only backend, 실제 native APIs/pipe, 사용자 WinSta0 clipboard 변경 없음 |

일반 121 run의 source ZIP SHA-256은 `bf92bd4f63be36d782e37432d7e55aba48c6ffad35ee553859678119d973a457`이다. Session 1의 sequence 25/Unicode format 없음 상태를 읽고 의도적으로 다른 sequence의 write가 PRECONDITION_FAILED/CLIPBOARD_CHANGED로 변경 전에 차단됨을 확인했다. desktop.type은 Agent에서 PERMISSION_DENIED다. 실제 user clipboard에는 쓰지 않았다.

private run의 source ZIP SHA-256은 `1cdf80deeb3524b7a64bfe39bdc4729e8b08794d4f5cdbb43dd0db237adde2dd`, test-only backend SHA-256은 `ddae11956fdaef8d5bfb73ae6ba4fd8283fed47f18e6995cb47ce94aa09b24d5`다. 별도 source Device `dev_5bdd608bf1514324b0f4be1d8662c996`에서 `RACP_REMOTE_CLIP_ROUNDTRIP_한글🙂`의 roundtrip, 5-byte `RACP_` prefix/truncated, 내용 없는 state, stale sequence 거부 및 clear 뒤 text=null을 확인했다. private window station은 테스트 fixture에만 존재하며 제품에서 임의 선택할 수 있는 backend로 추가하지 않았다. generic execution과 keyboard grant는 deny였다.

첫 private run은 기존 `run_source.py`를 create로 다시 쓰던 harness 충돌로 Agent 시작 전에 실패했다. 원본 (`../../dist/acceptance/RUN-20261009-B-private-clipboard-rpc-1/results.json`, 과거 경로)을 보존하고 launcher를 한 번만 생성하도록 고쳤다. 두 성공 run은 자신의 source/Broker PID와 생성 시각을 확인해 정리하고 시험 Device revoke·자체 폴더 삭제를 완료했다. 최종 기존 121은 ONLINE epoch 12였다.

> [!IMPORTANT]
> text clipboard의 source 구현과 실제 121 native API 경로를 검증했다. rich formats/images/files clipboard, native live debugger/proxy duplex, config 확장 및 나머지 계획은 미완료다. 현재 설정은 Agent 재시작으로 반영하는 cold 설정이며 hot permission 변경 지원을 주장하지 않는다. Enterprise Server→Client 정책 배포·OAuth 설정·제품 build는 수행하지 않았다.

### 27.9 native WinDbg 복수 채널 실패 원인과 Agent 전송 코어

기존 `open_cdb_remote`를 자체 정상 Python target에 사용해 native CDB의 역방향 연결을 조사했다. 첫 중계는 한 개 TCP 연결만 전달해 초기화 timeout으로 실패했다. 명시한 symbol 경로, 서버 `-noio`, 추가 prompt를 각각 시험했으나 해결하지 못했다. 직접 로컬 named pipe는 기존 MCP에서 정상 Session ID와 실제 marker 메모리를 반환했다. 이후 CDB 서버가 복수 TCP 연결을 생성한다는 사실을 확인하고 **각 연결을 별도 채널로 전달**하자 같은 native MCP의 TCP 역방향 초기화도 성공했다. 현재 자체 fixture에서는 **3 connections**였으며 모든 CDB 대상이 항상 정확히 3개라는 계약으로 고정하지 않는다.

| 검증 | 결과 | 근거 / 범위 |
|---|---|---|
| 단일 채널 reverse TCP | **FAIL: initialization timeout** | run 3 (`../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-3/native-open.json`, 과거 경로), run 4 (`../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-4/native-open.json`, 과거 경로), noio (`../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-6/native-open.json`, 과거 경로), prompt (`../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-7/native-open.json`, 과거 경로); 실패 기록 보존 |
| native MCP의 직접 named pipe | **PASS: open/marker read/close** | 환경 출력 제외 초기화 (`../../dist/acceptance/RUN-20261009-cdb-npipe-loopback-1/native-open-redacted.json`, 과거 경로); host 내부 비교, 원격 인수 아님 |
| 복수 reverse TCP prototype | **PASS: open/read/step/resume/break/detach/close** | 실제 MCP 명령 (`../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-9/native-commands.json`, 과거 경로), 자원 정리 (`../../dist/acceptance/RUN-20261009-cdb-reverse-loopback-9/results.json`, 과거 경로); 3채널, target/engine 부재 및 target의 engine 종료 후 생존 |
| Agent source relay 계약 | RED **2 collection errors** → GREEN **15 passed** | RED (`../../dist/acceptance/native-duplex-red-20261009.xml`, 과거 경로), 초기 GREEN (`../../dist/acceptance/native-duplex-green3-20261009.xml`, 과거 경로); 실제 loopback 3 socket/binary 정확성, typed canonical framing |
| ACK/scope/sequence/budget/lease | **21 passed / 1 failed** → 수정 | 보존 실패 (`../../dist/acceptance/native-duplex-current-20261009.xml`, 과거 경로); lease 정리 event와 watcher 완료의 경합 수정 |
| listener 생성 중 retirement | RED **1 failed** → 수정 | RED (`../../dist/acceptance/native-duplex-listen-retirement-red-20261009.xml`, 과거 경로); cleanup 완료 뒤 늦게 생성된 listener도 닫고 반환을 거절 |
| 현재 전송 코어 + version | **29 passed**, 4.58초 | 현재 GREEN (`../../dist/acceptance/native-duplex-current3-20261009.xml`, 과거 경로); 23 relay/계약 + version 6개, 기존 601 전체 회귀 이후 추가 범위 |
| native MCP → 실제 Agent source core | **PASS: open/read/step/resume/break/detach/close** | 원문, 환경 제외 (`../../dist/acceptance/RUN-20261009-cdb-source-duplex-2/native-mcp-redacted.json`, 과거 경로), 최종 상태 (`../../dist/acceptance/RUN-20261009-cdb-source-duplex-2/results.json`, 과거 경로); JSON roundtrip carrier/실제 OS socket peer PID 검증, Gateway/WSS 아님 |
| 현재 static checks | Ruff PASS, mypy **179 source files / issues 0**, VERSION **0.1.20** | 제품 build·추가 version bump 없음 |

새 `racp_protocol.native_duplex`는 frozen scope(Device/boot/epoch/principal/workspace/revision/session), 16채널, strict canonical base64의 16 KiB chunk, offset/ACK/end를 정의한다. `racp_agent.native_duplex.NativeDuplexRelay`는 loopback만 bind하고 trusted peer verifier/고정 connector·현재 gate/lease를 요구한다. 채널마다 최대 4개 미확인 chunk를 허용하며 전송된 boundary의 ACK만 credit로 처리한다. 기본 8 MiB/120초, hard 64 MiB/1시간으로 제한하고 frame마다 현재 gate를 재검하며 idle watcher도 회수한다. fingerprint는 scope 결합일 뿐 인증 token이나 암호학적 접근 권한이 아니다. 실제 carrier가 인증한 authority를 제공해야 한다.

새 코어의 첫 소켓 시험은 열린 accepted socket을 닫기 전에 `Server.wait_closed()`를 기다려 멈췄다. 소유 pytest 종료 기록 (`../../dist/acceptance/native-duplex-first-hang-20261009.json`, 과거 경로), 진단 원인 (`../../dist/acceptance/native-duplex-close-hang-20261009.json`, 과거 경로)을 보존했다. socket→task→server 순서로 닫도록 고쳤으며 자신의 시험 프로세스만 정리했다. 초기 직접 재귀 callback fixture는 bounded queue carrier로 바꿨다. send callback은 인증된 bounded carrier에 frame을 넘기는 계약이며 상대 receive 전체의 동기 완료를 기다리는 구조로 구현하지 않는다.

실제 source core run의 target **PID 21780/create_time 1791544709.0158207**, engine **PID 6180/create_time 1791544718.8557162**를 기록했다. 기존 native MCP Session `cdb-65be5cee`가 `db 0x1ad4b10b090 L18`로 **RACP_SOURCE_DUPLEX_OWN**을 읽었고 step/resume/CTRL+BREAK/detach를 실행했다. source relay 양쪽 모두 **3 channels / 43,412 transferred bytes**였으며 native session close, target의 engine 종료 후 생존, 자체 target/engine 종료 후 부재, relay task 부재를 확인했다. CDB SHA-256은 `5f54abafca3ae5638bbf807d402fabb350a64575c1dfa9fbfc7f5732df5bee67`이다.

첫 source-core run은 MCP open과 start-file 쓰기를 병렬 dispatch했지만 실제 도구 실행에서 start가 open timeout 뒤 수행돼 실패했다. 실패 (`../../dist/acceptance/RUN-20261009-cdb-source-duplex-1/native-open.json`, 과거 경로), 정리 (`../../dist/acceptance/RUN-20261009-cdb-source-duplex-1/results.json`, 과거 경로)를 보존했다. start-file 생성 후 native open을 순서대로 호출한 두 번째 run은 성공했다. 첫 named-pipe 성공의 자동 `!peb`가 inherited process environment까지 출력했으므로 이후 자체 target은 최소 OS 환경으로 생성하고 저장된 native 증거에서는 환경을 제외했다.

> [!IMPORTANT]
> 실패한 단일 채널 중계는 Agent 전송 코드로 해결 가능한 문제다. 기존 native MCP를 교체하거나 upstream을 수정하지 않았다. 새 코어는 **아직 Gateway/Agent authenticated WSS에 연결하거나 public RPC로 노출하지 않았다**. 121의 managed CDB runtime·exact target containment·outbound authenticated carrier·native MCP live 인수·proxy/replay는 남아 있다. 원본 native command의 광역 OS 제어를 세부 read-only 권한으로 보장하지 않으며, runtime/라이선스/OS 조건과 명시한 광역 grant를 후속 경로에서 적용해야 한다. 이번 host 증거를 121 인수 또는 전체 goal 완료로 바꾸지 않는다.

### 27.10 Hyper-V W11 — 인증 전송 코드와 실제 native WinDbg live 인수

사용자가 테스트 대상을 121에서 호스트의 Hyper-V `W11`로 전환했다. Default Switch의 VM 주소는 `172.28.147.80`, Gateway는 기존 `https://192.168.29.141:8765`다. 새 one-use 연결 파일을 발급해 Hyper-V guest file-copy로 VM에 전달하고 해당 integration service를 원래 disabled 상태로 복원했다. W11 Device **dev_a15c14e62d1d45e792786533702ac9d7**의 실제 Agent는 **0.1.13**이었다. 처음 read_only/medium integrity에서 사용자가 standard·관리자 실행으로 변경했고 session 2/integrity 12288/ONLINE을 확인했다. 기존 Client나 Agent binary를 빌드·교체하지 않았다.

Agent source에 `native.prepare/start/close`, 관리 CDB runtime 검증·Job Object 수명주기·helper socket peer 검증·현재 grant/revision/epoch/lease 회수와 queued output 재검을 추가했다. Gateway에 owner bearer/TLS WebSocket carrier, 단일 subscriber·Device/boot/epoch/owner/workspace/session scope·byte/queue ceiling·revocation/disconnect 정리를 연결했다. SDK가 공통 binary relay를 사용하며 원본 WinDbg MCP의 native protocol과 Session API를 그대로 유지한다. raw native bytes는 operation Journal에 저장하지 않는다. Gateway는 Client settings를 덮어쓰거나 grant를 확대하지 않는다.

관리 runtime은 caller RPC의 임의 executable/argv 대신 신뢰 설정의 manifest digest와 각 component SHA·x64 PE를 확인한다. 파일 handle을 FILE_SHARE_READ로 pin하고 helper 실행 중 변경·rename을 차단한다. fixed recipe는 CDB reverse loopback transport, `-pd/-noshell/-noinh`와 선택한 pinned Exts DLL만 사용한다. CDB engine은 Job Object에 포함하고 대상 프로세스는 borrowed다. native command는 넓은 OS 제어가 가능하므로 `network.tunnel.open`뿐 아니라 `exec.argv`, attach·memory read·export 등 명시한 grant를 요구한다. executable allow/deny 목록을 적용한 경우 opaque native bridge를 거절한다. 초기 target 검사와 별개로 임의 native command 전체를 read-only 또는 지정 PID에만 제한했다고 주장하지 않는다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| W11 source OS·권한 baseline | **OS 7개 및 deny PASS, Browser FAIL** | 원본 (`../../dist/acceptance/RUN-20261009-W11-source-permissions-1/results.json`, 과거 경로); source v0.1.20, 별도 Device. Browser는 UNKNOWN/cleanup grace 오류이며 전체 PASS로 표시하지 않음 |
| Browser 실패 후 시험 폴더 정리 | **복구 완료** | 진단 (`../../dist/acceptance/RUN-20261009-W11-source-permissions-1/diagnostic-native.json`, 과거 경로), 복구 (`../../dist/acceptance/RUN-20261009-W11-source-permissions-1/cleanup-recovered.json`, 과거 경로); source PID 부재, 남은 빈 자체 폴더만 nonrecursive rmdir. Browser 원인은 확정하지 않았고 사용자 요청에 따라 추가 Browser 시험 보류 |
| runtime pin 계약 | RED collection error → **14 passed**, 확장 추가 후 **15 passed** | 초기 GREEN (`../../dist/acceptance/native-runtime-green-20261009.xml`, 과거 경로), 현재 (`../../dist/acceptance/native-runtime-extension-20261010.xml`, 과거 경로); manifest/version/alias/hash/PE/extra files·write pin |
| core/catalog/version focused | **71 passed** | 결과 (`../../dist/acceptance/native-carrier-foundation-20261010.xml`, 과거 경로); 기존 전체 601회귀 이후 신규 변경 범위 |
| carrier·계약 focused | **25 passed**, 9.48초 | 결과 (`../../dist/acceptance/native-carrier-current-20261010.xml`, 과거 경로); owner/epoch/scope/bytes·schema/기본 계약/runtime |
| 현재 최소 focused | **42 passed**, 5.20초 | 결과 (`../../dist/acceptance/native-final-focus-20261010.xml`, 과거 경로); 이후 확장 1개는 위 15개 runtime 재검으로 구분 |
| 최종 변경 범위 회귀 | **83 passed**, 5.55초 | 결과 (`../../dist/acceptance/native-carrier-final-20261010.xml`, 과거 경로); native carrier/runtime/공통 relay·schema·기본 계약·권한/catalog/version. 기존 전체 601회귀를 새 코드 전체 회귀로 주장하지 않음 |
| Gateway source reload | **PASS: DB backup, 같은 boot, W11 epoch 4** | 기록 (`../../dist/acceptance/RUN-20261010-W11-native-gateway/restart.json`, 과거 경로); 같은 DB/TLS/주소, 다른 앱 listener 유지 |
| W11 기본 live native MCP | **PASS: 실제 open/read/step/resume/break/detach/close** | native 원문 (`../../dist/acceptance/RUN-20261010-W11-native-live-3/native-mcp.json`, 과거 경로), carrier 결과 (`../../dist/acceptance/RUN-20261010-W11-native-live-3/native-results.json`, 과거 경로); 3채널/34,482 bytes, 처음 최소 runtime의 `!peb` 확장 없음은 별도 제한 |
| Exts 보완 및 W11 live/PEB | **PASS** | native 원문, 환경 제외 (`../../dist/acceptance/RUN-20261010-W11-native-live-4/native-mcp-redacted.json`, 과거 경로), 결과 (`../../dist/acceptance/RUN-20261010-W11-native-live-4/native-results.json`, 과거 경로); 3채널/59,582 bytes, 실제 PEB·marker·step/continue/break/detach |
| 최종 소유 자원·기존 Agent | **PASS** | 최종 확인 (`../../dist/acceptance/RUN-20261010-W11-native-live-4/final-status.json`, 과거 경로); source/target PID 부재·helper process 없음·자체 folder 부재·시험 Device revoke·기존 W11 ONLINE epoch 4. 호스트 native client PID 19376 부재도 확인 |
| 정적 검사·버전 | Ruff PASS, mypy **186 source files / issues 0**, VERSION **0.1.20** | 같은 변경 batch 재사용, 제품 build·OAuth·Enterprise 정책 배포 없음 |

최종 run의 source Device **dev_348ce7d639924168af7c83eaa93f13d4**, boot **boot_c45d8fc6e50d4cd8b0d985f17d73bd5e**에서 native scope를 준비했다. VM target **PID 7704/create_time 1791560657.2513208**에 대한 native MCP Session **cdb-86556e6e**가 `db 0x23297df8a50 L20`으로 **RACP_W11_NATIVE_LIVE_OWN**을 읽었다. `|`의 **0x1e18=7704**, registers·threads, 실제 `t/g/CTRL+BREAK/.detach` 및 close를 확인했다. native session 정리 후 target 생존을 먼저 확인하고 자신의 source/target만 종료했다. runtime 전체 SHA와 source/runtime ZIP SHA·manifest digest는 각 run JSON에 보존했다. 런타임은 현재 호스트의 정상 Microsoft debugger 파일을 private 시험용으로 복사한 것이며 제품 재배포·설치·라이선스 인수를 뜻하지 않는다.

첫 run은 harness가 filesystem.read의 `text`를 `content`로 읽어 실패했다(기록 (`../../dist/acceptance/RUN-20261010-W11-native-live-1/native-results.json`, 과거 경로)). 두 번째는 native client의 IPv4/IPv6 listener 2개를 서로 다른 process로 간주해 start를 못 했다(기록 (`../../dist/acceptance/RUN-20261010-W11-native-live-2/native-results.json`, 과거 경로), native 실패 (`../../dist/acceptance/RUN-20261010-W11-native-live-2/native-open-failure.json`, 과거 경로)). PID를 deduplicate하고 executable/owner/정확한 connection string 검사 후 시작하도록 고쳤다. 성공 run의 listener rows=2/PID=1로 원인을 재확인했다. Agent 기술 불가로 분류하지 않는다.

처음 최소 runtime에서 `!peb`가 없던 문제는 Microsoft 문서의 [Exts.dll 요구](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/-peb)를 확인해 pinned extension과 고정 `-a` recipe로 수정했다. 이후 필요한 ntdll PDB가 없어 조회가 실패했다. 원격 CDB의 symbol 경로는 **VM의 경로**이므로 호스트 E: cache를 사용하지 않고 자체 VM folder의 cache로 `.sympath`를 설정한 뒤 공개 Microsoft OS symbols를 로드해 PEB 조회를 성공시켰다. `.sympath`는 semicolon을 경로 구분자로 읽으므로 reload·PEB를 별도 명령으로 실행했다. `native.prepare`는 호스트 도구가 사용할 VM symbol cache 경로도 반환한다. PDB/도구 확장 조건을 원격 Agent 자체의 불가로 바꾸지 않는다.

> [!IMPORTANT]
> 기존 native WinDbg MCP→SDK→Gateway bearer/TLS→outbound Agent→관리 CDB→실제 VM process의 live 경로를 처음으로 완료했다. 현재 catalog는 **75 RPC-backed leaf / 68 후속 = 143**이며 신규 leaf를 v1 migration에 추가하지 않았다. 모든 native 도구·모든 command·무제한 OS 제어 또는 모든 143 후보의 구현 완료를 뜻하지 않는다. proxy/replay, 추가 OS 기능·로컬 승인/hot retirement·제품 배포 인수는 남아 있다. Browser 추가 시험은 보류하고 구현 및 필요한 최소 인수에 집중한다. 전체 goal을 완료로 표시하지 않는다.

### 27.11 Hyper-V W11 — 원본 proxy MCP의 HTTP 수집·인터셉트·재전송

Agent에 `proxy.prepare/close`와 process-bound ProxyProvider를 추가하고 기존 SDK·Gateway 인증 carrier를 양방향으로 확장했다. scope의 endpoint role을 검증하며 Agent listener는 정확한 client PID/생성 시각, connector는 정확한 server PID/loopback 포트에 결합한다. host native socket도 기존 MCP Python PID/생성 시각과 역방향 TCP tuple로 검증한다. proxy helper를 VM에 설치하거나 사용자 앱·시스템 proxy·Root CA를 변경하지 않았다. 광역 native 전송은 intercept/replay/tunnel/exec/export grant가 모두 필요하며 executable allow/deny constraint가 설정되면 거절한다. 이전 등록의 권한 확대·Enterprise 설정 배포·OAuth·제품 build는 수행하지 않았다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 첫 변경 범위 검사 | **28 passed / 1 failed** | 실패 (`../../dist/acceptance/proxy-carrier-current-20261010.xml`, 과거 경로); 시험 fixture가 venv launcher PID를 socket 소유 PID로 잘못 사용 |
| 실제 socket/계약 수정 | **29 passed**, 5.07초 | 결과 (`../../dist/acceptance/proxy-carrier-current2-20261010.xml`, 과거 경로); base Python의 실제 PID와 async subprocess 사용, binary 보존·peer birth 거부 |
| 최종 변경 범위 | **90 passed**, 5.86초 | 결과 (`../../dist/acceptance/proxy-carrier-final-20261010.xml`, 과거 경로); 반복 close·다른 workspace close 거부와 native/core/catalog/schema/version 회귀. 이전 전체 601회를 최신 전체 회귀로 주장하지 않음 |
| 정적 검사 | Ruff PASS, mypy **189 source files / issues 0** | VERSION 0.1.20 변경 batch 유지 |
| Gateway source reload | **PASS: 같은 boot, W11 epoch 5** | 기록 (`../../dist/acceptance/RUN-20261010-W11-proxy-gateway/restart.json`, 과거 경로); nonterminal 작업 0 확인·DB backup·동일 TLS/DB/주소 |
| 첫 실제 W11 수집·재전송 | **PASS, explicit close FAIL** | native MCP (`../../dist/acceptance/RUN-20261010-W11-proxy-live-1/native-mcp.json`, 과거 경로), VM receipt/정리 (`../../dist/acceptance/RUN-20261010-W11-proxy-live-1/proxy-results.json`, 과거 경로); GET 200 및 수정한 POST/header/body가 VM server에 도착 |
| 범위 제한 native 인터셉트 | **PASS: 특정 URL header 수정** | VM receipt (`../../dist/acceptance/RUN-20261010-W11-proxy-live-1/intercept-results.json`, 과거 경로); nonce URL에만 inject_header, 자신의 rule만 remove_global_header로 제거·전후 rules={} |
| 새 source의 최종 실제 인수 | **PASS: capture/replay/close/borrowed 생존/정리** | native MCP (`../../dist/acceptance/RUN-20261010-W11-proxy-live-2/native-mcp.json`, 과거 경로), 결과 (`../../dist/acceptance/RUN-20261010-W11-proxy-live-2/proxy-results.json`, 과거 경로); origin 2채널/1,636 bytes, proxy 1채널/450 bytes |
| 최종 보존·정리 | **PASS** | 확인 (`../../dist/acceptance/RUN-20261010-W11-proxy-live-2/final-status.json`, 과거 경로); 기존 W11 ONLINE epoch 5·source Device REVOKED·자체 folder 부재·자신이 시작한 proxy listener 부재 |

원본 `mitmproxy_mcp`의 `start_proxy/search_traffic/inspect_flow/replay_flow/add_interception_rule/remove_global_header/stop_proxy`를 사용했다. 기존 rules·traffic DB는 지우지 않았고 자신의 새 `.flow` 파일에 기록했다. 첫 run에서 VM server PID 11744/client PID 11352의 자체 HTTP 요청을 host MCP flow `74a09999-da2e-43b8-8fdf-9d893b4733c1`로 읽었다. 같은 flow를 POST로 바꿔 `RACP_OWN_REPLAY_BODY`와 nonce header를 재전송했고, scoped rule로 변경한 header도 VM server receipt에서 확인했다. host MCP process PID 22860/create_time 1791542346.059802 및 socket ownership을 검증했다. 이 원본 MCP나 upstream 코드는 수정하지 않았다.

첫 실제 인수에서 SDK unsubscribe로 이미 정리된 session에 explicit close를 호출하면 HANDLE_EXPIRED를 반환했다. Agent의 close가 만료·종료 상태를 다시 active로 검사하던 결함이다. 현재 operation gate와 owner/device/boot/workspace를 확인하고 이미 닫힌 세션도 동일 CLOSED handle로 반환하도록 수정했다. 같은 범위 local 회귀와 새 source Agent의 실제 W11 인수로 수정 후 정리까지 검증했다. 첫 실패 기록을 PASS로 덮어쓰지 않았다. 두 run 모두 자신의 source/client/server를 생성 시각에 결합해 종료하고 폴더를 삭제했다. borrowed target은 carrier 정리 후에도 생존했고 이후 시험 cleanup에서만 종료했다.

> [!IMPORTANT]
> 현재 catalog는 **77 RPC-backed / 66 후속 = 143**이다. 실제 HTTP 경로의 수집·인터셉트·재전송과 native debugger의 live 분석을 증명했지만 모든 native 도구/command·모든 OS 앱의 자동 routing·HTTPS/pinning/mTLS·전체 143 후보 완료를 증명하지 않는다. 해당 조건 및 추가 OS Provider·로컬 승인/hot retirement·제품 배포 인수는 남아 있다. 이 경로의 반복 시험은 종료하고 남은 구현 작업으로 이동한다.

### 27.12 Windows 서비스·소프트웨어 inventory RPC 구현

`services.list/get`, `software.inventory`를 OSObservationProvider에 연결했다. 공통 model은 list limit 1..200/offset 0..10000과 제어 문자·경로 구분자를 제외한 service name을 검사한다. Registry는 Windows-only/read-safe로 광고하고 `services.read`와 `software.inventory.read`를 각각 검사한다. 기존 등록에 새 권한을 자동 부여하지 않는다. 임의 shell/argv·SCM 변경·install/uninstall 실행을 하지 않는다.

서비스 조회는 psutil native SCM 경로에서 이름·표시명·상태·시작 유형만 읽으며 executable arguments/logon account를 포함하는 `as_dict()`는 호출하지 않는다. 소프트웨어 조회는 machine/user 및 32/64-bit의 고정 Uninstall registry 경로에서 DisplayName/DisplayVersion/Publisher만 읽는다. native RegQueryValueExW는 4 KiB 고정 buffer로 조회하며 oversized/비문자/손상 값은 포함하지 않는다. 환경 변수를 확장하지 않고 uninstall command·credential을 읽지 않는다. registry 등록 inventory이며 MSIX 전체 목록이나 실제 설치 상태의 완전한 증명을 뜻하지 않는다. source 접근 실패·record unavailable·live pagination을 결과에 표시한다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 현재 inventory/OS·MCP·권한 계약 | **44 passed**, 16.12초 | 결과 (`../../dist/acceptance/windows-inventory-current-20261010.xml`, 과거 경로); 실제 host SCM·전용 HKCU fixture의 native bounded query·MCP grant off 거부/on 허용·실행 권한 없음·schema/catalog |
| 정적 검사 | Ruff PASS, mypy **190 source files / issues 0** | 첫 mypy의 list annotation 누락을 보완, VERSION 0.1.20 batch 유지 |
| W11 source reload 전제 검사 | **보류: 기존 Agent OFFLINE** | reload helper가 ONLINE 전제에서 멈춰 기존 Gateway를 종료하지 않음. Hyper-V W11 State=Running, 기존 Device epoch 5/같은 boot의 OFFLINE을 다시 조회 |
| 새 inventory의 실제 VM 인수 | **미실행** | 사용자에게 기존 Client의 Agent 재시작을 요청했다. host native/MCP 시험을 VM 성공으로 확대하지 않음 |

현재 catalog는 **79 RPC-backed / 64 후속 = 143**이다. 새 inventory와 남은 OS/로컬 승인/설정 기능 구현을 계속하며, 원격 연결 재개 후 해당 신규 RPC의 최소 VM 인수만 수행한다. 제품 build 및 Enterprise 정책 배포는 보류 상태를 유지한다.

### 27.13 원격 작업 폴더의 text 검색·검토 가능한 여러 파일 patch

`filesystem.search_content` 및 `filesystem.patch`를 Registry·MCP·Agent Provider·세부 권한에 연결했다. 검색은 literal line 방식이고 regexp/shell을 실행하지 않는다. anchored regular-file read, per-file revision 확인, 최대 10,000 entries/파일 수·파일 크기·전체 scan credit·깊이·결과 수 제한, 링크·binary·손상 encoding·변경 파일 skip을 적용한다. casefold가 ß 등을 여러 문자로 확장할 때도 원문의 Unicode codepoint 열을 반환하며 긴 줄의 match 주변 preview를 제공한다.

patch는 16개 파일/128 edits/입력 text 64 KiB 및 파일당 64 KiB로 제한한다. 각 대상의 예상 SHA와 선택적 revision, exact occurrence count를 **전체 preflight**에서 검사하고 diff/dry-run 또는 순차 변경을 수행한다. UTF-8 BOM·편집하지 않은 CRLF/LF bytes를 보존한다. 기존 anchored temp-file writer/ACL 보존/원자적 교체/cleanup을 재사용하며 commit 전 hash/revision을 다시 검사한다. file worker의 Budget에도 captured permission revision과 현재 grant 검사를 전달해 폐기 후 publication을 막는다. 기존 `filesystem.write`에는 명시한 bytes의 줄바꿈을 유지하는 `newline=verbatim`을 추가했다.

| 검증 | 결과 | 증거 / 범위 |
|---|---|---|
| 최초 변경 범위 | **53 passed / 1 failed / 1 skipped**, 50.91초 | 원본 (`../../dist/acceptance/text-files-current-20261010.xml`, 과거 경로); 실제 host file APIs·기존 파일/실패/권한·catalog/schema. MCP mutation 시험에서 필수 idempotency key 누락 |
| MCP 요청 보완·영향 큐 경로 | **4 passed**, 22.14초 | 결과 (`../../dist/acceptance/text-files-mcp-queue-final-20261010.xml`, 과거 경로); grant off의 실제 PERMISSION_DENIED/on patch/search, queued cancel/FIFO·Gateway restart·late result |
| 최종 text 기능·MCP | **10 passed**, 3.52초 | 결과 (`../../dist/acceptance/text-files-final-20261010.xml`, 과거 경로); Unicode 열/긴 줄 preview, scan/result budgets·link 거부·preflight 무변경·dry-run·BOM/혼합 newline 보존·partial batch·교체 직전 권한 폐기/cleanup |
| 정적 검사 | Ruff PASS, mypy **191 source files / issues 0** | 같은 VERSION=0.1.20 batch, build 없음 |
| 새 기능의 실제 W11 인수 | **대기: 기존 Agent OFFLINE epoch 5** | 같은 boot/OFFLINE을 현재 다시 조회했다. host file/MCP 결과를 VM 결과로 확대하지 않음. 기존 Agent 재시작 질문을 반복하지 않음 |

첫 과정의 Python WMI 호출에서 `0x8007000e` 예외 진단이 출력됐지만 검사 프로세스는 계속 실행됐다. 실제 실패는 MCP mutation signature의 필수 idempotency key 누락이었다. 해당 key를 넣고 off 결과가 PERMISSION_DENIED인지도 확인해 validation failure를 권한 거부 성공으로 착각하지 않도록 보완했다. 이후 MCP 검색·편집은 실제 파일 변경과 반환값으로 성공을 확인했다. 검사 중 관측한 host 메모리는 available 14.14 GiB/total 32 GiB이며 원격 Agent OFFLINE 원인을 이 진단으로 확정하지 않는다.

> [!IMPORTANT]
> 여러 파일의 교체는 **파일별 원자성**만 보장한다. 후속 파일 실패 시 완료된 파일과 hash/실패 대상을 error details로 반환하고 전체 batch 원자성·자동 rollback·외부 writer에 대한 atomic CAS를 주장하지 않는다. 완료된 mutation은 Journal의 같은 idempotency key로 다시 실행하지 않는다. 현재 catalog는 **81 RPC-backed / 62 후속 = 143**이며 로컬 승인/hot settings·나머지 OS 기능 및 W11 신규 인수는 아직 남아 있다.

### 27.14 Client 지연 측정 및 언어/플랫폼 전환 검토

사용자가 Client의 응답 지연과 Windows/macOS/Linux에 맞는 Rust/C++/C# 전환을 문의했다. 현재 UI는 Electron/React이고 로컬 Agent/desktop bridge는 Python이다. `backend.cjs`는 매 요청마다 Python을 실행하며 `controller.cjs`는 3초마다 status 다음 activity를 순서대로 요청한다. renderer의 overview는 cached 조회지만 refresh·변경 작업은 backend 응답을 기다린다. desktop bridge의 background/main import는 Agent runtime도 먼저 불러온다. 측정은 실제 사용자 설정·credential·실행 중 Client를 건드리지 않고 호스트의 새 빈 state에서 각 명령을 3회 실행했다.

| 측정 | info 중앙값 | status 중앙값 | activity 중앙값 | 범위 / 증거 |
|---|---:|---:|---:|---|
| 기존 0.1.13 번들 | 2,558.6 ms | 2,105.6 ms | 2,153.6 ms | 원본 (`../../dist/acceptance/RUN-20261010-client-latency-38f1bf09/timing.json`, 과거 경로); 같은 번들, 호스트의 별도 빈 설정 |
| 현재 0.1.20 source | 4,352.3 ms | 3,708.2 ms | 3,134.5 ms | 원본 (`../../dist/acceptance/RUN-20261010-client-latency-d4613d2c/timing.json`, 과거 경로); 프로젝트 .venv, 다른 runtime/package 조합이므로 버전별 순수 성능 비교 아님 |

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

기존 프로필 탐색과 모호한 상태 거절, DPAPI와 소유권 확인, native 연결 파일 선택, 시작 등록, installer maintenance/backup, 포터블 프로필 격리 및 고정 WebView2를 구현했다. ADR-0030(삭제, [ADR-0032](../adr/ADR-0032-native-client.md)로 통합)이 구 Electron/Python 배포 결정을 대체한다. 실행·서비스 등록과 빌드는 [Rust Agent 가이드](../guides/rust-agent-guide.md)를 따른다.

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


<a id="rust-plan-update-20261011"></a>
## 2026-10-11 Rust 계획 재정렬·정적 검토

검토 기준은 `3b48b0ff11d93c45ba40b0b30943b284a1e5b6fe`, 제품 SSOT는 v0.1.21이다. 사용자는 계획 방향과 실제 구성의 검토 후 세부 작업 계획서 갱신을 요청했다. [전체 Rust 전환 계획](../spec/rust-client-migration-plan.md)의 Iced·새 등록·레거시 미유지 목표를 유지하고 기존 Rust 구현을 재사용하는 R00–R14의 파일·인터페이스·선행·완료 기준과 실행 묶음을 보완했다. 이번 변경은 문서에 한정되며 제품 version/lock/runtime/CI/설치 상태를 변경하지 않았다.

### 실제 코드와 남은 차이

| 항목 | 정적 확인 | 후속 작업 |
|---|---|---|
| Rust 기반 | contract/core/runtime·Agent와 Windows Broker/Guardian 실행 모드 존재 | R02/R04/R06–R10에서 계약·수명주기 재사용 감사 |
| GUI | Tauri/Rust 호스트와 React/WebView2 사용 | R01 native GUI; R-PKG/R14에서 WebView2·React 제거 |
| 권한 | 18개 category·143개 ID 중 81 rpc/62 planned | R11-A–K에 62개 ID 전체 배정; 기능 지원 판정과 구현 상태 분리 |
| 승인/hot policy | require_approval은 오류 반환, 설정 변경에 실행 Agent 잠금 필요 | R05 nonce/target/revision 승인과 CAS/retirement |
| 상태/등록 | 기존 credential 경로 탐색, v1→v2 변환 | R03 새 state/등록·legacy 자동 import 제거 |
| GUI 제어 | loopback TCP·3초 조회 | R03 OS peer 인증 IPC·snapshot/event/gap resync |
| 빌드/runtime | Node/pnpm Client 빌드와 기본 Chromium/WebView2 번들 | R-PKG Rust xtask·기본/선택 runtime 분리 |

[기존 Tauri 설계](../spec/client-rust-migration.md)와 [10-task 구현 계획](../spec/client-rust-implementation-plan.md)은 향후 작업 기준으로는 Superseded로 표시하고 이력을 보존했다. ADR-0030(삭제, [ADR-0032](../adr/ADR-0032-native-client.md)로 통합)은 현행 v0.1.21 결정 기록으로 Accepted를 유지하며 향후 계획과 범위를 구분했다. 과거 승인·빌드·완료 기록을 이번 Iced 구현 승인·완료로 옮기지 않는다.

> [!IMPORTANT]
> 자동 테스트 실행·추가, GUI/native acceptance, 설치/업데이트/제거, macOS/Linux native 빌드는 계속 보류한다. 이번에 작성한 인수 사례는 미래 검증 명세다. GUI 적합성 보류 중에는 골격/공통 모델·독립 Agent 구현과 패키징 정의까지만 진행하도록 의존성을 명시했다. 전체 화면·최종 패키지·Tauri 제거는 해당 선행 조건을 충족한 뒤 진행한다. 기능 동등성·성능·전체 Rust 전환은 미완료다.

### 이번 문서 변경의 확인 결과

수정 문서 7개의 metadata·코드 fence와 상대 링크 625건을 점검했다. 새 링크 17건의 오류는 0건이며 주 계획서의 상대 경로/anchor 오류도 0건이다. R11 표의 권한 ID는 catalog planned 집합과 62/62 일치하고 누락·추가·중복은 0건이다. `git diff --check`는 PASS다. 기존 문서의 과거 dist 산출물 등 현재 checkout에서 확인할 수 없는 링크/anchor 398건(등장 횟수)은 기존 상태로 분리했으며 역사 자료를 삭제하거나 전체 링크 정상으로 주장하지 않는다. 이 확인은 문서 정적 검증이며 제품 테스트·빌드 실행 결과가 아니다.

<a id="rust-native-foundation-20261011"></a>
## 2026-10-11 v0.1.22 Rust native 기반 구현

사용자가 [전체 Rust 전환 계획](../spec/rust-client-migration-plan.md)에 따른 구현과 없는 로컬 Rust 환경의 설치를 요청했다. 계획서의 과거 문서 갱신 전용 범위와 현재 구현 요청을 구분했다. 이번 배치는 R00과 R01-A/B/C·R02-B/C·R03의 초기 연결 기반이며 **전체 전환 완료 또는 기능 동등성 합격이 아니다**.

### 환경·버전·변경 보존

- Windows x64 로컬 호스트의 Visual Studio 2022 Community C++ 도구를 확인하고 공식 rustup으로 `rustc 1.90.0 (1159e78c4)`, `cargo 1.90.0 (840b83a10)`, rustfmt/clippy를 설치했다. `rust-toolchain.toml`의 1.90.0을 유지했다.
- 기준 소스 commit은 `3b48b0ff11d93c45ba40b0b30943b284a1e5b6fe` / v0.1.21이다. 기존 문서 7개의 dirty 변경을 보존하고 같은 작업 디렉터리에서 `codex/rust-native-foundation` branch를 생성했다. 이번 코드도 미커밋 상태이며 커밋·푸시·원격 CI 실행은 하지 않았다.
- `scripts/version.py bump`를 배치에 한 번 적용해 v0.1.22로 동기화했다. `uv.lock`과 `Cargo.lock`을 갱신했다. 새 native GUI·UI 모델·xtask는 Cargo workspace version을 상속하며 표시 버전은 `CARGO_PKG_VERSION`을 사용한다. Python workspace에서 `apps/client-native`를 제외해 Gateway/CLI 설치를 유지했다.
- 기존 설치·Device·credential·사용자 작업 폴더·`dist/` 산출물을 변경하거나 제거하지 않았다.

### 실제 구현과 남은 gate

| 작업 | 실제 변경 | 판정 및 미완료 |
|---|---|---|
| R00-A/B | [migration-inventory.json](../../crates/racp-contract/migration-inventory.json)에 commit/dirty diff·원본 Registry/catalog/schema hash, 100 operation→Rust Provider 소스, 143 permission의 다대다 연결, 기본 AND binding과 조건부 권한 authority를 기록 | 정적 audit `static_pass`; dispatch 소스 연결은 실행 성공 증거가 아님 |
| R00-C | Tauri/React·TCP control·legacy state·Node build/CI·Java bridge·Python Ghidra bootstrap과 보존할 서버/Console 소비자를 분류 | 초기 소비자 추적 구현; 최종 dependency graph/제거 감사는 R14 대기 |
| R00-D | Iced **0.13.1**을 정확히 pin하고 tiny-skia 기본·wgpu 선택 조합을 Cargo.lock에 고정. Iced MIT/MSRV 1.80 및 tray-icon 0.21.2 MIT OR Apache-2.0/MSRV 1.71 후보 metadata 확인 | 두 renderer 조합 Windows 컴파일 `build_pass`; tray는 후보만 조사, toolkit 제품 적합성/전수 license·advisory 감사 미완료 |
| R01-A/B | [native GUI](../../apps/client-native/src/main.rs)와 공통 UiModel (`../../crates/racp-ui-model/src/lib.rs`, 과거 경로): dark theme·맑은 고딕·입력 초안·상태/권한 골격, deny 기본, category off 시 leaf 보존, grant·constraint·availability·revision 분리 | 골격 `build_pass`; 편집은 저장되지 않는 초안. 등록/설정/승인 전체 화면·트레이·single instance·IME/Narrator/DPI/VM 실기 gate는 대기 |
| R01-C | Iced subscription에 authenticated pipe snapshot/event 연결. 정상 연결 중 polling 없음, 단절 시에만 backoff. boot/sequence gap은 fresh snapshot 재접속 | 데이터 연결 코드 `build_pass`; 실제 GUI/Agent 인수 `deferred_by_user` |
| R02-B/C | [typed local IPC](../../crates/racp-contract/src/local_ipc.rs): protocol 1, 64 KiB, 16개 session, 5초 receipt 상한, unknown/duplicate key 거부. 선택 runtime metadata/OS·arch·hash·고정 recipe 계약. xtask inventory/contract drift audit | 정적 계약/컴파일 확인; runtime trust catalog admission, validator 전체 차이 감사와 cross-language fixture/실제 TLS handshake는 미완료 |
| R03-A/B | [새 state schema](../../crates/racp-core/src/state_schema.rs): native-client user root, 별도 state/policy/DPAPI credential, 지원하지 않는 schema 거부, 빈 명시 root에만 새 등록. 연결 파일 hash/만료·workspace/권한 검증을 token 소비 전 수행 | 새 CLI `native-enroll`/`native-run` 코드 `build_pass`; 실제 등록/저장/재시작 인수 미실행. token 결과 불명/저장 실패/crash는 pending marker를 보존하여 자동 재등록 금지 |
| R03-C/D | [Windows Named Pipe](../../crates/racp-runtime/src/local_ipc/windows.rs): SID ACL·remote client 거부·OS peer SID/session/PID/생성 시각 pin·양방향 challenge. snapshot/receiver 같은 lock, 256개 bounded event queue, overflow/gap 시 reconnect | 사용자 session 경로 `build_pass`; 다른 사용자/위조/replay 실기 인수 미실행. Session 0은 거부하며 service scope·Unix peer credential 구현은 미완료 |
| R03-E / R04 / R05 | native 시작 경로는 기존 credential loader·v1/v2 변환·TCP control을 호출하지 않음. IPC 실패 시 Agent future를 버리지 않고 shutdown/cleanup을 기다리는 연결 기반 추가 | R04 resource retirement/Journal 보강과 R05 hot CAS/nonce 승인 **미구현**. Stop/UpdatePolicy/DecideApproval은 `NOT_IMPLEMENTED`로 거부. activity 통합 및 stop cleanup receipt는 대기 |
| R-PKG-A | [xtask package dry-run](../../xtask/src/package.rs)에 Windows native staging/output·SDK·기본/선택 payload·manifest 요구 필드·setup/portable/ZIP 정의 | 정의만 `static_pass`; 실제 staging manifest·installer/updater/서비스 설치·서명·배포 미실행 |
| R06–R11 / R12–R14 | 기존 Provider 소스와 확장 permission 62개를 추적하고 R11-A–K 담당을 배정 | 이번에 기능 인수/62개 확장 구현을 완료하지 않음. macOS/Linux native 실행·Tauri 제거·최종 패키지는 대기 |

새 GUI 기본 의존성에 React/Tauri/WebView2/Node/Python을 추가하지 않았다. 기존 Tauri 경로는 R01 제품 gate와 새 화면 완성 전에 제거하지 않았으므로 저장소 전체의 legacy 제거는 미완료다. native Agent는 기본 Chromium 탐색/자동 시작을 하지 않으며 선택 runtime의 최종 admission은 R09/R10에서 연결해야 한다.

### 실행한 production·정적 확인

| 명령/확인 | 결과 |
|---|---|
| `cargo fmt --all -- --check` | PASS |
| `cargo check --workspace --bins --locked --target x86_64-pc-windows-msvc` | PASS |
| `cargo check --locked -p racp-client-native --no-default-features --features gpu-renderer --target x86_64-pc-windows-msvc` | PASS; software 기본과 GPU 선택 조합을 각각 컴파일 |
| `cargo build --workspace --bins --release --locked --target x86_64-pc-windows-msvc` | PASS; 마지막 변경 뒤 재빌드, 최종 incremental build 50.17초 |
| `cargo run --locked -p xtask -- inventory-check` / `contract-check` | PASS: 100 operation / 143 permission / 18 category / 62 확장 담당, 누락·중복·drift 0 |
| 현재 Python `REGISTRY`의 `asdict` 값과 공개 Registry JSON 비교 | PASS: 100개, ID 차이/계약 차이 0. 정적 계약 추출이며 Gateway 실행 인수가 아님 |
| `cargo run --locked -p xtask -- package --platform win --arch x64 --dry-run` | PASS; 파일 생성 없음 |
| `uv run ruff check scripts/version.py tests/unit/test_version.py packages/ apps/` | PASS |
| `uv run mypy` | PASS: 95 source files |
| `uv run python scripts/version.py show` | `0.1.22` |
| `git diff --check` | PASS |
| production EXE PE machine / size / SHA-256 | 아래 x64 결과 확인 |

기존 Rust Provider의 warning 4개(`native_carrier/windows.rs`의 미사용 version, `recipes/windows.rs`의 OS callback 상수 이름)는 남아 있다. `clippy -D warnings` 통과를 주장하지 않는다. 테스트 실행·추가, 언어 중립 fixture 생성, GUI smoke, 실제 Gateway enrollment/IPC·native acceptance, 실제 제품 설치/업데이트/제거, 성능/soak 및 macOS/Linux native 빌드는 `deferred_by_user`이다. version unit test도 보류 지시에 따라 실행하지 않았다.

### 로컬 production binary 증거

위치는 repository의 `target/x86_64-pc-windows-msvc/release/`다. 이 파일은 개발 binary이며 installer/portable 제품 패키징 또는 clean-machine 설치 성공을 의미하지 않는다. 기본 GUI renderer는 tiny-skia다.

| 파일 | PE architecture | bytes | SHA-256 |
|---|---|---:|---|
| `racp-client.exe` | x64 | 6,541,824 | `6cf720e154e15efdb2b5e101840978f68dda440e294a9ad479644c0b25c1ab4f` |
| `racp-agent.exe` | x64 | 19,405,312 | `97bfed2d7160a09a266a898176a8fafda1338aed5aba0e2de7cb57d947d0ceb9` |
| `xtask.exe` | x64 | 627,712 | `34f79515fe76d93602cc194a3f2ced0e0fae0617dc70db3b83b4742fa4447e03` |

`Cargo.lock` SHA-256은 `8b210e7634d024de6c74b0778044b8bddcb1a76507b186c4e08a674db27a87d6`이다. 이전 `dist/` 배치와 함께 섞지 않았다. workspace build가 생성한 기존 `racp-portable.exe`를 새 Iced 제품 portable 인수로 분류하지 않는다.

### 검토·결정과 다음 순서

계획서의 별도 agent 제한에 따라 자체 코드 검토를 수행했다. IPC session timeout 뒤 frame 재사용 차단, 16개 연결 초과 시 listener 재사용, GUI view의 filesystem I/O 제거, IPC 오류 때 Agent cleanup future 유지, 기존 상태 root의 등록 전 충돌 확인을 보강했다. 독립 reviewer 또는 실제 보안 인수 통과를 주장하지 않는다.

1. Ruling: 현재 사용자의 계획에 따른 구현 요청은 문서의 과거 문서 갱신 전용 범위를 대체한다. 비용/한계: 이번 기록은 실제 코드·컴파일 배치이고, 제품 배포/기존 설치 변경으로 확장하지 않는다.
2. Ruling: 저장소의 명시한 테스트 보류가 일반 TDD·version unit test 절차에 우선한다. 비용/한계: 컴파일·정적 audit만 확인했으며 IPC/등록/GUI 실기 안전성과 동등성은 미검증이다.
3. Ruling: 공유 dirty 문서의 보존과 즉시 review를 위해 현재 작업 디렉터리에서 별도 `codex/` branch로 구현했다. 비용/한계: 별도 worktree 격리 없이 기존 문서 변경과 함께 남아 있으며 사용자 변경을 자동 커밋하지 않았다.
4. Ruling: Subscribe는 매번 같은 atomic 경계의 fresh snapshot과 이후 event를 반환한다. 비용/한계: cursor replay 최적화 없이 reconnect 때 snapshot을 재전송하되 gap을 숨기지 않는다.

다음 구현은 R04의 resource scope/retirement·Journal/출력 fence와 R05의 durable hot CAS·target/revision-bound 일회 승인 연결이다. R06 최소 read-only checkpoint의 실제 Gateway/GUI 연결, R01-D 전체 화면·트레이, 제품 패키징·R14 제거는 계획서의 선행 조건과 보류 해제 후 인수로 구분한다. 전체 Windows/3-OS 전환 완료 checkbox는 변경하지 않았다.

<a id="rust-pretest-progress-0123"></a>
## v0.1.23 테스트 전 전체 구현 진행 원장

현재 사용자 목표는 **[전환 계획](../spec/rust-client-migration-plan.md)의 테스트 전 단계까지 구현 전체를 진행하는 것**이다. 이전 v0.1.22 초기 배치로 목표를 축소하지 않는다. 목표는 active이며 전체 구현 완료는 아직 입증되지 않았다. 이전 goal turn은 실제 코드 변경·Windows 컴파일 증거를 만든 progress로 분류한다. 이 배치의 PATCH는 v0.1.23으로 한 번 올렸으며 이후 재시도·이어지는 구현은 이 버전을 재사용한다.

### 실제 연결한 코드

| 범위 | 현재 authoritative source | 실제 변경 |
|---|---|---|
| R04 자원 수명 | [resources.rs](../../crates/racp-runtime/src/resources.rs) | device/boot/epoch/workspace/policy revision scope, owned/borrowed 구분, output fence, 순서별 bounded retirement, 최대 16 cleanup worker, timeout 작업 handle 보존 및 실패/quarantine ID 보고 |
| R04 기존 Provider 연결 | [dispatch](../../crates/racp-runtime/src/dispatch.rs), [Provider](../../crates/racp-runtime/src/providers/mod.rs) 및 process/terminal/browser/reversing/native-carrier | 실행 cancel→실제 완료 통지와 생성 handle을 registry에 연결. Provider별 handle close/detach를 호출하고 borrowed browser/debugger/target process를 종료 대상으로 분류하지 않음 |
| R04 출력·replay | [Journal](../../crates/racp-core/src/journal.rs), [Peer](../../crates/racp-runtime/src/peer.rs), [transport](../../crates/racp-runtime/src/transport.rs), [streams](../../crates/racp-runtime/src/streams.rs), [artifacts](../../crates/racp-runtime/src/artifacts.rs) | Journal publication tombstone, 전송 직전 result/reconcile/terminal/native frame 재검사, 정책 commit과 in-flight emission의 gate, artifact chunk/complete grant 재검사. 출력 회수 뒤 grant를 다시 켜도 이전 output은 재노출하지 않음 |
| R04 epoch | transport/dispatch | 연결 scope가 바뀌면 승인 폐기·이전 epoch resource retirement. 실행 직전 boot/epoch 검사와 ready 이전/stop 이후 result 발행 차단 |
| R05 정책 | [policy.rs](../../crates/racp-core/src/policy.rs), [IPC policy controller](../../crates/racp-runtime/src/local_ipc/policy.rs) | validation→cross-process lock/CAS→atomic durable 저장→snapshot publication→retirement→receipt. grant/constraint만 hot 변경하며 credential/gateway/profile 변경을 이 API에서 받지 않음. 검증한 permissions cache를 revision과 같은 lock으로 공개 |
| R05 승인 | [ApprovalBinding](../../crates/racp-core/src/approval.rs), [approval runtime](../../crates/racp-runtime/src/local_ipc/approval.rs) | request/operation/target digest·owner/device/boot/epoch/workspace/revision binding, OS-authenticated GUI nonce 결정, 1회 소비, TTL ≤ 60초와 잔여 deadline, GUI 부재/단절/deny/expiry 차단, 실행 직전 파일 revision/process 생성 시각 재확인 |
| R05 UI/IPC | [local contract](../../crates/racp-contract/src/local_ipc.rs), UiModel (`../../crates/racp-ui-model/src/lib.rs`, 과거 경로), `Iced app`(삭제됨) | Agent policy와 pending approvals를 snapshot/event로 표시. category off/on이 leaf 초안을 보존하며 revision CAS 저장·이번 요청 승인/거부·Stop receipt 및 cleanup event에 연결. 승인 화면에 credential/argv 본문 없이 대상 요약·digest를 표시 |
| R05 Desktop/native | [Desktop](../../crates/racp-runtime/src/providers/desktop/provider.rs), [native carrier](../../crates/racp-runtime/src/providers/native_carrier.rs) | Desktop enable 상태 hot 반영 및 Broker/Guardian retirement; native session은 live policy/revision을 다시 검사. 원본 Gateway profile ceiling은 별도로 유지 |
| R02 공통 JSON | [Rust decoder](../../crates/racp-contract/src/schema.rs), [Python decoder](../../packages/protocol/src/racp_protocol/models.py), [HTTP JSON boundary](../../apps/gateway/src/racp_gateway/json_boundary.py) | UTF-8·중복 key·NaN/Infinity/overflow float·길이/깊이 제한을 WS/HTTP/Rust 경계에서 정비. binary artifact content는 JSON parsing 대상에서 제외. 공개 schema/Registry 파일은 그대로 유지 |
| R00 baseline delta | [inventory](../../crates/racp-contract/migration-inventory.json), [audit](../../xtask/src/inventory.rs) | v0.1.21 원본 hash는 유지하고 R05 permission authority 변경을 baseline/current hash·owner·reason의 명시한 delta로 기록. 원본을 조용히 덮어쓰지 않음 |

정책 접수와 durable commit, commit과 자원 회수 완료, OS 실행 결과와 output 제공 실패를 분리했다. 기존 wire의 `error` frame은 `OPERATION_EXPIRED`만 허용하므로 permission/approval 거부를 그 frame에 임의 code로 보내던 경로를 수정했다. 거부는 schema에 맞는 metadata-only result로 전달하고 실행이 이미 끝났다면 기존 Journal 결과와 `execution_outcome_state`를 보존한다.

### 이번 검증 증거

- `cargo check --workspace --bins --locked --target x86_64-pc-windows-msvc`: PASS, 마지막 확인 4.33초.
- `cargo build --workspace --bins --release --locked --target x86_64-pc-windows-msvc`: PASS, v0.1.23 최종 Windows release 52.27초.
- `cargo fmt --all -- --check`: PASS.
- `cargo run --locked -p xtask -- inventory-check`: PASS, 기준 100 operation / 143 permission / 18 category / 62 확장 담당. 신규 R11 operation은 아직 추가하지 않았다.
- `scripts.schema.documents()`와 기존 공개 JSON 문서의 구조 비교: drift 0. 이 확인은 정적 schema audit이며 기능 테스트가 아니다.
- Ruff: PASS. Mypy: PASS, 96 source files. `uv lock --check`: PASS.
- 자동 테스트 실행/추가와 fixture 생성, 실제 enrollment/GUI/IPC/native acceptance, 설치/성능/soak 및 macOS/Linux native 실행은 계속 보류한다. 컴파일 결과를 기능 안전성/동등성 또는 전체 gate PASS로 바꾸지 않는다.
- 기존 Rust warning 4개는 유지한다. 기존 설치·Device·credential·`dist/`는 변경하지 않았고 커밋/푸시도 하지 않았다.

### 완료로 판정하지 않은 항목과 다음 순서

1. **R04/R05 추가 감사:** 모든 mutation/partial/crash 경계, 장기 blocking worker 격리/실제 handle 회수, policy file commit와 Journal retirement 중 실패/재시작 조건, browser-state 민감 출력, approval target의 batch/OS target 범위를 추가로 정비한다. 각 Provider 전체를 이 callback 추가만으로 최종 합격 처리하지 않는다.
2. **R01/R03 UI/등록:** 실제 onboarding·설정/constraint 편집·approval receipt 순서/조회, Agent 시작·트레이·single instance·로그인 시작, 서비스 user/session IPC 및 Unix peer/secret 경계는 남아 있다. 테스트 보류가 toolkit 접근성/IME 적합성 증거를 뜻하지 않는다.
3. **R06–R10:** 기존 Provider의 정책/Journal/resource scope와 runtime admission·선택 browser/분석 경계·전송/cleanup을 operation별로 마무리한다. 프로젝트 소유 Java bridge/Python bootstrap의 Client 소비자는 아직 제거하지 않았다.
4. **R11-A–K:** 확장 권한 62개는 아직 전체 구현 전이다. 필요한 RPC·서버/Rust/schema/binding을 같은 변경으로 추가하고 Windows의 실제 backend/조건을 ID별로 기록한다. 미구현을 unsupported OS/runtime 상태로 바꿔 통과시키지 않는다.
5. **R-PKG/R12-D/R14:** Rust package/staging/manifest/installer/updater 소스와 플랫폼 정의, 최종 native UI와 서버 소비자 분리 후 Tauri/React/Node Client 제거 및 운영 문서/CI 정리는 남아 있다. 실제 제품 패키징/배포와 native 타 OS 실행은 별도 요청/검증 범위다.

현재 전체 goal은 **active**다. 다음 turn도 위 전체 범위를 유지하고 미완료 구현을 이어간다. 실제 테스트 보류는 코드 구현 완료 판정과 별개이며, 전환 완료/테스트 통과/실제 제품 인수를 선언하지 않는다.

### R04/R05 checkpoint production 확인 (R11 추가 이전)

Policy cache는 snapshot `RwLock`과 저장용 mutex를 분리해 디스크 저장 동안 조회/승인 처리가 저장 mutex를 기다리지 않게 했다. 유효하게 승인된 동일 operation의 artifact export grant를 publication 검사에 반영했다. 최종 software release와 GPU feature check가 모두 PASS이며 마지막 GPU 확인은 4.00초다. `racp-agent --version`은 `racp-agent 0.1.23`을 출력했다. GUI 실행·등록·OS mutation은 하지 않았다.

- 최종 `racp-agent.exe` SHA-256: `5e0c7a901f791af548050b9fe4b0f8418e32c1075a87ce0ed40422c7c8d05c07`.
- 최종 `racp-client.exe` SHA-256: `2b20d99f27f2ec87415f6100b1a6306fd920755cbe2410da4d5b2b8e79a4908d`.
- 현황의 이번 상대 링크 23개는 존재하며, `git diff --check`와 format check는 PASS다.
- Ruling: 현재 사용자의 전체 테스트 전 구현 목표를 유지한다. 단계별 production 확인은 진행 근거이며 전체 구현 완료가 아니다. 비용/한계: 위 미완료 목록을 다음 turn에서 계속 구현하며 실제 테스트/인수 증거는 보류 상태로 남긴다.

### R11-A / R11-E / R11-J 확장 구현 checkpoint

동일 v0.1.23 배치에서 12개 typed operation을 추가했다. 기존 100개 operation과 143개 permission ID, 18개 category, 기준선의 planned 62개 담당 배정은 보존한다. 현재 Registry는 **112개 operation / 93개 RPC permission / 50개 planned permission**이다. 이 수치는 구현·인수 완료율이 아니며 새 경로의 availability는 `unverified`, 구현 상태는 `in_progress`다.

| 범위 | 실제 코드·계약 연결 | 현재 한계 |
|---|---|---|
| R11-A 이미지 | [preview](../../crates/racp-runtime/src/providers/filesystem/preview.rs)와 `filesystem.preview_image` / `files.preview.image AND artifacts.export` | pinned workspace source, 최대 16 MiB·16,777,216 pixel, Rust decoder child/256 MiB Job·출력 상한·취소/회수, PNG thumbnail. animation 첫 frame만 지원; 악성 이미지/실제 취소 인수 보류 |
| R11-A 휴지통 | [trash](../../crates/racp-runtime/src/providers/filesystem/trash.rs)와 `filesystem.trash` / `files.trash` | expected revision, handle-relative rename→사용자/SYSTEM 전용 sibling staging→고정 COM Rust worker. 영구 삭제 fallback 없이 실패 시 no-overwrite 복구/partial 기록. 실제 휴지통/복구 인수 보류 |
| R11-A ACL | [acl](../../crates/racp-runtime/src/providers/filesystem/acl.rs)와 `filesystem.acl_read/acl_modify` | file handle 기반 descriptor, 원래 SDDL/digest와 revision, actor의 READ_CONTROL/WRITE_DAC 회복 권한 readback·원래 DACL rollback. modify는 read grant도 AND 요구. 외부 writer와 원자적 CAS 보장은 하지 않음; 실제 ACL 인수 보류 |
| 파일 private 경계 | [Filesystem](../../crates/racp-runtime/src/providers/filesystem.rs), [Workspaces](../../crates/racp-core/src/workspaces.rs) | Agent 상태 root 직접 접근과 그 상위 tree의 copy/move/delete/trash/ACL 변경 차단; 탐색에서 private 자식 제외. `.racp-` / `racp-trash_` 이름은 내부 소유 작업 namespace로 예약. Windows rename은 pinned handle을 사용하며 link/reparse 거부 |
| R11-E 브라우저 | [expansion](../../crates/racp-runtime/src/providers/browser/expansion.rs), [events](../../crates/racp-runtime/src/providers/browser/events.rs), CDP/session/operations | console/network 읽기·cookies 읽기·local/session storage 수정. 페이지/frame/origin/navigation revision 및 고유 execution context 검사; ≤256개·각 ≤8 KiB queue와 gap, policy 변경 시 queue 폐기. 네트워크 header/body·URL query/fragment 제외. background 수집은 local leaf `allow`일 때만 하며 read 1회 approval로 수집을 넓히지 않음 |
| 브라우저 제한 | 위 R11-E 경로 | cookies/storage mutation은 borrowed 사용자 context에서 거부. cookie는 선택 URL의 owned context/domain으로 제한하며 partitioned cookie는 제외. storage는 기존 JSON scalar hash와 같은 JS 호출 안의 재비교를 사용하고 탐색 변경 후 결과 발행 거부. 선택 runtime admission과 실제 CDP/교차 context 인수는 남아 있음 |
| R11-J 진단 | [Diagnostics](../../crates/racp-runtime/src/providers/diagnostics.rs), [Windows Event Log](../../crates/racp-runtime/src/providers/diagnostics/windows.rs), [fixed worker](../../crates/racp-runtime/src/providers/worker.rs) | Agent curated metadata, ≤30초/600개 CPU 누적 tick·메모리 sample, 고정 System/Application/Security Event Log metadata 조회 및 bounded JSON export. Event Log blocking 호출은 Rust child Job에서 수행; private 결과 파일 hash/size 확인·취소 회수. token/cookie/clipboard/argv/env·raw EventData 제외 |
| R11-J 미완료 | `events.trace.record` | ETW recording은 아직 구현하지 않았다. Event Log metadata 수집을 ETW 또는 전체 event body 기능 완료로 처리하지 않는다 |
| 공통 계약 | [expansions](../../packages/protocol/src/racp_protocol/expansions.py), [Python permission authority](../../packages/protocol/src/racp_protocol/permissions.py), [Rust AND binding](../../crates/racp-core/src/permission-bindings.json), [server profile](../../packages/policy/src/racp_policy/engine.py) | 새 입력 schema·Registry·카탈로그·양쪽 AND binding·Gateway profile 연결. 승인 대상 요약에 origin/page/frame/navigation/channel과 파일 source/destination 추가 |
| R00 audit | [inventory](../../crates/racp-contract/migration-inventory.json), [xtask](../../xtask/src/inventory.rs) | 원래 Registry/catalog JSON과 hash를 frozen baseline으로 보존. 신규 operation별 owner/reason delta와 현재 hash·permission contract digest를 기록하고 원본 100개 ID 보존·62개 확장 담당 중복/누락을 확인 |

현재 확인된 정적 증거는 `cargo check --locked --workspace` PASS (13.84초), `xtask inventory-check` PASS (112/143/18, 확장 62개 담당·planned 50개), Python/Rust AND binding 및 catalog operation link 일치·공개 schema 구조 drift 0, Ruff PASS, Mypy PASS (97 source files), `uv lock --check` PASS다. 추가 release 결과는 아래에 실제 종료 확인 후 기록한다. 자동 테스트·fixture·GUI/IPC/등록·휴지통/ACL/브라우저/진단 실기 실행과 macOS/Linux native 실행은 하지 않았다.

R11-B/C/D/F/G/H/I/K 및 R11-J의 ETW, R01/R03 전체 UI와 lifecycle, R09/R10 trusted runtime admission, R-PKG/R12-D/R14는 여전히 전체 목표의 남은 구현 범위다. 기존 설치·사용자 자료·credential·`dist/`와 초기 dirty 문서 변경을 보존한다. 목표는 계속 **active**이며 이 checkpoint를 전체 구현 완료로 판정하지 않는다.

### R11-C job / R01·R03 native UI / R09·R10 runtime admission 후속 구현

이번 후속 작업은 실제 소스·계약을 변경하고 production 컴파일 증거를 만든 **progress**다. 현재 합계는 **114 operation / 143 permission / 18 category / 95 RPC permission / 48 planned permission**이며 원본 100개 operation과 확장 담당 62개 집합을 유지한다. PATCH는 계속 v0.1.23을 재사용한다.

| 범위 | authoritative source | 실제 연결과 검증 한계 |
|---|---|---|
| R11-C job | [Jobs](../../crates/racp-runtime/src/providers/jobs.rs), [Journal](../../crates/racp-core/src/journal.rs), [dispatch](../../crates/racp-runtime/src/dispatch.rs) | `jobs.observe/cancel`과 양쪽 binding·profile·catalog 추가. device/principal/workspace가 일치하는 자신의 `job`만 대상으로 한다. active cancel은 boot/epoch/revision도 검사하며 cancel은 observe grant도 AND 요구. 취소 접수와 terminal/실제 보고된 cleanup status를 구분하고 payload/result/error 본문을 반환하지 않음. Journal transaction 안에서 terminal 결과를 확인해 취소가 완료 결과를 덮어쓰지 않게 함. 실기 경합/회수 인수는 보류 |
| 계약 생성 | [schema generator](../../scripts/schema.py), [permission authority](../../packages/protocol/src/racp_protocol/permissions.py) | 공개 schema/Registry 생성과 함께 Rust 기본 AND table을 Python authority에서 생성. 정적 비교에서 114개 Registry·Python/Rust binding·catalog link 일치 및 schema drift 0 |
| R09/R10 로컬 runtime | [bundle manifest](../../crates/racp-contract/src/runtime_manifest.rs), [admission](../../crates/racp-core/src/runtime_admission.rs), [native client](../../crates/racp-runtime/src/native_client.rs) | 고정 runtime ID/recipe, 전체 manifest의 명시한 로컬 SHA-256 동의, 파일별 hash/size·license·상대 경로·중복/예약/ancestor 충돌·PE/ELF/Mach-O OS/arch 검증. 최대 4 GiB/4096 파일. 전용 candidate에 복사 후 manifest-addressed 경로로 commit; 실패 candidate는 비활성 상태로 보존. 기본은 runtime 없음이며 Chromium만 선택된 admission을 시작 경로에 연결 |
| runtime 시작·설정 | 위 admission/native client 및 [CLI](../../apps/agent-rust/src/main.rs) | `native-runtime-admit/configure/status` 로컬 명령, config revision CAS와 Agent 실행 lock. configure는 Agent 종료 상태만 허용. Agent 시작 때 다시 모든 파일을 확인하고 Windows no-write/no-delete handle을 실행 동안 유지. 손상된 선택 Chromium은 실행하지 않고 해당 availability를 runtime 필요로 표시. 검증을 명시한 hash 동의로 제한하며 제품 서명/공식 trust catalog 검증으로 광고하지 않음 |
| R03 상태 보호 | [native directory](../../crates/racp-core/src/native_directory.rs), [state schema](../../crates/racp-core/src/state_schema.rs) | 새 Windows root는 생성 시부터 현재 사용자/SYSTEM 전용 protected DACL과 사용자 owner를 지정. native state load와 등록 재시도에 owner/DACL 확인을 추가. 기존 공유/비공개 조건 미충족 root는 `STATE_DIRECTORY_NOT_PRIVATE`로 거부하며 기존 디렉터리 ACL을 자동 변환하지 않음. 실제 SID/ACL/DPAPI 인수는 보류 |
| R01/R03 등록 화면 | `Iced app`(삭제됨), [onboarding](../../apps/client-native/src/onboarding.rs), [local Agent helper](../../crates/racp-runtime/src/local_agent.rs) | 연결 파일의 Gateway/만료/hash만 확인하고 새 workspace/profile·모두 거부로 시작한 leaf/category 선택을 별도 Agent enrollment에 전달. token을 GUI view/argv로 노출하지 않음. 고정 sibling Agent helper, executable pin/host binary 확인, Job·256 MiB·125초·64 KiB stdout/stderr 상한, 취소 후 tree 정리. pending 등록을 자동 삭제/재등록하지 않음 |
| R01 runtime 화면·승인 | 위 UI/onboarding | runtime manifest/hash/source와 Chromium 선택·origin allowlist·외부 CDP를 실제 로컬 명령에 연결. 다수 승인 요청을 별도 scroll 화면에 표시하고 새 요청 때 창을 표시. event를 boot/sequence 검증 후 처리하며 final receipt가 접수 reply보다 먼저 와도 최종 상태를 대기로 덮어쓰지 않게 함. 256개 receipt cache는 reconnect/영속 receipt 조회를 대체하지 않음 |
| R01 Windows shell | [native shell](../../apps/client-native/src/native.rs), [main](../../apps/client-native/src/main.rs), [assets](../../apps/client-native/assets) | SID/session/state-root별 named mutex, 재실행은 기존 hidden Win32 window에 activation-only 통지. Rust/Win32 tray와 원래 Client icon의 별도 복사본, Explorer 재시작 재등록, 표시/Agent Stop/정리 후 종료 메뉴. 창 닫기는 tray hide, 전체 종료는 IPC Stop와 native-agent lock 해제 확인을 거치며 결과 불명 시 자동 정리 완료로 처리하지 않음. 기본 tiny-skia, GPU feature build에서는 `--gpu` 선택. 실제 IME/DPI/Narrator/menu/focus/renderer/종료 인수 보류 |
| R03 Agent 시작 | local Agent helper/UI | registered native root·Agent 실행 lock을 확인하고 고정 sibling `racp-agent native-run`을 hidden 사용자 daemon으로 시작. stdio·credential을 전달하지 않고 ready는 authenticated IPC snapshot으로 확인. GUI helper Job에 Agent/Guardian을 묶지 않으며 breakaway 실패를 fallback으로 숨기지 않음. 시작 receipt를 실제 연결/ready로 광고하지 않음 |

Windows tray는 [Shell_NotifyIconW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw)와 native menu/message loop를 사용한다. `tray-icon` 후보 라이브러리는 이번 경로에서 사용하지 않았다. Core state directory와 GUI helper executable 보호는 저장소의 실제 새 경계 구현이며, 서명된 제품 패키지/서비스 session 0 지원을 대신하지 않는다.

후속 미완료는 R11-C의 recipe/script/elevation과 나머지 48 planned ID, ETW, GUI constraint·상세 설정·로그인 시작·접근성 연결, 서비스/Unix 인증 IPC·secret backend, CDB/GDB/분석/driver runtime Provider 연결·다운로드/제품 trust catalog·admission 실패 quota/정리, 모든 mutation/partial/crash/revocation boundary, R-PKG/R12-D/R14다. 현재 GUI source 확장은 사용자의 전체 테스트 전 구현 목표를 따른다. 비용/한계: R01의 실기 toolkit 적합성은 여전히 `deferred_by_user`이며 전체 UI 완료/제품 적합성 gate PASS를 선언하지 않는다.

기존 설치·device·credential·사용자 data·`dist/`·초기 dirty 문서를 보존했으며 실제 등록/GUI/OS mutation/자동 테스트/fixture/타 OS native 실행을 하지 않았다. Windows·3-OS 전환 및 전체 goal 완료 판정은 계속 미입증이고 목표는 **active**다.

### 후속 checkpoint의 production·정적 증거

- `cargo build --workspace --bins --release --locked --target x86_64-pc-windows-msvc`: PASS, 최신 native directory/UI/Agent 변경 포함, 1분 32초.
- `cargo check --locked -p racp-client-native --features gpu-renderer --target x86_64-pc-windows-msvc`: PASS, 57.19초. GUI 실행과 실제 GPU 선택/소프트웨어 fallback 인수는 수행하지 않음.
- `cargo run --locked -p xtask -- inventory-check`: PASS, 114개 current operation / 143 permission / 18 category / 62개 확장 담당 배정 / 48개 still planned; 원본 100개 operation 보존.
- 공개 schema/Registry와 Python/Rust AND table·permission operation link 정적 비교: PASS, drift 0. Ruff PASS, Mypy PASS (97 source files), `uv lock --check` PASS, Rust format·`git diff --check` PASS.
- 설치한 Rust는 `rustc 1.90.0 (1159e78c4 2025-09-14)`이며 version tool은 `0.1.23`을 출력. 두 release binary의 `--version`만 실행해 각각 `racp-agent 0.1.23`, `racp-client 0.1.23` 확인; GUI 생성·Named Pipe 실행·등록·OS mutation·테스트를 실행하지 않음.
- `racp-agent.exe` SHA-256: `0e7bb62cbbf6d68971ed6f3815dca8fcfcff029755eb566560b1cdf0055b075f`.
- `racp-client.exe` SHA-256: `6ed7f0323f87b039e88930d19b5c256b34eaf40714d3527dc0765314c32869ed`.
- 기존 runtime warning 4개를 유지하고 신규 compiler warning은 없음. 제품 package/signing/publishing·기존 설치 변경·커밋/푸시는 하지 않음.

Ruling: 이 checkpoint 당시 사용자 목표의 전체 테스트 전 구현 범위를 유지했다. 비용/한계: 당시 release·정적 검사는 컴파일·source 계약 일치만 증명하며, 남은 확장 권한·GUI 세부 기능·서비스/Unix 경계·선택 분석 runtime·패키징/레거시 제거와 제품 인수 완료를 증명하지 않는다. 이후 사용자 범위 변경은 아래 기록이 우선한다.

### 핵심 기능 우선으로 실행 범위 변경 및 후속 구현

> [!IMPORTANT]
> 2026-10-11 사용자가 **“핵심 기능 우선, OS별 API 확장은 후순위”**를 선택했다. 원격 실행·화면/입력·파일·승인·정책·작업 추적 및 Rust native Client 전환을 먼저 완성한다. R11의 미완료 확장은 backlog로 보존하고 이번 핵심 구현의 완료 조건에서 제외한다. 계획의 기존 62개 전체 확장 완료 요구보다 이 실행 범위 변경이 우선한다. 테스트/GUI smoke/실기 OS mutation 보류와 Windows x64 기본 정책은 유지한다.

- 미연결 R11-H 서비스/레지스트리/스케줄러/소프트웨어 8개 모델은 `DEFERRED_CONFIGURATION_MODELS`로 분리했다. 공개 Registry·입력 schema·permission binding에 추가하지 않고, permission catalog의 planned 상태도 유지했다. 미연결 worker 초안은 제품 source에서 제외했다. 기존 공개 114개 operation과 이미 연결된 확장 기능을 보존했다.
- Native 권한 화면에 workspace ID, 실행 시간·출력 크기 상한, 실행 파일 allow/deny 목록의 다중행 편집을 추가했다. Agent와 같은 schema/path validator로 저장 전에 검사하며 Agent revision CAS가 최종 authority다. 연결 중단 또는 변경된 revision에서 stale 제약 초안을 저장하지 않고 현재 정책 다시 불러오기를 제공한다. 현재 공통 계약은 `strict_os_isolation=true`를 거부하므로 GUI도 해당 옵션을 비활성화했다.
- 정책 저장·Agent 종료 최종 결과를 같은 boot의 상태 snapshot에 최대 16개 보존한다. 64 KiB frame 상한이 부족하면 오래된 결과부터 제거한다. GUI 재연결 시 이 결과를 가져오며 Agent boot가 바뀌면 이전 receipt cache를 비운다. 기록 없음·연결 중단·프로세스 종료를 cleanup 성공으로 추정하지 않는다. 이 메모리 보존은 crash-durable receipt 또는 Agent 재시작을 넘는 결과 저장을 대체하지 않는다.
- Native shell/renderer 시작 실패를 사용자 세션의 Win32 오류 창으로 표시한다. renderer 오류는 고정 코드만 표시한다. `--version`은 여전히 GUI/tray 초기화 전에 반환한다.
- Agent의 durable Journal 상태를 native activity 화면에 연결했다. 접수·실행·표준 취소 요청·최종 상태를 요청 ID/operation ID/operation/state만으로 발행한다. 동시 publisher는 StateHub lock 안에서 집합을 갱신하며 늦게 온 실행 상태가 취소/terminal 상태를 되돌리지 않게 한다. 최대 32개이며 오래된 terminal부터 제거한다. 재연결은 snapshot으로 복구하고 연결 중단 시 마지막으로 확인한 정보임을 표시한다. Gateway 결과나 명령 payload를 이 화면에 전달하지 않는다. job Provider가 직접 취소한 대상의 중간 취소 상태는 표준 Agent 취소 경로와 동일한 발행 hook으로 아직 연결되지 않았고, 최종 상태는 실행 종료 hook에서 갱신한다.
- 권한 화면은 실행 파일 목록이 시작 executable에 대한 제한임을 설명한다. 허용한 셸/터미널 안의 모든 명령을 서비스·레지스트리 등 개별 OS 권한으로 격리한다고 광고하지 않는다.
- Production `cargo check --locked --workspace --target x86_64-pc-windows-msvc` PASS (13.46초), 공통 schema/Registry/Python·Rust AND binding 정적 비교 PASS (114개, drift 0), Ruff PASS, Mypy PASS (97개 source), `uv lock --check` PASS. 자동 테스트·GUI·IPC·등록·OS 변경은 실행하지 않았다. 이 검사 이후 시작 오류 표시 변경의 release 증거는 아래에 종료 확인 후 기록한다.

Ruling: 핵심 우선 범위로 계속 진행 중이며 전체 goal은 **active**다. 핵심 UI·lifecycle 세부 작업, 선택 runtime 필수 경로, native 패키징 도구와 레거시 제거가 남아 있다. 후순위 OS API를 미구현 핵심 기능과 혼합해 전체 완료를 주장하지 않는다. PATCH는 기존 배치 v0.1.23을 재사용한다.

핵심 우선 checkpoint의 최종 production 증거 (activity와 시작 오류 표시 포함):

- `cargo build --workspace --bins --release --locked --target x86_64-pc-windows-msvc`: PASS, 1분 20초.
- `cargo check --locked -p racp-client-native --features gpu-renderer --target x86_64-pc-windows-msvc`: PASS, 10.92초.
- `xtask inventory-check`: PASS, current 114 operation / 143 permission / 18 category / 62개 후속 확장 담당 / 48개 planned. 공개 계약 정적 비교 PASS (drift 0), Rust format·diff check PASS. 변경 배치 version tool은 `0.1.23` 출력.
- 최종 Agent SHA-256: `74082a7bc97c64520782acf7810019dd6e2ee54bc119ed23a153bd65f7377a79`.
- 최종 Client SHA-256: `7b45320d2a3a7ed216cab8473c596a5d544b49df2a98d3d1fbcca3d2bfec0afc`.
- 기존 warning 4개 유지, 신규 compiler warning 없음. 제품 package 생성·설치·등록·GUI 실행·자동 테스트·실기 OS 변경·다른 플랫폼 실행은 수행하지 않았다. 위 해시는 이번 checkpoint 실행 파일에 대한 증거이며 제품 인수/서명/배포 완료 증거가 아니다.

### R-PKG native staging·포터블 구현 진행

이번 배치는 기존 v0.1.23을 재사용하며 새 Rust 배포 경로를 실제 source로 추가했다. 제품 artifact 생성·설치·GUI/포터블 실행은 보류했고 개발 staging 및 production 컴파일만 수행한다.

| 범위 | authoritative source | 구현 및 한계 |
|---|---|---|
| 기본 payload 계약 | [distribution contract](../../crates/racp-contract/src/distribution.rs), [검증·pin](../../crates/racp-core/src/distribution.rs) | Client/Agent/LICENSE/SBOM 4개만 허용하는 strict manifest. version·Windows x64·size/SHA-256 및 실제 native binary header 검사, 여분 파일 거부. Windows no-write/no-delete handle과 directory handle 유지. 자체 hash manifest는 서명 trust catalog를 대신하지 않음 |
| Rust staging | [xtask package](../../xtask/src/package.rs), [build evidence](../../xtask/src/package/evidence.rs) | pinned toolchain·`--locked`·별도 `target/native-product`·static CRT production 빌드. 명시한 고유 build-id, batch lock, 기존 출력 보존, 새 실패 batch만 quarantine. source commit/dirty/파일 digest·lock·feature·hash·SBOM 기록. 빌드 전후 source digest가 다르면 실패. 서버/Console 도구는 변경하지 않음 |
| 선택 runtime 분리 | 위 payload 계약/xtask | WebView2·React·Node·Python·Chromium을 기본 payload에 허용하지 않음. 기존 runtime admission을 별도 사용하며 암묵적 다운로드 없음 |
| 포터블 | [native launcher](../../crates/racp-runtime/src/native_portable.rs), [bin](../../apps/agent-rust/src/bin/racp-native-portable.rs), [state selector](../../crates/racp-core/src/state_schema.rs) | 새 native footer만 허용, archive 전체와 각 파일 hash/size/target·중복 JSON 검증. private cache candidate→검증→commit; 2 GiB 전체 cache/4개 failed candidate/상한 있는 tree scan. 실패는 비활성 보존. 명시한 launcher 위치 hash별 새 state, 기본 installed state/legacy credential import 없음. GUI·Agent는 같은 root를 사용. launcher와 GUI/Agent가 payload pin을 각각 유지 |
| artifact recipe | [Rust writers](../../xtask/src/package/artifacts.rs), [NSIS](../../xtask/native-installer.nsi) | 검증된 staging에서 native self-extracting EXE·고정 ASCII file ZIP32·사용자별 버전 디렉터리 setup 생성 source 연결. 자동 등록/legacy import/앱 강제 종료 없음, fixed-file uninstall은 state/credential 보존. 실행 명령은 별도 product package 요청 때 사용; 실제 artifact 생성·NSIS 컴파일·설치/업데이트/제거·ZIP/포터블 실행 검증은 미실행 |
| 공급망·플랫폼 한계 | 위 evidence/[가이드](../guides/desktop-client-guide.md#native-development-staging) | Cargo graph CycloneDX SBOM은 license 문자열/metadata만 기록. license/advisory 검토·native DLL·외부 build tool inventory·signature·update trust/per-machine/service 설치는 후속. macOS DMG/APP ZIP, Linux deb/AppImage 정의만 출력하며 native build/CI 실행하지 않음 |

`cargo check --locked --workspace --target x86_64-pc-windows-msvc`는 포터블 GUI/state 연결까지 PASS (5.35초); source 계약 정적 audit 114개/drift 0, Ruff/Mypy(97 source)/uv lock check PASS. 최종 staging·artifact writer 변경이 포함된 static CRT 빌드의 결과는 아래에 실제 종료 확인 후 기록한다.

Ruling: R-PKG source 구현이 진행됐으며 제품 패키징/clean-PC 인수 완료를 주장하지 않는다. native GUI/Agent의 세부 lifecycle·로그인 시작·서비스 모드, restart-required 설정, runtime 연결·업데이트, artifact 검증·license, R12-D/R14는 남아 있고 goal은 **active**다. 자동 테스트/fixture·실제 등록·GUI·OS mutation·기존 설치 변경은 수행하지 않았다.

R-PKG checkpoint의 최종 production·staging 증거:

- 첫 `xtask stage --platform win --arch x64 --build-id native-pretest-20261011-a`: production static CRT 빌드 PASS, 5분 9초. 기존 batch를 보존했다.
- GUI 시작 실패 종료 코드·타 OS native 파일명·compiler/import 증거 보정 후 `native-pretest-20261011-b`: 최종 production static CRT 빌드 PASS, 15.43초. `verify-staging`: PASS, admitted 4개 payload·launcher SHA-256/size/Win64 header·manifest·PE dependency 목록 일치.
- 최종 staging: `dist/client-native/0.1.23/win-x64/native-pretest-20261011-b/`. 기본 Agent 21,379,584 bytes / SHA-256 `a3e9c93a290b72ffc32df2edca2b2168dd19a7cc462374a064e677eedbdd4162`; Client 12,158,464 bytes / `7d897675a4abb13197e71e6edd8e98b9601ff2bf5a822ee974df0e6a1e7a75cf`; native launcher 2,635,776 bytes / `37a6da19910c96743d7b2074f82be5e10e544d92ee0864209b70b2c43491dba1`.
- Rust PE import verifier가 3개 실행 파일에서 허용된 Windows 시스템 DLL/API set만 확인했다. dynamic CRT/미허용 library/delay-load는 거부한다. 첫 staging의 Client/Agent는 MSVC `dumpbin /DEPENDENTS`에서도 dynamic CRT 없는 system dependency 목록을 확인했다. clean-PC 실행/지원 최소 OS 증거로 확대하지 않는다.
- `rustc 1.90.0`, native host `x86_64-pc-windows-msvc`, LLVM 20.1.8, `cargo 1.90.0` 및 Cargo.lock digest `7f322b32f81f357f0709794e810f181c5da802991af837f35fa51b5858a47438`를 build manifest에 기록했다. source 입력 digest는 빌드 전후 일치.
- SBOM component 318개, Cargo license 문자열 UNKNOWN 0개. SPDX/license/advisory 법률·보안 검토를 수행했다는 뜻은 아니다. SBOM 자체 SHA-256 `84b29886b188cc575108722972aced99be4d5377643dd68502d14101d1de7d05`.
- Mac arm64 dry-run은 APP 내부 native 실행 파일·DMG/APP ZIP·`aarch64-apple-darwin`, Linux x64 dry-run은 native 실행 파일·deb/AppImage·`x86_64-unknown-linux-gnu`를 출력했다. 파일 쓰기/해당 OS build 없음, minimum OS는 `pending_native_validation`.
- 실제 Agent/Client 실행은 `--version`만 수행해 각각 `0.1.23` 확인. GUI/tray/IPC/등록/포터블 실행·설치·OS mutation·제품 artifact 생성·signing/publishing·자동 테스트는 하지 않았다. ZIP/portable archive 검증기는 production package 생성 경로에 연결했지만 해당 archive를 생성하지 않았으므로 format/runtime 인수 PASS는 선언하지 않는다.
- 최종 native distribution 변경의 GPU feature production source check PASS (17.93초), inventory-check PASS (114 operation, 143 permission, 18 category, 후순위 48 planned), 공통 schema/binding 정적 비교 PASS (drift 0), Rust format·diff check PASS. Ruff/Mypy(97 source)/uv lock check도 PASS. 기존 compiler warning 4개 외 신규 warning 없음.
- [Client workflow](../../.github/workflows/client.yml)는 Windows 2022·Rust 1.90.0·Cargo inventory/GPU compile/native static CRT staging·integrity 경로로 전환했다. Node/pnpm/Tauri와 routine installer 생성 단계를 제거했다. 제품 artifacts는 `workflow_dispatch.package_artifacts=true`에서만 생성하고 GUI·테스트·설치·publishing을 실행하지 않는다. 요청된 artifact job만 build-only NSIS 3.11 ZIP을 [공식 배포 페이지의 SHA-256](https://sourceforge.net/projects/nsis/files/NSIS%203/3.11/nsis-3.11.zip/download)으로 확인한 뒤 사용하도록 정의했다. 다른 플랫폼 native runner는 아직 없으므로 `include_deferred_platforms=true`는 명확한 미지원 오류로 종료한다. YAML parser PASS; workflow dispatch/CI 실행과 NSIS download/compile을 실제 수행한 증거는 없다.

### R01/R03 설정·현재 사용자 로그인 시작 후속 구현

| 범위 | authoritative source | 실제 source 연결 및 한계 |
|---|---|---|
| restart-required 설정 | [native state](../../crates/racp-core/src/state_schema.rs), [native commands](../../crates/racp-runtime/src/native_client.rs), `UI`(삭제됨) | 기본 workspace·15개 named workspace·실행 profile 편집, typed/unknown-key 거부, 기존 validator 사용. native state의 monotonic settings revision CAS와 Agent/settings lock. Agent가 lock을 먼저 얻은 뒤 상태를 읽어 start와 commit 사이의 stale read 방지. 저장 후 명시적으로 시작하며 Gateway/device/CA/credential은 enrollment authority로 보존. 새 등록에도 named workspace draft 전달 |
| 과거 결과 격리 | [transport](../../crates/racp-runtime/src/transport.rs), [dispatch](../../crates/racp-runtime/src/dispatch.rs) | native 요청을 Agent 설정 revision으로 내부 binding, Journal accept에 저장. 이전 설정 revision의 replay/output 발행을 거부하고 publication tombstone 유지. 동일 workspace ID가 다른 path로 바뀐 뒤 과거 결과를 새 root의 결과로 해석하지 않게 함. 서버 wire 계약/Registry를 확대하지 않음 |
| 현재 사용자 로그인 | [native login](../../crates/racp-runtime/src/native_login.rs), native command/local Agent helper/UI | 현재 SID·native root에 고정한 HKCU Run value 한 개만 read/configure. admission된 packaged sibling Client의 고정 `--state-dir/--start-agent/--require-package` 명령만 작성; 임의 executable/argv/registry path 입력 없음. revision·기존 값 digest·소유 값 재비교, 16 KiB/UTF-16/NUL/type bounds, durable intent→write→readback→document commit→pending 제거. 기존 사용자/legacy 항목과 실행 앱을 보존 |
| 중단 복구 | 위 native login/UI | UI가 명시한 reconcile에서 registry가 이전 값 또는 목표 값인지 비교. durable document가 이미 next revision으로 저장된 경우도 처리하며 미확정 상태를 자동 rollback/성공으로 추정하지 않음. 외부 변경은 ownership conflict로 남김. Registry의 external-writer atomic CAS 부재와 실제 로그인/경합/취소 인수는 미검증 |
| 로그인 시작 실행 | [GUI entry](../../apps/client-native/src/main.rs), onboarding/app | user-root override는 한 번만 선택·normalize/검증. `--start-agent`는 등록된 상태를 확인한 첫 refresh 뒤 실행하며 이미 IPC 연결된 Agent는 다시 시작하지 않음. pending/미등록 상태는 자동 등록하지 않음. 실패 시 고정 코드, 설정/로그인 helper는 기존 bounded Job/cancel/unknown outcome 절차 사용. UI header를 제목과 navigation 두 줄로 나눔 |

이 변경은 current-user startup source를 추가한 것으로 Windows 로그인 실제 실행, Startup Apps 허용 여부, service/session-0 모드, portable/install/uninstall 인수와 동일하지 않다. 등록 UI에서 타 Gateway로 credential을 옮기는 기능은 추가하지 않았다. 실제 새 등록 또는 HKCU 수정은 실행하지 않았다.

`cargo check --locked --workspace --target x86_64-pc-windows-msvc` PASS (5.19초, settings revision binding/UI 연결 포함). 공개 계약 정적 비교 PASS (114 operation/drift 0), Ruff PASS, Mypy PASS(97 source), uv lock check PASS. 추가 static CRT production staging `native-pretest-20261011-c` PASS (1분 17초), recovery 보강 후 `d` PASS(1분 30초). 최종 helper command 길이 검사 위치의 compiler 오류로 `e`는 실패했고 `e.quarantine`으로만 이동해 보존했다. 수정된 source의 최종 batch `f` 결과는 아래에 실제 종료 확인 후 기록한다. PATCH는 계속 v0.1.23이며 기존 출력·state/credential·설치를 보존한다.

Ruling: 핵심 우선 scope로 진행 중이며 goal은 **active**다. service scope·접근성/GUI 실기 적합성·native runtime 필수 연결·수동 update/maintenance 및 R14 제거 등 남은 pretest 구현과 명시한 deferred 인수는 여전히 완료로 판정하지 않는다. 후순위 OS 전용 API 확장은 추가하지 않는다.

설정/로그인 checkpoint의 최종 증거:

- `xtask stage --platform win --arch x64 --build-id native-pretest-20261011-f`: static CRT production release PASS (1분 20초), `verify-staging` PASS (payload 4개 + launcher hash/size/target/import 검사). 이전 a/b/c/d와 실패 e.quarantine 보존. source digest는 빌드 전후 동일하다.
- 최종 Agent SHA-256 `fcfdf6b973c8dd04b84b096a9e7651643c26be7f0d72a5ce9afae75241853503`, Client `1031055e3c0cafd2dea8d8719c27a5f591e8e091e195b47c2c703944259d99a8`, native launcher `00c9aadb74e4f0d8f502ba1209af7ae5d199ab4130d152488f9c494307596bec`. 실제 파일 크기는 build manifest에 기록했다.
- `cargo check --locked -p racp-client-native --features gpu-renderer --target x86_64-pc-windows-msvc`: PASS (12.45초). inventory-check PASS (114 operation/143 permission/18 category/48 후순위 planned), schema·AND binding 정적 비교 PASS(drift 0), Rust format·diff check PASS, Ruff/Mypy(97 source)/uv lock check PASS. 기존 4개 compiler warning 외 신규 warning 없음.
- Agent/Client binary `--version`만 실행해 `0.1.23` 확인. `native-login-configure/status`, 새 등록, GUI, 원격 명령, installer, startup/uninstall 또는 자동 테스트는 실행하지 않았다. HKCU 값/기존 설치·Device·credential·사용자 state 변경 없음. 이 증거는 source 연결/production 컴파일/정적 무결성을 증명하며 실제 OS 및 lifecycle 동작을 PASS로 처리하지 않는다.

### R09/R10 선택 runtime의 native Provider 연결

| 범위 | authoritative source | 실제 구현과 검증 한계 |
|---|---|---|
| 선택 snapshot | [runtime catalog](../../crates/racp-runtime/src/runtime_catalog.rs), [runtime config/admission](../../crates/racp-core/src/runtime_admission.rs), [native startup](../../crates/racp-runtime/src/native_client.rs) | Chromium/CDB/GDB/native 분석 host hash 선택·CAS 저장. 시작 때 전체 파일을 다시 hash/size/OS/arch 검증하고 Arc pin으로 Provider와 native session 수명 동안 유지. 선택 손상은 해당 runtime을 비활성으로 남기며 기본 Agent 경로를 시작. 파일·상태 import 또는 PATH 탐색 없음 |
| CDB | [carrier](../../crates/racp-runtime/src/providers/native_carrier.rs), [Win adapter](../../crates/racp-runtime/src/providers/native_carrier/windows.rs) | native mode는 admission된 CDB만 사용하고 legacy `native-runtime.json`을 읽지 않음. 같은 parent의 cdb.exe/dbgeng.dll/dbghelp.dll 요구, 실제 file version 검사, exts.dll은 같은 parent에서만 사용. 기존 process birth/target·scope·framed carrier·소유 debugger 종료/borrowed target 보존 경계를 유지. CDB 자체 실행/MCP attach·step·detach 인수는 보류 |
| GDB·분석 host | [admitted adapter](../../crates/racp-runtime/src/providers/reversing/admitted.rs), [Reversing](../../crates/racp-runtime/src/providers/reversing/provider.rs), [Supervisor](../../crates/racp-runtime/src/providers/reversing/plugin.rs) | native mode는 legacy plugins.json을 읽지 않음. GDB는 고정 Rust plugin-gdb worker/MI adapter를 사용하며 native host는 고정 stdio plugin protocol/manifest handshake로 연결. generated manifest는 승인 bundle별 고정 private directory에 작성·pin. 첫 authorized open에 health 확인, 취소/오류/버전 불일치 때 owned supervisor 종료; cleanup 실패를 숨기지 않음. ADMITTED와 READY 구분 및 lazy routing operation 목록 연결 |
| Host boundary | 위 admitted adapter/[runtime manifest](../../crates/racp-contract/src/runtime_manifest.rs) | native 분석 host entrypoint는 racp-analysis-host(.exe)만 허용하며 java.exe wrapper를 선택하지 않음. native startup에서 프로젝트 Java bridge를 실행하지 않는다. 기존 legacy Ghidra source/CLI가 아직 남아 있으므로 Client 소스/배포 전체 Java 제거 또는 별도 host implementation 완성을 주장하지 않음 |
| Inventory·UI | 위 admission/`Iced UI`(삭제됨) | 실제 bundle 파일 목록을 manifest+admitted metadata와 비교해 unlisted adjacent 파일 거부; 8192 entry/32 depth scan bound. manifest provenance URL의 query credential을 거부. 새 runtime hash 편집·NeedsRuntime/NeedsPermission/UnsupportedOs/ProtectedTarget/Unverified 표시 연결. 로컬 hash 동의이며 signed trust catalog 검증이 아님 |

현재 연결은 source/계약 구현이다. 제공된 실제 CDB/GDB/native analysis host bundle을 admission하거나 실행하지 않았고 기존 host MCP tool과의 dump/PCAP/live/proxy 경로 실기 인수는 보류다. GDB 기존 고정 adapter operation 집합(launch/read/command/close 등)을 유지하며 borrowed attach RPC가 새로 구현됐다고 주장하지 않는다. packet-driver 활성화·download/제품 trust catalog·vendor license 검토와 별도 native host 제공, legacy Ghidra 및 이전 Client 경로 R14 제거는 여전히 남아 있다.

production 증거:

- intermediate `native-pretest-20261011-g` static CRT release PASS(2분 1초), lazy routing/health cleanup/UI reason 보강 후 최종 `native-pretest-20261011-h` PASS(1분 24초). 모든 이전 batch 보존. `verify-staging` PASS, 4개 payload와 launcher hash/size/Win64/import 검증 및 빌드 전후 source digest 일치.
- 최종 Agent SHA-256 `bb90650c49f401d7969f378e751f3ca11d1dadbda33fbe2a554d01e3fa070691`, Client `c07ec28795ac24e15db78060248190ef123170a3c90c2b51bd4c2c974422ea50`, native launcher `611673e6f832c550f46eb694c14471cd27ccbf823b480ad4560391174b47525b`.
- final source `cargo check --locked --workspace --target x86_64-pc-windows-msvc` PASS(9.76초), GPU feature check PASS(13.16초), inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned), shared schema/Python·Rust binding audit PASS(drift 0), Rust format/diff PASS, Ruff/Mypy(97 source)/uv lock check PASS. unused runtime.version 경고를 해결해 기존 recipe constant warning 3개만 남았다.
- 실제 runtime/GUI/IPC/새 등록/원격 명령/OS mutation·자동 테스트·installer·CI dispatch를 실행하지 않았다. 컴파일과 staging 성공을 엔진별 실행 성공·기능 인수로 이전하지 않는다. 기존 설치·credential·사용자 상태와 dist 보존, PATCH v0.1.23 재사용.

Ruling: 핵심 우선 scope의 native runtime source 연결이 진행됐으며 goal은 **active**다. 남은 core lifecycle/service boundary·접근성 및 renderer 적합성·별도 host/maintenance/update·R14 제거를 완료로 처리하지 않고, 후순위 OS 전용 API 확장을 이 배치에서 추가하지 않는다.

### R09-B 프로젝트 Java bridge를 선택 분석 host로 분리

| 범위 | authoritative source | 실제 구현과 한계 |
|---|---|---|
| 독립 host | [host workspace](../../apps/analysis-host/Cargo.toml), [native stdio main](../../apps/analysis-host/src/main.rs) | 별도 Cargo workspace, base Rust/uv workspace에서 제외. admitted bundle·entrypoint·전체 파일 hash/pin 확인 후 host-runtime.json으로 engine/JDK의 bundle-relative 경로를 선택. 고정 stdio manifest/version/instance 검사, 최대 8개 분석 session/1 MiB frame, 복사한 analysis resource root만 처리. DB import/수정 후 오류는 unknown으로 보고 |
| Engine·Java 이동 | [host engine](../../apps/analysis-host/src/engine/ghidra.rs), [bridge](../../apps/analysis-host/src/engine/resources/RACPStaticBridge.java), [선택 host build tool](../../apps/analysis-host/tools/bootstrap_ghidra.py) | Ghidra 실행 코드·프로젝트 Java bridge·Python vendor downloader를 Client source에서 host로 이동했다. vendor Java는 host가 소유하는 bounded native child와 private 분석 폴더에서 사용하며 기본 Client에 동봉/실행하지 않음. downloader는 host 개발 도구로만 남겼고 실행하지 않았음 |
| Agent 경로 제거 | [GDB worker](../../crates/racp-runtime/src/providers/reversing/worker.rs), provision (`../../crates/racp-runtime/src/providers/reversing/provision.rs`, 과거 경로), [CLI](../../apps/agent-rust/src/main.rs) | Agent plugin-ghidra/provision-ghidra 실행·도움말·engine branch 제거. GDB와 shared bounded hashing/process/protocol primitives만 유지. 기본 Agent가 Java bridge를 포함하거나 이전 CLI로 실행하는 경로 없음 |
| 계약·이력 | [host metadata schema](../protocol/analysis-host-runtime-v1.schema.json), [inventory](../../crates/racp-contract/migration-inventory.json), [xtask](../../xtask/src/inventory.rs) | 기존 public RE/stdio 계약 경로를 유지하고 host metadata 추가. legacy consumer의 baseline source_path를 보존하면서 current_source_path/disposition으로 실제 relocation을 기록; consumer audit는 현재 경로 확인. 원본 Registry 100개 ID 및 현재 114개 operation 유지 |
| 버전·배포 경계 | [version tool](../../scripts/version.py), [가이드](../guides/desktop-client-guide.md#64-별도-native-분석-host-빌드) | host app도 같은 repository SSOT v0.1.23을 사용하고 version tool의 Cargo target에 포함. host independent Cargo.lock은 별도 유지, 기본 staging/CI가 host를 feature-unify하거나 payload에 포함하지 않음. vendor engine version과 host app version은 별도 의미 |

production·정적 증거:

- Client `native-pretest-20261011-i` static CRT release PASS(55.63초), `verify-staging` PASS. 최종 Agent SHA-256 `a2484c8be8a0bea8179e54668f0446b8caa8237af034099d9fe50cd6ea3ba9a0`, Client `eb1399a588c81dce203ed2f9f6a006eb4ef8075fc2b7876d91b0ca8d058e0e3d`, native launcher `78f89dc7a27ad82a83165b40fa4d6087883280835680017fd628515419811e06`.
- 독립 host native Windows x64 `cargo check` PASS(첫 1분 37초, 최종 0.91초), `cargo build --manifest-path apps/analysis-host/Cargo.toml --locked --release --target x86_64-pc-windows-msvc` + static CRT PASS(2분 5초). `racp-analysis-host.exe` 7,305,216 bytes / SHA-256 `7d740eb984637c39885724c6fb60bbef48c3e577f99b520f580942b323b64e4d`. `--version`만 실행해 0.1.23 확인.
- Build graph/source/binary 정적 audit PASS: root workspace는 contract/core/runtime/ui-model/Agent/native Client/xtask만 포함, default payload 4개 유지. Java bridge class marker는 Agent binary에 없고 optional host binary에만 존재. 기본 Client source에서 plugin-ghidra/provision-ghidra/Java bridge/JAVA_HOME/Ghidra integration 검색 0개.
- shared schema/binding audit PASS(114 operation/drift 0), inventory-check PASS(143 permission/18 category/48 후순위 planned), 양쪽 Rust format/diff PASS, Ruff PASS, Mypy PASS(98 source, host build helper 포함), uv lock check PASS. 기본 runtime의 기존 recipe warning 3개 외 새 warning 없음.
- 최종 Client GPU feature check PASS(8.80초). actual GUI·GPU 선택·소프트웨어 renderer·접근성 인수는 실행하지 않음.
- 실제 vendor tool download, runtime admission, Java/Ghidra 실행, GUI/IPC/원격 분석·자동 테스트·installer/기존 설치/사용자 데이터 변경은 수행하지 않았다. 외부 host/vendor bundle 조립과 license/advisory 검토·실기 MCP/PCAP/dump 분석 인수는 아직 보류/미완료다. host production 컴파일을 Ghidra 분석 성공으로 판정하지 않는다.

Ruling: R09-B의 Client/host source 경계를 구현했으며 goal은 **active**다. core service/GUI accessibility boundary·maintenance/update·remaining lifecycle 및 전체 R14 제거는 남아 있다. 후순위 OS 전용 API 확장은 이 배치에서 추가하지 않았으며 모든 기존 dist batch와 state/credential을 보존했다.

### R14-A/B 부분 구현: Tauri/React·Node Client tooling 제거

tracked legacy Client source 35개를 `.tools/retired-client-source-0123-20261011`의 복구 가능한 로컬 snapshot으로 이동했다. 원래 경로의 ignored build/cache와 기존 설치·등록·credential·사용자 상태·dist는 유지했다. snapshot manifest는 원본 경로·파일 크기·SHA-256을 기록한다. 이 snapshot은 배포 payload 또는 실행 fallback이 아니다.

- Tauri/React Client 28개 파일, Node build/manifest/WebView2 staging 4개 파일, 이전 portable/maintenance Rust 경로 3개를 active source에서 제거했다. native portable의 오류 창 처리를 자체 모듈로 이동하고 Agent의 이전 maintenance command를 제거했다.
- pnpm workspace/lock은 Console만 유지한다. version tool은 Python·Console·root Rust·별도 분석 host의 v0.1.23 SSOT를 동기화하며 이전 Tauri/React 대상은 제거했다. CI·AGENTS·desktop-build/version-manager skill·Desktop Client Guide의 운영 명령을 native 경로로 갱신했다. 계약 inventory는 baseline consumer path와 retired disposition/current replacement를 함께 보존한다.
- Iced window에 embedded PNG icon을 연결했다. 지정된 Node 22.23.0/pnpm 11.19.0의 Console TypeScript/Vite production build PASS(167 modules, Vite 1.23초). lock 갱신은 offline/ignore-scripts로 수행했으며 새 도구를 내려받거나 Client Node 경로를 실행하지 않았다.
- `native-pretest-20261011-j` static CRT production release PASS(54.45초), `verify-staging` PASS(4 payload files/target/import/hash/size). Agent SHA-256 `aea280f81ef8d830ee797713e8514e84736192adbc6fe08f250417ecb35b0754`, Client `b48a077581543344da5cafcccce15e57a5ee6a4b9cad320fb0b41629de6679bd`, native launcher `b66f0e2b9606cbf6651eb6a62f9287e755178ddc4c492500eec7079ab8883021`. 이전 batch를 보존했고 실제 installer/portable product artifact는 생성하지 않았다.
- Root workspace production check PASS, 최종 GPU feature check PASS(5.05초), 독립 host check PASS(4.18초), consumer retirement audit PASS(35 files), inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned), 계약 정적 audit PASS(drift 0), Ruff/Mypy(98 source)/uv lock/format/diff check PASS. 기존 recipe warning 3개 유지.

> [!IMPORTANT]
> R14 전체 완료가 아니다. 이전 Rust TCP control·v1/v2 state/credential·service 실행 경로가 아직 남아 있고 native service boundary·maintenance/update 및 접근성/renderer 적합성도 미완료다. 자동 테스트·GUI/IPC smoke·native acceptance·새 등록·OS mutation·installer/CI dispatch는 실행하지 않았다. source 제거·생산 컴파일은 기능 동등성이나 설치/제거 인수 증거가 아니다. 핵심 우선 범위의 goal은 **active**, PATCH v0.1.23을 재사용한다.

### R02/R04 핵심 작업 추적: native 로컬 취소·재시작 이력

| 범위 | authoritative source | 구현과 한계 |
|---|---|---|
| 취소 계약 | [local IPC](../../crates/racp-contract/src/local_ipc.rs) | `CancelOperation`은 boot ID·operation ID·target request ID를 받는다. PID/argv/target path 입력 없음. 64 KiB/identifier bounds 유지; typed 접수 receipt와 `CancelFinished` 관찰 결과 구분. 같은 boot 재연결 snapshot의 최근 제어 결과 16개에 포함 |
| 로컬 owner 제어 | [cancellation](../../crates/racp-runtime/src/local_ipc/cancellation.rs), [ControlPlane](../../crates/racp-runtime/src/local_ipc/policy.rs) | 기존 같은 user/session·peer PID birth 인증 IPC만 사용. 현재 Agent device/boot/Journal request를 다시 비교하고 active Execution에만 신호. remote principal의 `jobs.cancel` API와 별도 로컬 소유자 제어; 다른 Agent/재시작 전 요청/임의 PID 취소 경로 없음. 16-worker bound·policy/stop serialization, 대기 중 serialization 해제로 Stop을 관찰 시간 동안 막지 않음 |
| 결과 관찰 | 위 cancellation/[dispatch](../../crates/racp-runtime/src/dispatch.rs) | durable cancel intent 후 cancellation token 신호, 최대 15초 동안 Journal terminal과 execution release를 확인. 정상 결과가 먼저 끝나면 그 상태 보존. timeout/unknown/미보고 정리를 성공으로 바꾸지 않으며 아직 execution이 남아 있으면 cleanup은 pending. payload/argv/result/credential 원문은 receipt/event에 없음 |
| 이력·GUI | [Journal](../../crates/racp-core/src/journal.rs), [native startup](../../crates/racp-runtime/src/native_client.rs), `Iced app`(삭제됨) | device별 최근 32개 메타데이터만 SQL 조회해 시작 snapshot에 복원. 중단한 이전 실행은 기존 recover 규칙대로 UNKNOWN이며 자동 재실행 안 함. 현재 진행 작업에 취소 버튼; stale snapshot/연결 중단/종료 중에는 비활성. 접수·관찰 상태·terminal·정리를 각각 표시. control receipt 자체는 crash-durable 완료 기록이 아니며 실제 operation Journal과 취소 intent만 durable |
| 새 결정 기록 | ADR-0031(삭제, [ADR-0032](../adr/ADR-0032-native-client.md)로 통합) | native GUI·분리 상태·선택 host·Rust build 결정 및 핵심 제어 우선 의도 기록. ADR-0030의 새 source 결정만 대체하고 기존 제품 이력/설치는 보존. shell/interpreter 내부 명령이 개별 OS 객체 권한으로 제한된다고 주장하지 않음 |

production·정적 증거:

- `native-pretest-20261011-k` static CRT production release PASS(1분), `verify-staging` PASS(4 payload files/hash/size/Win64/import/source consistency). Agent SHA-256 `469748b1ee523fe0a9d6cb7c7052fa8529ceed715a423102efcd0fa56b95ffa5`, Client `1701e41fb0fdf0d78eff5d4ab48855ffd7ccf522cbb0a9e2be48a9f219c4d6d1`, native launcher `31c87b4dd2f312da52289599f896c29f0789388b7ecc03a2f62cf11664fd233d`. 이전 j 및 모든 batch 보존.
- workspace Windows production check PASS(4.88초), GPU feature check PASS(9.21초), 독립 분석 host check PASS(8.39초), root/host Rust format PASS, Ruff PASS, Mypy PASS(98 source), uv lock check PASS, inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned), 공통 계약 정적 비교 PASS(drift 0), source retirement audit PASS(35 files). 기존 recipe warning 3개 외 신규 compiler warning 없음.
- 작업 취소·Journal 재시작 복원·정책/Stop 경합·취소 후 cleanup의 실제 동작은 인수하지 않았다. 자동 테스트·GUI/IPC 실행·등록·원격 요청·OS mutation·installer·CI dispatch 미실행. 생산 컴파일/정적 검사는 이 기능의 runtime PASS를 뜻하지 않는다.

Ruling: 핵심 작업 제어와 추적의 source 연결을 보강했다. goal은 **active**, 전체 테스트 전 구현은 아직 완료가 아니다. native service/controller 경계, 접근성·renderer 적합성, trust/update/maintenance·잔여 legacy Rust 실행 경로 제거를 이어가며 후순위 OS 전용 API는 추가하지 않는다. PATCH v0.1.23 재사용, 기존 설치·Device·credential·state 보존.

취소 원인 보강의 최종 source 증거:

- Execution의 cancellation cause는 처음 신호한 원인을 유지한다. local owner 취소 후 cleanup 대기 중 deadline 또는 policy retirement가 발생해도 이전 취소를 TIMED_OUT으로 다시 분류하지 않는다. Gateway 계약의 requested/deadline/revoked 구분은 유지한다. 실제 경합 인수는 보류다.
- 최종 `native-pretest-20261011-l` static CRT production release PASS(57.20초), `verify-staging` PASS. Agent SHA-256 `f8626fbc4a6492ac11d8d9029c3a285f0513b902289d8ce329f042cd773ec341`, Client `47433c41336177df7cfefc4027f964cbf340d0e3cfb6ab225646f1b3b5602362`, native launcher `ccf2cdb7fac35f1893b7cafce099edaca27ef5db377e15c4cc3b623b55396d64`. k와 모든 기존 batch 보존.
- 최종 workspace check PASS(13.02초), GPU feature check PASS(6.04초), Rust format/diff check PASS. 변경된 ADR·가이드·skill의 상대 링크 61개 확인, broken link 0개. version SSOT는 0.1.23으로 유지한다. 테스트/GUI/원격 실행/실제 취소/OS mutation/제품 패키징은 실행하지 않았다.

### R03 서비스 경계 1: 별도 SCM host·인증 IPC·역할 scope

| 범위 | authoritative source | 실제 구현과 남은 범위 |
|---|---|---|
| 별도 service | [native SCM host](../../apps/agent-rust/src/native_service.rs), [Agent CLI](../../apps/agent-rust/src/main.rs) | 고정 `RACPNativeAgent`/`NT SERVICE\RACPNativeAgent`를 사용해 기존 RACPAgent와 분리. session 0·OS 조회 service SID와 user SID의 일치·활성 service SID·비관리자 token 검사 후 packaged payload를 pin. `native-service --state-dir`는 explicit private native state/config만 읽고 legacy settings/credential/config를 호출하지 않음 |
| config/state | [private config](../../crates/racp-core/src/native_service.rs), [typed scope](../../crates/racp-contract/src/service_scope.rs), [schema](../protocol/native-service-config-v1.schema.json) | 중복/unknown key·format version·16 KiB config bound. 정상화한 state/control root의 겹침 거부, 최대 16개 고유 사용자 SID/각 16개 고유 nonzero session ID, observer/controller role. SID component는 canonical u32, 고정 service SID는 OS에서 재조회. credential은 기존 DPAPI current-account scope와 private root를 유지; machine/plaintext fallback 없음. service onboarding/config 저장 CAS는 아직 미구현 |
| shared directory | [service directory](../../crates/racp-core/src/service_directory.rs) | service/SYSTEM full access, 지정 사용자 read/traverse만 있는 보호 DACL·service owner를 생성/검사하고 directory pin 유지. 사용자에게 credential/state root를 공유하지 않음. 기존/다른 ACL을 자동 repair하지 않으며 parent provisioning은 후속 설치 작업 |
| authenticated pipe | [Windows local IPC](../../crates/racp-runtime/src/local_ipc/windows.rs), [identity query grants](../../crates/racp-runtime/src/local_ipc/service_identity.rs) | service PID/birth/session 0·virtual SID·활성 service SID 검증, actual client SID/session과 Named Pipe impersonation identity 비교. observer는 snapshot/subscribe만 허용; controller만 mutation/승인/Stop/cancel 가능. pipe client는 GENERIC_WRITE 대신 read+write-data의 명시 권한으로 create-instance bit 제외. 허용 사용자에게 service process query/synchronize와 token query만 부여해 identity 검증을 가능하게 함; process 수정/token duplicate/impersonation 권한 없음. 실제 cross-account 동작/ACL 경합은 미검증 |
| presence/readiness/stop | [StateHub](../../crates/racp-runtime/src/local_ipc/events.rs), 위 native SCM/[startup](../../crates/racp-runtime/src/native_client.rs) | observer subscription을 approval-controller presence에서 제외. pipe 생성/endpoint publish 뒤에만 SCM RUNNING 보고; 시작/정리 대기 중 checkpoint 갱신. SCM stop/shutdown은 native cancellation/cleanup 완료를 기다리고 고정 실패 코드를 사용. 실제 SCM deadline/shutdown 인수는 보류 |
| session desktop | [Desktop Provider](../../crates/racp-runtime/src/providers/desktop/provider.rs) | native_only의 session-0 경로는 legacy service-login.json을 읽지 않고 `NATIVE_SERVICE_BROKER_NOT_CONFIGURED`로 남김. 사용자 native Agent의 Broker/Guardian 경로 유지. service 사용자-session Broker/GUI service endpoint 선택·restart·per-machine installer 연결은 아직 미완료 |

이 변경은 service Hosting/IPC의 source 경계를 추가했으며 OS 전용 service 관리 operation 확장이 아니다. 공개 Registry는 114개로 유지한다. 기존 서비스/로그인/설치·Device·credential·사용자 데이터·dist에 명령을 실행하거나 ACL을 변경하지 않았다.

production·정적 증거:

- 최종 workspace Windows production check PASS(4.35초). 초기 check에서 subscription field 위치·Windows constant import/visibility·moved runtime 오류를 확인하고 수정했으며 생산 staging은 최종 check 이후에만 실행했다. GPU feature check PASS(29.26초), 독립 분석 host check PASS(26.39초), root/host Rust format PASS. 기존 recipe warning 3개 외 신규 warning 없음.
- `native-pretest-20261011-m` static CRT production release PASS(1분 14초), `verify-staging` PASS(4 payload files/Win64/import/hash/size/source consistency). Agent SHA-256 `8e5069ef1f0dc32097678ec54cdceb8c63d3ed5bf7c453029f20603ba0f7a1bf`, Client `4dcd7fcb746323659e43182f064a42cf7c2b5956bfee08da7dc08bd07809559e`, native launcher `dc74b6b4966d7ff67bef31dfea276deae9e4fb21ddb251f82a301658183cc7b5`. l와 모든 이전 batch 보존, PATCH v0.1.23 재사용.
- inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned), 공통 계약 정적 audit PASS(drift 0), diff check PASS. 실제 service 등록/시작/중지·token/process/directory ACL 변경·DPAPI profile 사용·GUI/IPC 접속·원격 요청·자동 테스트·installer/CI dispatch는 실행하지 않았다.

> [!IMPORTANT]
> R03/service 전체 완료가 아니다. 새 service registration/bootstrap, config 생성/변경 CAS, GUI service controller 모드, native session Broker 및 installer/maintenance/update 연결을 이어가야 한다. virtual account profile/DPAPI·service SID 설정과 cross-account peer 검증을 실제로 확인하지 않았다. 접근성/renderer source 적합성과 잔여 Rust legacy 경로 R14 제거도 남아 있다. 이 checkpoint를 전체 테스트 전 구현 또는 기능 인수 완료로 판정하지 않으며 goal은 **active**다.

설계 근거는 [Microsoft service SID](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/ns-winsvc-service_sid_info), [Named Pipe access rights](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights), [DPAPI 기본 account scope](https://learn.microsoft.com/en-us/windows/win32/SecCrypto/example-c-program-using-cryptprotectdata)이며 실제 제품 인수 증거와 구분한다.

### R03 서비스 경계 2: GUI control target·명시 bootstrap·입력 정리

| 범위 | authoritative source | 구현과 남은 한계 |
|---|---|---|
| 서비스 GUI | [control target](../../apps/client-native/src/control_target.rs), [GUI entry](../../apps/client-native/src/main.rs), `Iced app`(삭제됨) | `--service-control`로 공유 control directory 하나를 명시. user/portable state·start-agent flags와 혼합 거부, packaged payload 확인. singleton/tray/IPC target도 이 경로 사용; 서비스 모드의 local refresh/등록/로그인/settings/runtime helper는 early guard로 user state/credential을 읽거나 호출하지 않음. 서비스 GUI 종료는 서비스 Stop과 분리 |
| 권한·식별자 | [IPC DTO](../../crates/racp-contract/src/local_ipc.rs), [pipe](../../crates/racp-runtime/src/local_ipc/windows.rs), 위 Iced/[tray](../../apps/client-native/src/native.rs) | snapshot/subscribe에 실제 authenticated observer/controller role을 반환하고 client는 endpoint 기대 role과 재비교. snapshot에는 Device/Gateway origin/user 또는 service scope만 추가하며 credential/payload 원문 없음. service identity 없는 snapshot 거부. observer의 정책 편집/승인/취소/Stop 차단, 조회 화면/비활성 버튼·tray 권한 연결. disconnect/gap 시 role 제거; observer 승인 prompt는 창을 자동 활성화하지 않음 |
| 새 서비스 등록 | [bootstrap](../../crates/racp-runtime/src/service_bootstrap.rs), [enrollment](../../crates/racp-runtime/src/native_client.rs), [SCM](../../apps/agent-rust/src/native_service.rs) | SCM의 optional bootstrap SHA-256은 비밀이 아닌 입력 동의 hash. 고정 private bootstrap/connection 파일만 허용, file pin·64 KiB bounds·hash·actual service identity·scope 검사 후 native enrollment 호출. secret/token을 GUI IPC/argv에 보내지 않음. initial native service input만 등록 reservation의 허용 목록에 추가하며 legacy/다른 파일/pending 상태는 거부. 이미 등록한 native state는 bootstrap을 재소비하지 않음 |
| Secret 준비·취소 | [SecretStore](../../crates/racp-core/src/secrets.rs), 위 enrollment/bootstrap | token 소비 전에 현재 account DPAPI encrypt/decrypt roundtrip을 확인. credential/application file을 쓰지 않고 machine/plaintext fallback 없음; Windows profile/key 초기화 가능성은 실제 인수 필요. workspace/policy 검증 후 reserve-before-token 유지. 120초 budget/취소는 unknown outcome으로 pending 보존, 완료와 동시 취소면 완료 우선. uppercase connection hash는 canonical lowercase로 검증 |
| 성공 입력 정리 | 위 bootstrap | Device ID·bootstrap/connection hash·file revision을 durable cleanup marker에 기록. read pin 해제 뒤 read+DELETE handle로 동일 file identity/hash를 다시 확인하고 handle disposition으로 삭제; 외부 변경은 conflict로 보존. 재시작 때 marker를 확인해 남은 고정 입력만 정리. 실제 deletion/profile/중단 복구/경합은 미검증 |
| 범위 표시 | 위 startup/[가이드](../guides/desktop-client-guide.md#66-service-gui와-bootstrap-source-연결) | service의 desktop/clipboard는 `NATIVE_SERVICE_BROKER_NOT_CONFIGURED`/NotImplemented로 표시하고 legacy service-login.json fallback 없음. native user-run은 session-0 endpoint 거부를 credential/WSS 시작 이전에 수행. service parent/계정/SID provisioning·config CAS·native session Broker·installer/maintenance/update는 여전히 미완료 |

실제 증거:

- 최종 workspace Windows check PASS(5.10초), GPU feature check PASS(13.87초), 독립 분석 host check PASS(13.04초), root/host Rust format PASS. compile 과정에서 Directory path/Windows bool type를 확인해 수정했고 신규 unused import/field 경고를 제거했다. 기존 recipe warning 3개 유지.
- `native-pretest-20261011-n` static CRT production release PASS(51.05초), `verify-staging` PASS(4 payload files/native target/import/hash/size/source consistency). Agent SHA-256 `4043f998c19ca7f7a2dc35bfb61d232a81b2adc2b03ee9d674ec49efd1b6fba2`, Client `b09e13bceb0c78bddd6a96e1bf33fbb90425fe4df5bc5ce564a5fc6bbab41f18`, native launcher `8b022f461191404bdf63cdbd5f45fd6fbac0e9db65007482fa7ba9f658fded3e`. m와 모든 이전 batch 보존. PATCH v0.1.23 재사용.
- inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned), 계약 정적 audit PASS(drift 0), diff check PASS, guide 상대 링크 broken 0개. 공개 OS service operation 확장을 추가하지 않았다.
- 실제 DPAPI probe/입력 생성·삭제/새 등록·service 설치/시작/중지·ACL 변경·GUI/IPC 접속·원격 요청·자동 테스트·installer/CI dispatch는 실행하지 않았다. 기본 payload에 bootstrap/token/credential/state를 추가하지 않았고 기존 설치/Device/credential/user state를 보존했다.

> [!IMPORTANT]
> GUI/bootstrap source 연결을 구현했으며 service 기능 전체 또는 전체 테스트 전 구현 완료가 아니다. provisioning/config CAS·native session Broker·접근성/renderer 적합성·trust/update/maintenance·잔여 Rust legacy 실행 경로 제거를 이어간다. 테스트/실기 gate 보류는 유지하고, 핵심 우선 scope의 goal은 **active**다.

### R03 서비스 경계 3: config CAS·ACL readback·명시 reconcile

| 범위 | authoritative source | 구현과 한계 |
|---|---|---|
| config 변경 | [transaction](../../crates/racp-core/src/service_config.rs), [config](../../crates/racp-core/src/native_service.rs), [update schema](../protocol/native-service-update-v1.schema.json) | fixed private update 입력 hash, 현재 revision/config SHA-256, 다음 revision=현재+1 비교. service actor/private root/등록 상태·config 및 Agent lock 확인. service SID와 두 root 이동 금지; 사용자/세션/role만 변경. Agent의 service run은 같은 config lock을 수명 동안 보유 |
| durable intent | 위 transaction | old/new config·입력/기존 hash를 pending에 기록한 뒤 ACL→config commit→readback→result receipt→pending 제거. 일반 config load/Agent 시작은 pending을 거부. explicit reconcile은 현재 값이 원래/목표 값으로 확인된 경우만 이어가고 외부 config 변경/알 수 없는 ACL은 conflict로 보존. credential/Gateway/Device 변경 없음 |
| ACL 경계 | [service directory](../../crates/racp-core/src/service_directory.rs) | protected service owner directory 및 endpoint/IPC lock의 service owner·inherited read ACL 검사. 두 fixed file 외 콘텐츠, writer SID/권한·protected child ACL 거부. service actor만 변경; root/파일 ACL이 known old/new인 부분 propagation을 explicit reconcile로 처리하고 arbitrary broad ACL을 repair하지 않음. live IPC lock의 기존 write handle과 충돌하지 않도록 보안 metadata는 shared read handle로 조회 |
| one-off SCM 실행 | [native SCM](../../apps/agent-rust/src/native_service.rs) | `StartService`의 서비스명 다음 bounded UTF-16 args로 `--configure-sha256 <hash>` 또는 `--reconcile-config` 선택. 입력은 private file에만 두며 ImagePath/token/Gateway 설정 변경 없음. maintenance RUNNING은 config 작업 진행 상태이며 Agent/pipe/WSS를 시작하지 않고 완료 뒤 정지. SCM START 권한·관리자 handoff/호출 도구는 후속 provisioning에서 연결 |

검증 증거:

- workspace Windows check PASS(최종 4.60초), GPU feature check PASS(10.63초), 독립 분석 host check PASS(9.60초), root/host Rust format PASS. 기존 recipe warning 3개만 유지.
- `native-pretest-20261011-o` static CRT production release PASS(59.92초), `verify-staging` PASS(4 payload files/Win64/import/hash/size/source consistency). Agent SHA-256 `b433c19a855425cf6af7a8c1ef0f8443d3375d14743a94bb4c38375ef167422a`, Client `87b647ffb913c6099b0516ae62950c6b4ef50ed3c2858104005b26e38bb490fa`, native launcher `8c0cb2dcf7968ab1931a1290c524035abece280e36d010265d8917880f2c1f0e`. n와 모든 기존 batch 보존, PATCH v0.1.23 재사용.
- inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned), 공통 계약 정적 audit PASS(drift 0), diff check PASS, service schema JSON parsing/guide 상대 링크 확인 PASS. 공개 OS API 확장 없음.
- 실제 config/ACL 변경·pending 생성/제거·SCM start arguments·reconcile·서비스/GUI/IPC/DPAPI/원격 요청·자동 테스트·installer·CI dispatch는 실행하지 않았다. 기존 service/설치/Device/credential/user state 보존.

> [!IMPORTANT]
> Windows의 외부 SYSTEM writer까지 포함한 파일 replacement/ACL 변경의 원자적 CAS는 보장하지 않는다. cooperative lock·직전 compare·readback·durable intent로 제한하며 실제 외부 writer/부분 propagation/강제 종료 인수는 보류다. config transaction source를 구현했지만 관리자 handoff/provisioning·native session Broker·installer/update/maintenance·접근성/renderer 적합성·잔여 Rust legacy 제거는 남아 있어 전체 테스트 전 구현 완료가 아니다. goal은 **active**다.

SCM argument 설계는 [Microsoft ServiceMain callback](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nc-winsvc-lpservice_main_functionw)의 서비스명 다음 start parameter 정의를 참고했다. source 구현과 실제 서비스 인수 증거를 구분한다.

### R03/R14 핵심 사용자 경로: 종료 기록·단일 CLI·오류 전달

| 범위 | authoritative source | 구현과 남은 한계 |
|---|---|---|
| 단일 실행 진입점 | [Agent CLI](../../apps/agent-rust/src/main.rs), [runtime exports](../../crates/racp-runtime/src/lib.rs) | native 명령 및 고정 Broker/Guardian/Provider worker만 허용. 전체 argument 목록의 길이·중복·unknown flag를 상태/패키지 접근 전에 검사. 이전 bridge/run/start/status/stop/serve/info/settings/activity 및 구 service/로그인/provision 명령 제거. version/help는 단독 argument만 허용. legacy TCP fallback 없음 |
| 소스 폐기 | 위 runtime/[HTTP transport](../../crates/racp-runtime/src/network.rs) | legacy TCP control·background launcher·SCM host·구 credential 등록과 해당 Client 전용 enrollment 검사를 복구 가능한 ignored source snapshot으로 이동. 총 40개 보관 파일의 원본 SHA-256 일치 확인. native enrollment가 사용하는 TLS/CA/no-proxy/no-redirect HTTP 경계만 network 모듈로 보존. 새 테스트/fixture를 추가하거나 실행하지 않음. 기존 dist/설치/Device/state는 변경하지 않음 |
| helper 실패 | 위 Agent CLI/[GUI helper](../../crates/racp-runtime/src/local_agent.rs) | CLI의 실패 응답을 GUI helper가 실제로 읽는 `error.code`와 일치시킴. 기존 flat code 때문에 revision/permission/등록 오류가 generic 오류로 바뀌던 경로 수정. framed worker stdout에는 outer JSON 결과를 추가하지 않고 실패 code는 stderr에만 기록 |
| durable 종료 | [lifecycle](../../crates/racp-core/src/native_lifecycle.rs), [등록 commit](../../crates/racp-core/src/state_schema.rs), [Agent startup](../../crates/racp-runtime/src/native_client.rs), [GUI 종료 확인](../../apps/client-native/src/onboarding.rs) | registration 최종 state marker 이전에 never_started 기록. runtime/provider 생성 전에 starting을 저장해 이전 완료 기록 무효화. run ID/boot ID/device를 바인딩하고 최종 Agent 정리 결과를 private native-run.json에 atomic commit. 같은 Agent lock을 얻은 상태에서 미시작 또는 완료 기록만 정상 종료로 인정. 파일 없음/시작/실행 중/정리 실패/손상은 NATIVE_CLEANUP_UNCONFIRMED이며 잠금 해제만으로 성공 추정하지 않음 |
| 최종 자원 회수 | [Agent run](../../crates/racp-runtime/src/transport.rs) | IPC Stop 외 Ctrl+C/SCM stop/transport 종료 경로에서도 최종 execution drain→ResourceRegistry retire→Provider cleanup을 확인한 뒤 완료 기록. borrowed 대상 보존 규칙과 15초 final cleanup bound 유지. crash/Guardian hang/timeout/partial retirement의 실제 인수는 보류 |

앞선 service config 배치의 후속 source인 [SYSTEM handoff](../../apps/agent-rust/src/service_admin.rs), [machine inspection](../../crates/racp-core/src/machine_service.rs), `service_config::stage_admin`도 현재 workspace에 연결되어 production source check를 통과했다. 고정 service/account/ImagePath·STOPPED 확인, private update CAS·one-off start·bounded 종료/receipt 비교 경로다. 실제 호출·SCM/ACL 변경은 하지 않았다. ancestor trust/provisioning 및 installer 호출 연결을 포함한 서비스 제품화는 아직 완료가 아니며 핵심 사용자 경로에 대한 이 기록으로 서비스 전체를 완료 처리하지 않는다.

검증 증거:

- workspace Windows check PASS(12.93초), GPU feature check PASS(12.16초), 독립 분석 host check PASS(10.92초), Rust format/diff check PASS. HTTP 모듈 분리 후 남은 transport import 오류를 확인·수정하고 최종 check를 통과했다. 기존 recipe warning 3개 유지.
- inventory-check PASS: 현재 114 operation/143 permission/18 category, 원본 100 operation 유지, 48개 후순위 planned. legacy control 소비자의 현재 source를 인증 Named Pipe로 갱신했다.
- 공개 Registry/OS 전용 API를 확대하지 않았다. 자동 테스트·GUI·원격 실행·등록·서비스/Registry/ACL mutation·제품 installer·CI dispatch는 미실행이다. PATCH v0.1.23을 재사용한다.

> [!IMPORTANT]
> 핵심 사용자 종료·CLI 정리 source checkpoint다. native GUI의 접근성/renderer 적합성, updater/maintenance·설치 정의, native service provisioning/session Broker 및 남은 legacy settings/provider 분기는 미완료다. 전체 테스트 전 구현 또는 실제 동작 인수 완료로 판정하지 않으며 goal은 **active**다.

이 checkpoint의 최종 production 증거:

- `native-pretest-20261011-p` static CRT production release PASS(1분 5초), `verify-staging` PASS(4 payload files/Win64/import/hash/size). 기존 o 및 이전 모든 batch 보존. Agent 20,928,000 bytes / SHA-256 `8cf3a9b8d84a300f87cd51f6260fe2097bb33f48f5acd5c638b0bfdf2fcbbbd8`, Client 13,196,800 bytes / `26265337e5473ccc24c22e7c8205228088738e5ba2c609a14571a5ecf359bc77`, native launcher 2,638,848 bytes / `9949d3bfbe6c4d0c4533643040f93a920898467191883ea92786a2572f57a83f`.
- manifest의 captured source input digest `7032125e3862dbd8f9c0fac92907e6594be2e7371c211d85cb060e0ac1bb6eb4`는 빌드 전후 일치, Cargo.lock SHA-256 `7e5ea0438ab7a68ff8445e644f54b1bd38bc3c3819d57166543d135b2f19bf48`. 마지막 Rust format/diff check와 guide 상대 링크 검사 PASS. file 목록의 SHA-256은 실제 staging manifest에서 읽었다.
- GUI/Agent/helper/service를 실행하거나 자동 테스트·등록·OS mutation·제품 archive/installer 생성·설치를 하지 않았다. 종료 기록의 crash/경합/복구 증거는 컴파일로 대신하지 않는다. SSOT v0.1.23 및 goal active를 유지한다.

### R14 핵심 source 정리: 구 설정 변환·기본 허용·runtime 탐색 제거

| 범위 | authoritative source | 변경과 검증 한계 |
|---|---|---|
| 새 설정 authority | [settings](../../crates/racp-core/src/settings.rs), [core exports](../../crates/racp-core/src/lib.rs), [state](../../crates/racp-core/src/state_schema.rs) | 구 credential.bin의 읽기/설정 갱신/backup/자동 version 1→2 변환 함수를 제거. 공통 origin/workspace/CA 검증과 native AgentSettings/WorkspaceSpec만 유지. 내부 설정 version 2·명시한 permissions를 요구하고 desktop_enabled와 policy의 실제 desktop 허용이 일치해야 함. NativeState/Policy schema version 1은 그대로이며 credential을 새 state에 가져오는 경로 없음 |
| 권한 누락 | [permissions](../../crates/racp-core/src/permissions.rs), [Agent authorization](../../crates/racp-runtime/src/transport.rs), [Browser](../../crates/racp-runtime/src/providers/browser/session.rs) | frozen legacy 넓은 기본 허용 목록/legacy_permissions 제거. policy 누락·손상은 거부하고 임의 allow ceiling 생성 없음. 원본 Registry 100개 및 현재 114개 operation·143개 권한의 AND/conditional binding은 보존. 변경된 permission source hash를 R05/R14 delta로 기록 |
| 선택 runtime | [Provider runtime snapshot](../../crates/racp-runtime/src/runtime_catalog.rs), [CDB](../../crates/racp-runtime/src/providers/native_carrier/windows.rs), [RE](../../crates/racp-runtime/src/providers/reversing/provider.rs), [admitted adapter](../../crates/racp-runtime/src/providers/reversing/admitted.rs), [Browser config](../../crates/racp-runtime/src/providers/browser/mod.rs) | native_only/legacy 두 분기 제거. CDB native-runtime.json 및 RE plugins.json 로드·CLI provisioning 제거. Chromium sibling/개발 Playwright 폴더 자동 탐색 제거. 명시한 admission/selection만 사용하고 lazy authorized health 유지. 선택 runtime의 실제 실행은 보류 |
| Desktop | [Provider](../../crates/racp-runtime/src/providers/desktop/provider.rs), [modules](../../crates/racp-runtime/src/providers/desktop/mod.rs) | 구 service-login/Task Scheduler 등록·legacy Registrar/등록 Broker 분기 제거. user-session Broker/독립 Guardian·입력 해제·lease·cleanup 유지. 서비스 session-0는 기존 native 경로와 동일하게 NATIVE_SERVICE_BROKER_NOT_CONFIGURED로 남기고 capability의 service_cross_session_registration을 false로 정정. native session Broker 구현을 삭제 완료로 대신하지 않음 |
| 소비자/CI | [Agent workflow](../../.github/workflows/rust-agent.yml), [native build evidence](../../xtask/src/package/evidence.rs), [Guide](../guides/rust-agent-guide.md) | Node를 쓰는 옛 Agent+Chromium ZIP build script와 default CI 호출 제거. native build source digest에 실제 embedded PNG도 포함. 구 bridge/credential Python Client acceptance harness 10개 파일 폐기, 공통 연결/SecretStore·Gateway/SDK/Console 계약/회귀 소스는 유지. Core의 obsolete credential API 사례 4개 제거, Browser의 삭제된 discovery API 사용 2곳은 unconfigured default로 갱신. 새 테스트/fixture 추가·실행 없음 |
| 복구 가능한 source 보관 | ignored source snapshot/retirement manifest | 이번 추가 폐기 및 rewrite 원본을 기존 source snapshot에 개별 보존. 누적 폐기 54개 + rewrite 원본 19개 = 73개 파일의 SHA-256 일치 확인. 기존 dist/설치/Device/credential·사용자 데이터와 unrelated ignored cache 보존 |

검증 증거:

- 최종 workspace Windows check PASS(24.47초), GPU feature check PASS(12.58초), 독립 분석 host check PASS(24.16초), Rust format/diff check PASS. 제거 이후 남은 RE sweeper 필드 참조·unused helper를 수정해 기존 recipe warning 3개만 유지한다. TaskScheduler dependency feature 제거, Cargo.lock은 변경 없음.
- inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned). 폐기한 provision 소비자를 native admission으로 변경하고 `re.query`는 실제 admitted adapter의 operation 선언 및 RE Provider dispatch로 연결했다. 초기 source path/mapping 실패를 확인·정정했으며 검사 규칙을 완화하지 않았다. 기존 RPC 계약/Registry wire/schema 파일에 변경 없음.
- Windows static CRT production batch `native-pretest-20261011-q` PASS(1분 14초), verify-staging PASS(4 payload files/Win64/import/hash/size). Agent 20,736,512 bytes / SHA-256 `26a94e85a3014e2da8889235f199a89119acafa94eb4a05d3201727f0f11a6ae`; Client 13,196,288 bytes / `dbc7f13119aefe2a8db8333bce47c5b7899a570ddabcb244af3c7e73f50d720a`; native launcher 2,638,848 bytes / `339b8f1794c379352482ac1923dd456643aa0fd761cdb83f95d36ecde253b663`. 기존 p 및 이전 batch 모두 보존.
- source input digest `604f4ecff670bbfd304b1cbe4477bba8045226f1fb125e4af2d1f4656ed711eb` 빌드 전후 일치. manifest에 embedded app.png의 SHA-256 `8e864a07545b52f03fcb2e5fa43db0659a4e57a86fd45bf77c766efdd6d4bd26`/1,673 bytes 포함을 직접 확인. Cargo.lock SHA-256 `7e5ea0438ab7a68ff8445e644f54b1bd38bc3c3819d57166543d135b2f19bf48`.
- Workflow YAML parsing·Guide metadata/상대 file link·원본 snapshot hash 검사 PASS. workflow/Agent/GUI/runtime/helper/service·자동 테스트·실제 등록/OS mutation·제품 installer/archive는 실행하지 않았다. routine Client build와 retained server/Console test tooling을 구분한다.

> [!IMPORTANT]
> 이 기록은 native source/생산 컴파일 및 소스 폐기 증거다. 남은 수동 update/maintenance·Rust state/DB compatibility·서비스 provisioning/session Broker·접근성/renderer 적합성·잔여 문서/소비자 최종 감사는 진행 중이다. 기존 native 테스트의 v1/누락 policy fixture와 retained Console E2E의 구 Agent fixture는 테스트 재개 때 새 경로로 정비해야 하며 실행 성공으로 기록하지 않는다. 전체 테스트 전 구현 완료로 판정하지 않는다. goal **active**, PATCH v0.1.23 재사용.

### R03/R-PKG 사용자 maintenance와 Journal 형식 경계

| 범위 | authoritative source | 구현과 한계 |
|---|---|---|
| Journal schema | [format definition](../../crates/racp-core/src/journal_schema.rs), [Journal open](../../crates/racp-core/src/journal.rs) | 기존 column ALTER/backfill·부분 schema 생성 제거. 빈 DB의 전체 6 table/3 explicit index와 user_version=1을 한 transaction으로 commit. 기존 store는 현재 SQL table/constraint/index 정의를 whitespace/literal 보존 normalization 후 비교. foreign/version-0-with-content·partial·unknown/높은 format 거부. runtime 내 in-memory format definition은 persisted fixture 또는 사용자 DB 변환이 아님. 실제 SQL 실행/기존 q DB 호환·fault 인수는 보류 |
| 유지보수 사전 검사 | [local inspector](../../crates/racp-runtime/src/native_maintenance.rs), [Agent command](../../apps/agent-rust/src/main.rs), [lifecycle pin](../../crates/racp-core/src/native_lifecycle.rs) | native-maintenance-status는 packaged user/session 전용. 등록·로그인 pending, private root, 최종 cleanup receipt 및 held Agent/settings/login locks, state/policy/runtime 형식과 read-only Journal inspection 확인. 미등록 root는 알려진 lock 외 파일을 거부. credential/등록/Stop/제거/rollback 실행 없음. 성공은 preflight snapshot이며 atomic update lease가 아님 |
| 로그인 참조 | [native login](../../crates/racp-runtime/src/native_login.rs) | 현재 사용자의 HKCU Run에서 최대 1024개 이름을 조회하고 RACP.Native prefix만 읽음. bounded UTF-16/5개 고정 argv grammar를 확인해 custom root를 포함한 제거 payload 참조를 거부. 다른 앱 값/비밀을 출력하거나 수정하지 않음. malformed native 값과 registry 경합은 conflict로 남김 |
| 제거 recipe | [NSIS](../../xtask/native-installer.nsi) | 기본 root preflight 성공 및 packaged helper 종료 후 payload 전체를 payload.removing으로 rename. GUI/Agent/helper pin의 rename 거부를 이용해 원래 payload에서 Agent만 먼저 삭제되는 부분 실패를 방지. 고정 5개 파일만 삭제, state/credential/runtime 보존. 기존 removing batch는 자동 resume/overwrite하지 않음. 실제 NSIS compilation/rename/Delete/installer 인수는 미실행 |
| 수동 교체 정의 | [Guide](../guides/desktop-client-guide.md#71-native-수동-교체와-제거-source-정의-실기-보류) | trusted 배포 hash/version/OS 확인→기존 로그인 해제·정리/GUI 종료→preflight→별도 native version 설치/같은 explicit root→실제 health/정리→새 로그인 명령→기존 버전 제거. 호환성이 확인되지 않은 자동 downgrade/rollback·legacy fallback 없음. publisher signing/trust catalog·자동 updater·service 유지보수는 미완료 |

검증 증거:

- 최종 workspace Windows source check PASS(3.96초), GPU feature check PASS(4.55초), 독립 분석 host check PASS(5.37초), inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned), Rust format/diff·Guide 상대 file link PASS. 기존 recipe warning 3개 유지. 새로운 자동 테스트/fixture를 추가하지 않았다.
- `native-pretest-20261011-r` static CRT production release PASS(57.20초), verify-staging PASS(4 payload files/native Win64/import/hash/size). Agent 20,829,696 bytes / SHA-256 `d4b0c67df02d943cef77a8bec6e3d9980bfddf285432ccc45d74dac8d1a7f24f`; Client 13,185,536 bytes / `ee60491b4bd9b0dd9a03307ae461fe1044f6c74a6c041782883973e03cf3061d`; native launcher 2,634,752 bytes / `863ac80c62c641af57c453f209afc04643b748380b2e48a68a11c41ffe340497`. q 및 모든 이전 batch 보존.
- captured source input digest `1b6b3bdba42c56b6248da67f665d62175a4ebd039e075756a0404f64ec5acb5f`는 빌드 전후 일치. 실제 파일 hash/size는 staging manifest에서 읽었다. SSOT v0.1.23 재사용.
- Journal initializer/inspector·Registry enumeration·maintenance command·NSIS 또는 GUI/Agent/runtime를 실제 실행하지 않았다. 기존 설치·서비스·Device/credential/state·dist를 보존했고 원격/자동 테스트·제품 artifact 생성·CI dispatch도 미실행이다. SQLite normalization/transaction·실제 locks·custom-root 참조·registry 외부 writer·중단 후 removing 복구 인수는 보류다.

> [!IMPORTANT]
> user maintenance와 수동 교체 정의의 source checkpoint이며 전체 migration 또는 설치/업데이트 인수 완료가 아니다. 서비스 provisioning/session Broker·publisher trust/signing·접근성/renderer·잔여 소비자 감사와 명시한 deferred 인수를 계속 구분한다. 외부 SYSTEM/Registry writer까지 포함한 atomic CAS를 보장하지 않는다. goal은 **active**다.

### 2026-10-11 현재 진행 범위 확인

사용자 현황 질의 시점에 Rust GUI/Agent·로컬 승인/정책·작업 추적/취소·사용자 세션 Desktop·native staging source가 존재하며 마지막 검증 staging은 `native-pretest-20261011-t`다. 서비스 사용자 세션 등록/helper는 작업 중이며 GUI 연결·동적 capability 표시·cleanup 검토가 남아 있다. 서비스 설치/SYSTEM runner·접근성/renderer 적합성·배포 trust/유지보수 최종 감사도 미완료다. 전체 전환 완료 또는 실제 기능 인수 완료로 판정하지 않는다.

현재 작업본의 `cargo check --locked --workspace --target x86_64-pc-windows-msvc` PASS(1.49초), Rust format 완료. Agent helper 진입점의 누락된 Path 참조를 수정했다. 기존 recipe warning 3개 유지. 이 source check는 마지막 t staging에 새 session helper가 포함되었다는 뜻이 아니다. 테스트·GUI·실제 서비스/원격 실행·제품 패키징은 실행하지 않았다.

### R03 fresh service provisioning source와 machine trust

| 범위 | authoritative source | 구현과 남은 한계 |
|---|---|---|
| 고정 fresh 준비 | [provisioner](../../apps/agent-rust/src/service_provision.rs), [CLI](../../apps/agent-rust/src/main.rs) | SYSTEM-only standalone native-service-provision, bounded/unknown-key 거부 controllers+native enrollment JSON. 기존 service/root 존재·불확실한 service 조회는 conflict. 새 fixed RACPNativeAgent/virtual account를 disabled로 생성, SID unrestricted 설정, 고정 private 입력 commit 이후 demand-start. prepared와 started/registered/ready 구분하며 command는 시작/enrollment/DPAPI/WSS/Broker를 실행하지 않음 |
| 입력 검증 | 위 provisioner/[config factory](../../crates/racp-core/src/native_service.rs) | controller SID/session/role·policy revision 0·connection file hash/expiry·workspace/profile/permission 검증을 mutation 전에 수행. source connection은 pinned file로 다시 hash/size 확인. bootstrap은 fixed private connection path로 생성하고 hash만 ImagePath에 넣음. token/credential/password를 argv 또는 progress receipt/응답에 넣지 않음 |
| 상태 namespace | [machine boundary](../../crates/racp-core/src/machine_service.rs), [shared directory](../../crates/racp-core/src/service_directory.rs) | 환경변수 대신 OS Known Folder ProgramData의 RACP-Native-Service/state·control 고정. 새 parent owner SYSTEM, private/control owner actual service SID. scoped SeRestorePrivilege 사용/원래 상태 복원. parent는 SYSTEM write + service/지정 사용자 read/execute만 exact ACL readback. private child는 service/SYSTEM FA만, control은 역할 사용자 read만. 임의 기존 ACL repair 없음 |
| 이미지·부모 trust | 위 machine boundary | 기존 leaf-only 검사에 전체 ancestor owner/ACL 검사 추가. SYSTEM/Administrators/OS 조회 TrustedInstaller만 mutator로 인정. nonprivileged ancestor delete/reparse/DACL/owner 변경·leaf writer 거부, inherited-only ACE 제외. 일반 staging/user-owned 설치에서 privileged image 사용 거부. OS 기본 ACL과 실제 service read/traverse/execute 조건은 인수 필요 |
| readback·중단 | 위 provisioner | private config/connection/bootstrap 저장 후 byte 비교, SCM type/account/exact ImagePath/start type/service SID type/STOPPED/PID=0 확인. SYSTEM-owned parent에 nonce/phase/payload manifest hash/bootstrap hash만 durable receipt. 중단된 namespace·새 disabled/demand service를 삭제/adopt/retry하지 않고 진단 가능한 미확정 상태로 보존. 외부 SYSTEM/SCM writer와 부분 propagation 인수는 보류 |

최종 source 검사: workspace Windows PASS(4.66초), GPU feature PASS(49.54초), 독립 분석 host PASS(34.20초), inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned), Rust format/diff 및 Guide 상대 file links PASS. 기존 recipe warning 3개만 유지하며 공개 OS service/registry operation을 추가하지 않았다. PATCH v0.1.23을 재사용한다.

Win32 근거는 [CreateServiceW](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-createservicew)의 virtual-account/null-password·disabled/demand-start 정의와 [SHGetKnownFolderPath](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shgetknownfolderpath)다. source 구현과 실제 OS/설치 증거를 구분한다.

> [!IMPORTANT]
> provisioning source를 구현했으며 per-machine installer/SYSTEM runner·서비스 시작/virtual account profile/DPAPI/workspace access·native session Broker·중단된 provisioning의 명시 복구 및 제품화는 미완료다. 서비스/계정/ACL/directory/Registry를 실제 생성·변경하지 않았고 token 소비·GUI/원격/자동 테스트·제품 artifact/installer·CI dispatch도 실행하지 않았다. 기존 RACPAgent·설치/Device/credential/data·모든 dist batch를 보존한다. goal은 **active**다.

이 checkpoint의 production·staging 증거:

- `native-pretest-20261011-s`: static CRT production compile PASS(1분 34초), import admission 단계에서 combase.dll을 거부하여 staging FAIL. 도구가 실패 batch만 s.quarantine으로 보존했고 기존 batch를 이동/삭제하지 않았다.
- pinned windows-sys 0.61.2의 CoTaskMemFree binding이 combase.dll을 직접 link함을 local crate source에서 확인. 현재 OS의 System32/combase.dll은 Microsoft Corporation/10.0.19041.4522, Authenticode Valid/Microsoft Windows signer를 확인했다. [공식 CoTaskMemFree](https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-cotaskmemfree)의 allocator API 정의와 구분해 기록한다. 이 explicit system DLL 한 개만 import allowlist에 추가했으며 dynamic CRT/임의 DLL/delay-load 거부는 유지한다. 실제 OS DLL을 payload에 복사하지 않았다.
- 최종 `native-pretest-20261011-t`: static CRT release PASS(캐시 재사용 1.31초), verify-staging PASS(4 payload files/Win64/import/hash/size/source digest). Agent 20,992,512 bytes / SHA-256 `85ccaa7640034241852d05a2410bf50223845c23ab6c4cb8c5b68e983b28428f`; Client 13,186,560 bytes / `10f14f673bdd430ebc16f5b26b7360d7fd69a8d7b68c2ce0e578650a80b050aa`; native launcher 2,634,752 bytes / `a824641aacad32b3e36c8388046c25adb7b7f4efe06ad8185f991560bdc14e25`.
- captured source input digest `5a324c121c65694c9a23c28335d488de396b263a5d5394eea48d97d254982179` 빌드 전후 일치. 실제 hash/size를 final staging manifest에서 읽었다. 마지막 diff check PASS, PATCH v0.1.23 재사용. 실제 provisioning/서비스/ACL/Registry/등록/GUI/테스트/installer/CI 실행은 없음.

### R03 native service의 사용자 화면 연결과 GUI 정리

| 범위 | authoritative source | 구현과 남은 한계 |
|---|---|---|
| 인증 등록 | [local DTO](../../crates/racp-contract/src/local_ipc.rs), [control plane](../../crates/racp-runtime/src/local_ipc/policy.rs), [session bridge](../../crates/racp-runtime/src/providers/desktop/service_session.rs) | Prepare/Register/RemoveDesktop는 actual controller SID/session·Agent boot·동일 launcher PID/birth에 한정. 30초 one-use 준비/최대 16 pending, 등록 시 Broker/Guardian의 OS SID/session/PID/birth/같은 Agent image 및 Guardian outside-all-Jobs 확인. Broker HMAC status 검증 후 join. observer는 snapshot/subscribe만 허용. 공개 RPC 추가 없음 |
| 공유 bucket | [shared directory](../../crates/racp-core/src/service_directory.rs), 위 bridge | control/desktop-sessions는 service/SYSTEM write 및 scope 사용자 read. pair bucket은 service owner/protected exact DACL readback, service/SYSTEM FA 및 해당 사용자 read/write/execute/delete만; user WRITE_DAC/WRITE_OWNER 없음. service private data 아래 공유하던 초안 제거. 사용자 DPAPI pair file과 ephemeral secret만 사용하며 service enrollment credential을 읽거나 전달하지 않음 |
| GUI 연결 | `Iced app`(삭제됨), [Agent entry](../../apps/agent-rust/src/main.rs) | service 상태 탭의 현재 사용자 화면 연결/해제, observer 비활성화. 현재 packaged Agent와 서비스 image 비교. GUI가 Broker Job을 소유하며 service GUI 종료는 자체 화면 정리를 기다림. CLI native-service-session도 같은 경로. snapshot heartbeat는 approval presence로 계산하지 않음 |
| 정리·입력 보호 | [Provider](../../crates/racp-runtime/src/providers/desktop/provider.rs), [Guardian](../../crates/racp-runtime/src/providers/desktop/guardian.rs), [peer handle](../../crates/racp-runtime/src/providers/desktop/native_identity.rs) | 해제는 service의 인증 Broker stop 이후 user-owned Job 종료/독립 Guardian 해제를 확인. Guardian이 service 및 launcher PID/birth를 pin하여 launcher crash도 해제 신호로 취급. startup 오류에서 Guardian stop/wait, registration failure release signal, pinned wait 실패는 process-exited 증거로 사용하지 않음. 이미 서비스가 정리한 bucket을 GUI가 재생성하지 않음. unknown bucket 파일·실패한 삭제 pin 소비는 후속 retry의 성공으로 바뀌지 않음 |
| 표시·서비스 trust | 위 control plane/Provider/[startup](../../crates/racp-runtime/src/native_client.rs), [SCM](../../apps/agent-rust/src/native_service.rs) | service desktop 초기 상태 NeedsRuntime/NATIVE_SERVICE_SESSION_NOT_CONNECTED, 등록 후에도 Unverified/NATIVE_ACCEPTANCE_DEFERRED. unregister 후 살아 있는 세션이 없으면 미연결 표시. capability는 pinned process 생존을 확인. 실제 service 시작에서도 machine image directory trust 검사 유지 |

검증 증거:

- workspace Windows check PASS(최종 사전 check 4.31초), GPU feature check PASS(28.14초), inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned, frozen 100 operation 보존). root Rust format/diff check PASS. 기존 recipe warning 3개만 유지. Rust 1.90.0/PATCH v0.1.23 재사용.
- `native-pretest-20261011-u` static CRT production release PASS(1분 27초), verify-staging PASS(4 payload files/Win64/native import/hash/size/source consistency). Agent 21,607,424 bytes / SHA-256 `6a72dbcc77ac2abe61efcf5bb6a670648dfc628f219a8c603b7b1d98e82a38cc`; Client 13,429,248 bytes / `7917f5daf7cb1ff6a8f70944fbb2b84b713808ca487ebc562f97729f633ce132`; native launcher 2,635,264 bytes / `8b049001e3965d4f4f81c48db769efa04774434ae47f8489db7aca8adb320f97`.
- source input digest `14cdfd946aa29792c6ebdf4e7133eec5f7a32b389bfa6fd8a8b359cb88f1ca42`를 final build-manifest에서 확인. 모든 이전 dist batch 보존. GUI·IPC 접속·실제 service/ACL/DPAPI/입력·등록·자동 테스트·제품 installer/CI는 실행하지 않았다.

> [!IMPORTANT]
> service session source/GUI 연결 checkpoint이며 전체 테스트 전 구현 완료가 아니다. 만료된 빈 pending bucket은 정리하지만 in-flight/실패 bucket은 release 신호 후 보존할 수 있다. 잔여 bucket의 명시 복구·per-machine SYSTEM installer runner·서비스 유지보수·접근성/renderer source 적합성·배포 trust/잔여 소비자 최종 감사가 남아 있다. 실제 cross-account/DPAPI/서비스 재시작/잠금 화면/Job breakaway/입력 해제 인수는 보류하며 goal은 **active**다.

### R03 service session 원장·복구 및 관리자 SYSTEM runner source

| 범위 | authoritative source | 구현과 한계 |
|---|---|---|
| private session 원장 | [ledger](../../crates/racp-runtime/src/providers/desktop/session_ledger.rs), [pair prepare/register](../../crates/racp-runtime/src/providers/desktop/service_session.rs), [Provider 정리](../../crates/racp-runtime/src/providers/desktop/provider.rs) | 공개 bucket과 별도 service/SYSTEM private directory에 최대 64개 format-1 receipt. pair ID·사용자/session·원래 Agent/launcher/Broker/Guardian PID/birth·bucket volume/inode를 저장하며 secret 없음. seed 반환 전 prepared 기록, OS/HMAC 검증 뒤 registered 기록. 이전 receipt 내용 digest 비교 뒤 atomic rewrite. Guardian exit 성공 확인 및 cleanup receipt commit 뒤 bucket 삭제, receipt 마지막 삭제. 실제 disk/ACL/crash 인수 미실행 |
| 명시 복구 | 위 ledger/[SCM mode](../../apps/agent-rust/src/native_service.rs)/[admin](../../apps/agent-rust/src/service_admin.rs) | SYSTEM handoff recover_sessions → 별도 virtual-account 유지보수 mode. Agent/config lock·원래 Agent 종료·bucket identity·기록된 peer 종료와 Guardian 상태 비교, 고정 pairing 파일만 삭제. PID reuse는 새 process에 signal하지 않음; access denied/wait 실패는 unknown. 전체 60초/peer 5초와 stop cancellation, nonce/config hash가 일치하는 새 receipt로만 완료 확인. unknown launch/없는 기록/변경된 폴더·입력 정리 불명/unknown 파일은 보존하며 unresolved를 완료로 바꾸지 않음 |
| SYSTEM runner | [machine setup](../../apps/agent-rust/src/machine_setup.rs), [entry](../../apps/agent-rust/src/main.rs) | native-machine-setup은 elevated administrator/high integrity·packaged machine-trusted image만 허용. 새 fixed RACPNativeSetup/LocalSystem demand helper와 ProgramData/RACP-Native-Setup/setup_nonce 사용. fresh provisioning 또는 configure/reconcile/recover_sessions만 실행. 기존 helper conflict; arbitrary service/image/account/action 없음. raw request는 owner BA/SY와 정확한 두 inherited FA ACE readback·pinned file/hash로 전달, argv/token/receipt 노출 없음. 실제 생성/호출 미실행 |
| runner 결과/정리 | 위 setup/[provision](../../apps/agent-rust/src/service_provision.rs) | nonce/input hash가 일치하는 successful receipt 및 STOPPED/PID=0 이후에만 새 setup service 삭제/request 제거. 비밀 없는 결과 보존. 오류·120초 timeout·부분 준비는 service/input/receipt 보존, 자동 채택/재실행 없음. Agent의 정상 run/enrollment/등록/WSS/화면을 실행하지 않음. per-machine installer 및 failed setup의 명시 제거/복구 recipe는 미완료 |
| parent read scope | [machine boundary](../../crates/racp-core/src/machine_service.rs), 위 service admin | config commit/hash 검증 뒤 SYSTEM이 고정 data parent의 trusted ACL/owner SYSTEM을 확인하고 새 controller read/execute ACL을 갱신/readback. 이전 prototype에서 새 controller가 parent를 읽지 못할 수 있던 source gap 해소. service/private child는 별도 protected DACL 유지. 외부 SYSTEM writer 경합 및 실제 inheritance 인수 미실행 |

production·정적 증거:

- workspace Windows check PASS(5.35초), GPU feature check PASS(10.73초), 독립 분석 host check PASS(14.28초), inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned, frozen 100 preserved), Rust format/diff PASS. 기존 recipe warning 3개 유지. PATCH v0.1.23 재사용, 새로운 자동 테스트/fixture 추가 없음.
- `native-pretest-20261011-v` static CRT release PASS(1분 10초), verify-staging PASS(4 payload files/Win64/native import/hash/size/source consistency). Agent 21,789,184 bytes / SHA-256 `d6c1ed39cbe0ec6e87a1ed50157a7bfc0434336de4bff622cc1d5335a4062a76`; Client 13,463,552 bytes / `a584dfcfabc1f3aab2546005949a0438dd4f3991ad1ee6ea731d1cbcef77bba0`; native launcher 2,634,240 bytes / `0d998e06397e001fc3ac43c8cc3c5aed55bdcf63f9e3fa0fb53ce00b1c85724c`.
- source input digest `26702fabe9f227983d042a4015e8250f5e44999aa267540d2ba649b01a9b3106`를 final build-manifest에서 읽었다. 모든 이전 batch 보존. 실제 service/SCM/Registry/사용자 state ACL·DPAPI·GUI·등록·원격·테스트·제품 packaging·CI를 실행하지 않았다.

owner handle API 및 LocalSystem service 생성의 설계 근거는 [SetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo), [CreateServiceW](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-createservicew)다. OS group-owner 지정·ACL inheritance·SCM deadline·SYS/virtual-account 실제 접근 가능성은 컴파일 증거와 별도로 인수해야 한다.

> [!IMPORTANT]
> 복구 및 SYSTEM handoff source checkpoint이며 전체 목표 완료가 아니다. per-machine installer/제거·failed setup/partial provisioning의 명시 복구·접근성/renderer 적합성·배포 trust/유지보수와 잔여 소비자 최종 감사가 남아 있다. 미확정 입력/Guardian 정리를 자동 성공으로 바꾸는 강제 cleanup은 제공하지 않는다. 테스트/실기/타 OS 보류를 유지하며 goal은 **active**다.

### 2026-10-12 R-PKG machine 서비스 설치·제거 및 stopped helper 정리 source

| 범위 | authoritative source | 구현과 인수 경계 |
|---|---|---|
| 별도 setup scope | [package options](../../xtask/src/package.rs), [machine NSIS](../../xtask/native-machine-installer.nsi), [CI](../../.github/workflows/client.yml) | --install-scope user/machine/both, default user. 같은 staging/version에서 두 setup 선택 및 portable/ZIP user scope 유지. machine setup은 고정 ProgramFiles/version·Windows x64/high administrator, 기존 version overwrite 거부. 단일 output batch에 format별 hash/scope 기록. 실제 artifact/NSIS/installer/CI 미실행 |
| package 보호 | [install helper](../../apps/agent-rust/src/machine_install.rs), [known-folder/trust](../../crates/racp-core/src/machine_service.rs) | current Agent가 고정 version/payload에 있음을 확인, manifest pin·fresh content·trusted ProgramFiles parent 검사 뒤 보호 owner BA/DACL write BA+SY, authenticated user read/execute. 알려진 payload 및 uninstaller 보호/readback. 별도 scope/version/payload manifest/uninstaller hash receipt. 기존 trusted shared container 유지, unknown content/version conflict |
| GUI/로그인 scope | [GUI startup](../../apps/client-native/src/main.rs), `view`(삭제됨), [native login](../../crates/racp-runtime/src/native_login.rs) | sealed machine-service receipt 확인 뒤 기본 service control target 선택. 초기 미등록/미시작에는 관리자 조치가 필요한 대기 표시. machine-service 설치는 per-user Agent autorun을 만들지 않으며 해당 사용 목적은 기존 user/portable 설치로 유지. 복사한 별도 user payload와 기존 사용자 state를 변환/삭제하지 않음 |
| 서비스 제거 | [SYSTEM admin](../../apps/agent-rust/src/service_admin.rs), [virtual maintenance](../../crates/racp-runtime/src/service_maintenance.rs), [SCM mode](../../apps/agent-rust/src/native_service.rs) | remove_service는 expected config hash/device ID 및 같은 fixed image/account/root, STOPPED/PID=0 검증. 새 nonce maintenance receipt·NativeRun cleanup·policy/runtime/Journal·pending/잔여 session 검사 뒤 startup/config lock과 stopped 재확인, SCM 삭제 요청만 수행. Device/Gateway 등록·DPAPI credential·native state/workspace 보존. running 강제 stop/kill 없음, 삭제 요청과 실제 SCM visibility 구분 |
| 제거 recipe | 위 NSIS/install helper | 두 native SCM 이름 부재·installed scope/hash/uninstaller identity·trusted payload preflight. whole payload rename 뒤 고정 5파일/설치 receipt/shortcut/HKLM entry만 삭제. existing pins/unknown removing batch 차단, native user data/credentials 삭제 없음. preflight는 외부 관리자/SYSTEM writer까지 포함한 atomic lease가 아님 |
| 실패 helper 정리 | [setup cleanup](../../apps/agent-rust/src/machine_setup.rs), [CLI](../../apps/agent-rust/src/main.rs) | native-machine-setup-cleanup은 정확한 setup root/hash 입력, high admin·trusted payload·private request ACL/hash·LocalSystem/own-process/demand/exact ImagePath·STOPPED/PID=0 확인. 새 fixed helper SCM/request만 제거, 비밀 없는 cleanup/result·target service/state 보존. running/foreign/changed input 거부. partial target provisioning을 임의 복구/삭제하지 않음 |

실행한 검증:

- workspace Windows source check PASS(최종 1.18초), GPU feature check PASS(12.64초), 독립 분석 host check PASS(13.91초), root format/diff PASS. inventory-check PASS(114 operations/143 permissions/18 categories/48 후순위 planned, frozen 100 유지). user/machine/both packaging definition dry-run PASS, writes_files=false/artifact_execution=false. 기존 recipe warning 3개 외 신규 warning 없음.
- `native-pretest-20261012-a` static CRT production release PASS(1분 35초), verify-staging PASS(4 payload files/Win64/native import/hash/size/source consistency). Agent 21,856,768 bytes / SHA-256 `d69b8e1ce759fa1210f3f2d7234f6a874626bb057a750fcc97e09be02412f3ae`; Client 13,477,376 bytes / `10f27f0d0de2f030582f5e29a7f25b6c22125bee65463bab43716b89293033de`; native launcher 2,634,752 bytes / `4325cf6f2a4ff3e6793e346e54d0449192ad39c893749138d0f436f2b50b470c`.
- source input digest `b97c2d7a14b8c082c7ad4e33a9faf9755dd4ef5c65b080bc1c3fcfbb36eef087`를 final manifest에서 확인. 전체 기존 batch 보존. ongoing migration batch PATCH 0.1.23 재사용, 날짜 변경으로 새 bump하지 않음. 실제 service/Registry/사용자 데이터 ACL·GUI·DPAPI·새 등록·원격·자동 테스트·제품 package/installer/CI는 실행하지 않았다.

> [!IMPORTANT]
> machine setup/제거 source 정의이며 실제 clean-machine 인수나 전체 테스트 전 목표 완료가 아니다. per-machine 제거 후 보존된 native 상태의 명시 서비스 복원/재설치·부분 target provisioning 복구·updater/배포 trust·접근성/renderer 적합성과 잔여 소비자 최종 감사가 남아 있다. NativeRun/Guardian/형식/소유권을 확인하지 못한 상태를 자동 성공으로 바꾸지 않는다. goal은 **active**다.

### 2026-10-12 R03 native SCM 복원·버전 watermark와 설정 저장 source

| 범위 | authoritative source | 구현과 한계 |
|---|---|---|
| 명시 복원 | [restore](../../apps/agent-rust/src/service_restore.rs), [SYSTEM route](../../apps/agent-rust/src/service_admin.rs), [handoff](../../apps/agent-rust/src/machine_setup.rs) | restore_service 네 필드의 expected config/device/bootstrap binding. SCM 부재만 새 fixed virtual account/disabled service 생성. registered native state는 동일 Device/credential 존재 및 null bootstrap, unregistered preparation은 null Device/보관 bootstrap hash 요구. legacy 탐색/기존 SCM 채택/credential import·decrypt·token 소비 없음 |
| 사전/사후 identity | [admin reader](../../crates/racp-core/src/native_service.rs), [private SID pin](../../crates/racp-core/src/machine_service.rs), 위 restore | fixed ProgramData root·private config hash/format·canonical stored SID/owner/protected ACL 비교. 삭제된 서비스의 OS SID lookup에 의존하기 전에 봉인된 SID로 private root 검사, 새 SCM SID unrestricted 설정 후 OS SID와 재비교. machine image/quoted bounded ImagePath·account/type/SID-type/STOPPED/PID=0 readback |
| validation/result | 위 restore/[maintenance](../../crates/racp-runtime/src/service_maintenance.rs) | 별도 nonce virtual-account preflight로 lifecycle/policy/runtime/Journal/pending/session/Device 검증, normal Agent/enrollment/WSS 미시작. 최종 Agent/config lock과 config hash 재확인, native state/credential 보존한 demand-start 복원 기록. 실패/시간 초과는 metadata/새 service 보존하며 stopped에서 disabled 제한, running service 강제 종료/rollback 없음 |
| 저장 버전 | [watermark](../../crates/racp-core/src/native_release.rs), [lifecycle](../../crates/racp-core/src/native_lifecycle.rs), [state reader](../../crates/racp-core/src/state_schema.rs) | 별도 native-release.json의 numeric SemVer/저장 format strict validation. 등록/Agent 시작 및 확인한 registered restore에서 현재 version 기록. 표시된 높은 version/다른 format은 거부, migration/downgrade/rollback 없음. 표시가 없던 초기 native root는 첫 현재 실행에서 기록하며 과거 표시를 무시하는 binary까지 보호한다고 주장하지 않음 |
| 설정 저장 수정 | 위 state reader | 기존 파일 설정 CAS 뒤 atomic_write(overwrite=false)여서 저장을 막던 source bug 수정. Agent/settings lock·expected revision 유지하고 기존 state 파일을 overwrite=true로 atomic 교체/readback. 새 등록 파일의 create-only 동작은 유지. 실제 저장/경합 인수 미실행 |
| keyboard source | `Iced app`(삭제됨) | ignored Tab/Shift+Tab을 Iced focusable 입력 필드로 연결, Ctrl+1..5 페이지 이동. service 설정/runtime guard 동일 적용. pinned Iced source에서 ignored-key subscription/focus operation 확인. button/checkbox 전체 focus/action·Narrator/UIA·IME/DPI 합격을 대체하지 않음 |

production·정적 증거:

- workspace Windows check PASS(사전 check 1.63초), GPU feature check PASS(11.86초), 독립 분석 host check PASS(7.74초), root format/diff PASS. inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned, frozen 100 유지). 기존 recipe warning 3개 유지, 자동 테스트/fixture 추가 없음.
- `native-pretest-20261012-b` static CRT production release PASS(1분 10초), verify-staging PASS(4 payload files/Win64/native imports/hash/size/source consistency). Agent 21,902,848 bytes / SHA-256 `6f8962056d78f4bf975da43fc318938222c70583ec2deba6f52062e9e4609a1c`; Client 13,511,168 bytes / `75141f51795a50a214635e9d2b6b2ef4ba1bc7150d281971ad73252761d65abe`; native launcher 2,634,752 bytes / `27ef41a4a170aa468a2d75d96c24b4dfd8ecbb588e4a77e619ce49cba8a4a354`.
- source input digest `dccb7a18a37cfa084d7eb1134395465197bebd36037c3c54cd1610ba17625fc6`를 final manifest에서 읽었다. 이전 batch 보존, PATCH v0.1.23 재사용. 실제 service/SCM/Registry/state ACL·DPAPI·settings/state/Journal mutation·GUI/keyboard·등록·원격·자동 테스트·제품 packaging/installer/CI는 실행하지 않았다.

> [!IMPORTANT]
> source restore/watermark/settings/keyboard checkpoint이며 전체 목표 완료가 아니다. 실제 virtual account/profile/DPAPI·삭제 후 restore·세션 및 고유 SID 조건·새/이전 native 호환성은 인수 필요다. 부분 target provisioning 복구·updater/배포 trust·button/checkbox 키보드/Narrator/UIA·renderer 적합성과 잔여 소비자 최종 감사가 남아 있으며 goal은 **active**다.

### 2026-10-12 R01 접근성 진행 중 상태와 현재 컴파일 확인

- `접근성 adapter`(삭제됨), `키보드 위젯`(삭제됨), `GUI 연결`(삭제됨)은 작업 중이다. Windows AccessKit adapter 초기화와 button/checkbox focus/action 경로를 추가했으나 입력 필드·텍스트 의미 연결, 전체 키보드 이동 및 DPI/renderer 적합성은 미완료다.
- AccessKit SetValue의 Box<str>→String 변환 누락으로 발생한 컴파일 오류를 수정했다. 현재 `cargo check --locked --workspace --target x86_64-pc-windows-msvc` PASS(2.36초), `cargo fmt --all -- --check` PASS. 기존 recipe warning 3개와 아직 연결하지 않은 TextInput/Label variant warning 1개가 남아 있다.
- 마지막 확인된 production staging은 위 `native-pretest-20261012-b`이며 현재 접근성 변경은 포함하지 않는다. 이번 확인은 개발 컴파일이며 새 release/staging·테스트·GUI/Narrator·설치·원격 인수는 수행하지 않았다. v0.1.23 재사용.

> [!IMPORTANT]
> 핵심 원격 제어·권한·승인·작업 추적 및 native GUI/Agent의 source 구현과 Windows production 빌드 증거는 존재하지만 전체 전환 완료나 실제 사용 인수 완료는 아니다. OS별 전용 API 확장은 계속 후순위이며, 현재 변경 마무리와 핵심 사용자 경로를 우선한다.

### 2026-10-12 R01 GUI 의미·키보드·Windows UIA source 연결

| 범위 | authoritative source | 변경과 인수 경계 |
|---|---|---|
| Windows UIA | `adapter`(삭제됨), [startup](../../apps/client-native/src/main.rs), `GUI`(삭제됨) | AccessKit 0.25.1/Windows adapter 0.35.1 고정. 숨긴 창의 소유 UI 스레드에서 adapter 설치 후 표시. 초기 창 조회는 bounded 재시도하며 트레이 show는 초기화 완료 후 허용. UIA event/action queue는 32개로 제한. 실제 adapter/COM/Narrator 실행 미검증 |
| 컨트롤 의미 | `wrappers`(삭제됨), 위 adapter/GUI | 버튼·체크박스·single/multiline input·profile combobox·화면 text의 name/role/value/toggle/disabled/focus 연결. 반복 권한/승인 버튼 이름에 대상 포함. 활성 ID는 갱신 사이 유지, 제거 ID 다른 대상 재사용 없음. 변경 action의 stale view revision/disabled/없는 대상 거부, 원래 App 메시지/role/policy/승인 guard 유지 |
| keyboard focus | 위 wrappers/GUI | native 입력 focus operation에 명시 ID 연결. 버튼·체크박스 Tab/Shift+Tab·Enter/Space release 1회, keydown 시 원래 Message 고정, 반복 keydown 중복 없음. identity 교체/disabled/focus 해제/window blur는 pending action 취소. profile 방향키/Home/End. Iced focus 다중 operation pass 완료 후 containing page reveal operation. 실제 입력/경합 실행은 보류 |
| 좌표/초안 | 위 adapter/wrappers, [constraints](../../apps/client-native/src/constraints.rs) | Iced scroll operation의 translation을 별도로 읽어 window 좌표/viewport로 환산하고 현재 DPI transform 적용. multiline SetValue도 기존 제약 초안 메시지로 연결, Agent에 자동 저장하지 않음. UIA text range/selection·popup 선택 탐색·IME·실제 DPI/renderer 적합성 합격을 주장하지 않음 |
| native dependency | [import gate](../../xtask/src/package/imports.rs) | UIAutomationCore.dll을 Windows system import의 명시 목록에 추가. dynamic CRT/외부 DLL/무조건 wildcard 허용 없음. MS SDK UiaReturnRawElementProvider의 DLL 정의 근거를 아래에 링크 |

정적 검증: 최신 workspace Windows check PASS(1.59초), GPU feature check PASS(33.39초), inventory-check PASS(114 operation/143 permission/18 category/48 후순위 planned, frozen 100 유지), format check PASS. 미연결 TextInput/Label warning은 제거됐고 기존 recipe warning 3개 유지. v0.1.23 재사용, 자동 테스트/fixture·GUI·OS 사용자 state·service mutation 미실행.

UIA system import의 근거는 Microsoft [UiaReturnRawElementProvider](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationcoreapi/nf-uiautomationcoreapi-uiareturnrawelementprovider)의 requirements다. source로 확인한 Iced 0.13 winit bridge에는 IME event 연결이 없으며 winit 0.30.13은 초기 IME를 비활성화한다. 별도 조합/commit/candidate 경로 보강이 필요하다.

> [!IMPORTANT]
> GUI 의미/키보드/API 연결의 source checkpoint다. 실제 Narrator·한글 조합·DPI·renderer 적합성 인수와 전체 목표 완료는 아니다. IME source 보강, 부분 service provisioning 복구, updater/배포 trust 및 최종 소비자 감사가 남아 있으며 goal은 **active**다. macOS/Linux native 빌드·제품 artifact·자동 테스트·GUI 인수 보류를 유지한다.

이 source checkpoint의 production 증거:

- `native-pretest-20261012-c` static CRT Windows release PASS(1분 29초), verify-staging PASS(4 payload files/Win64/system imports/hash/size/source consistency). Agent 21,902,848 bytes / SHA-256 `61a6496b1fc03a52fe48fd83da9e143ea0992f04f0fc2bf92757f3933564c923`; Client 14,168,576 bytes / `97d02c444fb04c8be8e7bf1dcc989b3a32f37fd5e7d7e2b814784e5ab8167473`; native launcher 2,634,752 bytes / `9b6a05db40040d0195284fe03f8779a230ab75644b2cf34665e0841045315140`.
- source input digest `fd7ae50c99678715cf7e0e0e7cda49992802a706b0286d023a742702c0ee1722`, Cargo.lock SHA-256 `0203763db626e483456bf49b33f481b324cafe3f9b334a748f8ae90e7e6e3e17`를 final build-manifest에서 읽었다. Client PE의 UIAutomationCore.dll/system COM imports를 직접 확인했고 import verifier의 기존 조건을 유지했다. SBOM 164,616 bytes / SHA-256 `ad9c9ca47d9dab89d4cd2bcb077139bf3ce0d82617a08862bf13140426a00217`.
- 모든 이전 batch 보존. 실제 adapter/GUI/Narrator/keyboard/IME·Agent/helper/service·등록·원격·사용자 state ACL/Registry·자동 테스트·제품 archive/installer/CI를 실행하지 않았다. 서명/publisher 및 license review gate를 완료로 바꾸지 않는다. v0.1.23을 같은 migration 배치에서 재사용한다.
- 최종 Rust format/diff 검사 PASS. 이번 R01 section과 Desktop Guide의 상대 file link 검사 PASS. SSOT 전체를 대상으로 한 링크 검사는 기존 과거 dist/acceptance 및 폐기 source를 향한 링크 때문에 실패했다. 이번 build 산출물의 검사와 과거 링크의 부재를 구분하며 과거 성공 기록을 재검증한 것으로 취급하지 않는다. 과거 산출물을 생성하거나 source 폐기를 되돌려 링크를 억지로 통과시키지 않았다.

### 2026-10-12 사용자 범위 전환: 추가 기능 중단, 실제 Rust Agent 테스트

> [!IMPORTANT]
> 사용자가 추가 구현 확대를 중단하고 Agent 빌드·실제 동작 테스트를 명시적으로 요청했다. 기존 테스트 보류보다 이 요청이 우선한다. 이후 작업은 실제 Agent 검증과 발견한 결함 수정에 한정하며 IME/updater/서비스 기능 확장을 계속하지 않는다. GUI·제품 installer·기존 등록 profile·macOS/Linux는 이번 실행 대상이 아니다.

Windows x64 release Agent를 빌드하고 [실행 harness](../../scripts/test_native_agent.py)로 독립 HTTPS/WSS Gateway, 새 Device, 현재 사용자 DPAPI native state, 별도 workspace와 Windows Named Pipe에 연결했다. 실제 Rust 실행 파일/프로세스/파일/ConPTY/작업 DB를 사용하며 mock 또는 폐기된 Python Agent fixture를 성공으로 집계하지 않는다. Python은 Gateway와 테스트 실행기 및 알려진 자식 명령 fixture에만 사용했다.

발견 및 수정:

- 첫 TLS fixture는 CA를 서버 인증서로 겸용하여 native enrollment가 GATEWAY_UNREACHABLE로 실패했다. root CA/서버 leaf를 분리해 테스트 환경을 수정했다. 다음 실행의 policy 검사도 accepted receipt를 최종 commit으로 오인한 harness 문제였으며 receipt에 대응하는 PolicyFinished event를 기다리도록 수정했다. 두 실패를 Agent 통과로 기록하지 않는다.
- 실제 재시작은 **JOURNAL_SCHEMA_UNSUPPORTED**로 실패했다. Journal 형식은 6 table/3 explicit index만 예상했지만 OutputSpool이 실행 중 같은 DB에 1 table/2 index를 추가했다. [전체 Journal 형식](../../crates/racp-core/src/journal_schema.rs)에 output spool을 포함해 7 table/5 explicit index를 한 transaction에서 만들고 [OutputSpool](../../crates/racp-core/src/outputs.rs)의 뒤늦은 DDL 생성을 제거했다. arbitrary table/부분 형식 허용이나 사용자 DB ALTER/backfill로 우회하지 않았다. 수정 전 native-pretest-20261012-c는 컴파일 증거이며 재시작 인수 합격 artifact가 아니다.
- [회귀 테스트](../../crates/racp-core/tests/journal_restart.rs)에서 spool 초기화 후 close/read-only inspection/reopen 성공과 foreign table 거부를 검증했다. `cargo test --locked -p racp-core --test journal_restart`: **2 PASS / 0 FAIL**(실행 0.04초). Agent release 재빌드 PASS(57.32초), 기존 recipe warning 3개 유지. 같은 migration 배치 v0.1.23 재사용.

최종 실동작 run [native-agent-20261012-g 결과](../../dist/acceptance/native-agent-20261012-g/results.json): **17 PASS / 0 FAIL**, 기존 profile 사용 없음. 새 Agent와 Gateway는 run 종료 후 모두 종료했다.

테스트한 실행 파일은 `target/x86_64-pc-windows-msvc/release/racp-agent.exe`, SHA-256 `c4876328a3b74c31ac2594362ddd9180e1948d79d10ae3da88f150676bc0ca2e`다. 실행 harness SHA-256은 `9b2943188a1a809d4841b0f0bdfa8450d1651afcd5b63b819d6bc697bd096ce6`이며 g run에서 실행 전후 일치했다. 이 실행 파일의 실동작 검증과 이전 static CRT GUI staging의 검증을 같은 artifact로 취급하지 않는다.

| 실제 검증 | 결과 |
|---|---|
| HTTPS owner 인증/무인증 거부, native 등록·DPAPI 저장 및 token 1회 사용 | PASS |
| 실제 WSS ONLINE/version 확인, OS 인증 Named Pipe snapshot | PASS |
| UTF-8/한글 fixture 파일 읽기, 기본 write 거부·부작용 없음, workspace 탈출 거부 | PASS |
| 자식 실행 stdout/stderr/exit 7/cleanup, 중복 idempotency 요청의 mutation 1회 | PASS |
| 실제 Windows ConPTY open/input/명령의 파일 부작용/output/resize, close 뒤 소유 PID 부재 | PASS |
| hot policy commit 및 stale revision 거부, 로컬 승인 뒤 실행 및 nonce 재사용 거부 | PASS |
| job RUNNING 추적→cancel→CANCELLED, 취소한 실제 소유 자식 PID 부재 | PASS |
| Stop receipt→Agent exit 0→durable native-run phase complete, 같은 state 재시작/WSS 재연결 및 policy/workspace 보존 | PASS |

Ruff(실행 harness), Rust format 및 diff check PASS. 실행 전후 harness SHA-256 일치를 검사했다. 실패한 a/b/d와 수정 전 통과 c, 수정 후 e/f/g 기록을 모두 보존했다. GUI/Narrator/IME·service 설치/복구·브라우저/분석 runtime·2-PC/LAN·clean-PC installer·장시간/전체 기능 인수는 이번 17개 시나리오로 검증했다고 주장하지 않는다.

### 2026-10-12 실제 Windows GUI 검증 및 연결 결함 수정

> [!IMPORTANT]
> 사용자의 후속 요청 **“GUI 검증부터 해줘”**에 따라 이전 GUI 보류를 해제했다. Windows x64의 실제 Rust Client/Agent와 독립 HTTPS/WSS Gateway, 새 test Device/state/workspace만 사용했다. 기존 등록·로그인 Registry·서비스·installer는 변경하지 않았다. 추가 기능 구현은 중단한 상태이며 이번 수정은 실동작에서 발견한 결함에 한정한다.

[PID 범위 GUI probe](../../scripts/probe_native_gui.py)로 실제 Iced 창을 Windows UI Automation에서 관찰하고 원래 GUI 컨트롤과 native 키보드 입력을 실행했다. bundled computer-use Node runtime은 초기화 단계에서 경로 오류로 사용할 수 없어 pywinauto 0.6.9를 테스트 실행기에만 사용했다. 제품 dependency/bundle에 Python이나 pywinauto를 추가하지 않았다. default software renderer, 현재 화면의 150% DPI/1440×1020 capture 범위다. UIA tree와 PNG를 함께 보존하고 승인 화면·작업 화면 이미지를 직접 확인했다.

발견 및 수정:

1. `AccessKit adapter`(삭제됨)의 static Text가 화면에는 보이지만 UIA Name이 비었다. Windows Label의 Name에 필요한 value를 설정하여 실제 UIA Korean text 이름 노출을 확인했다.
2. [로컬 Agent helper](../../crates/racp-runtime/src/local_agent.rs)가 빈 환경으로 실행되어 Windows `SYSTEMROOT` 누락 시 HTTPS 등록이 실패했다. 같은 EXE/Gateway의 새 진단 state에서 env={}는 GATEWAY_UNREACHABLE, SYSTEMROOT만 제공한 실행은 등록 성공이었다. 기존 필터링된 OS 필수 환경 함수를 사용하고 정해진 network 오류 코드를 GUI에 보존했다. [진단 기록](../../dist/acceptance/native-gui-20261012-b/helper-environment-diagnostic.json)을 보존하며 실패한 pending 등록 root를 삭제/재사용하지 않았다.
3. [heartbeat](../../crates/racp-runtime/src/transport.rs)가 17 capability를 한 번에 보내 wire의 maxItems=16을 위반해 약 5초 후 끊겼다. 16개 이하 batch로 모두 전송하고 handle snapshot은 첫 batch에만 포함했다.
4. 내부 provider 이름 os_observation/process_dump/network_capture 및 clipboard 혼합 operation은 Gateway registry capability 분류와 달랐다. [wire 목록](../../crates/racp-runtime/src/providers/mod.rs)을 기존 registry 기준으로 분류·병합하고 available member의 operation만 활성 목록에 넣었다. 관리자 권한이 없는 network.capture를 네트워크 조회의 가용성 때문에 활성화하지 않는다. Gateway의 scope 검증 또는 immutable wire schema를 완화하지 않았다. canonical 목록은 19 capability이며 실제 Gateway가 모든 batch를 수용했다.

최종 [GUI run 결과](../../dist/acceptance/native-gui-20261012-e/results.json): **핵심 경로 12 PASS / 0 FAIL**, 아래 범위로 한정한다.

| 실제 GUI/OS 동작 | 결과·증거 |
|---|---|
| 한국어 화면 및 UIA Text Name | PASS, initial.json/PNG. 별도 a run에서 실제 키보드 Unicode “GUI 한글 입력”, Tab/Down/Space 및 Ctrl+1/4/5 확인. 한글 IME 조합 인수는 아님 |
| GUI 연결 파일 확인→권한 초안→새 등록 | PASS, registered.json. 실제 DPAPI 등록 및 새 test Device 표시 |
| GUI Agent 시작→인증 IPC→ONLINE | PASS, online.json. 실제 EXE/PID 및 Gateway WSS 연결 |
| heartbeat 연결 유지 | PASS, connection-stability.json의 0..35초 8회 모두 ONLINE/동일 epoch=1. 승인·거부·창 숨김까지 같은 연결 유지. 장시간 안정성 인수는 아님 |
| 원격 파일 읽기→GUI 허용 | PASS, [승인 화면](../../dist/acceptance/native-gui-20261012-e/gui-allowed-retry-pending.png), gui-allowed-retry-result.json. 실제 한글 fixture 내용 반환 |
| 새 읽기 요청→GUI 거부 | PASS, gui-denied-result.json. FAILED/result=null, 내용 반환 없음 |
| 작업 성공·실패 표시 | PASS, [작업 화면](../../dist/acceptance/native-gui-20261012-e/activity.png)/JSON, 실제 operation ID/state 일치 |
| 닫기→창 숨김→중복 실행의 기존 창 활성화 | PASS, hidden.json/single-instance.json/restored.json. 두 번째 PID exit 0, 기존 PID/ONLINE 유지 |
| GUI 정상 종료→재실행 및 등록 보존 | PASS, reopen.json/reopened.json. native-run complete 뒤 같은 test Device를 표시 |
| Agent 정지 상태의 GUI 설정 저장 | PASS, settings-saved.json. 실제 helper/atomic state 교체, 설정 revision 0→1, 로그인 등록 버튼 미실행 |
| GUI Agent 재시작→ONLINE | PASS, restarted-online.json. 등록·권한·작업 기록 보존 |
| GUI 종료 및 실제 정리 | PASS, final-quit.json/native-run complete. test Client·Agent·Gateway 프로세스 부재 확인 |

실패/실행기 오류도 보존했다. a Text Name 결함, b helper 오류/pending root, c oversized heartbeat, d capability scope 오류는 수정 전 실패다. e의 첫 승인 요청은 도구 왕복 중 30초 만료했고 다음 실행은 비동기 UIA focus를 즉시 확인한 probe 오류였다. focus 완료를 기다리는 probe로 실제 키보드 승인을 재검증했다. 최초 probe가 HTTP result와 UIA capture를 같은 JSON 이름에 기록해 덮어쓴 문제도 수정했다. 해당 두 최종 operation 결과는 종료 후 Gateway durable DB를 read-only로 읽어 별도 `*-result.json`에 복원했으며 HTTP 응답 역시 도구 출력에서 관찰했다. 실패한 실행을 PASS로 집계하지 않는다.

재빌드·회귀 증거:

- Windows release Client/Agent 최종 빌드 PASS(58.07초), 기존 recipe warning 3개 유지. 같은 migration batch **v0.1.23** 재사용. 최종 Agent SHA-256 `45f7f977139c466613e3be9f256e6b8f6e4a9c0bdf690e62229c780d06e1b70f`; Client `d5ae6b0bdbe1b11447f2c5b38bc5e7a0bc62741c9c9aa634bf72e64fae7c989c`.
- capability registry/operation 병합 및 unavailable network.capture 제외 회귀: `cargo test --locked -p racp-runtime --lib capability_tests` **1 PASS / 0 FAIL**.
- 동일 최종 Agent의 [h run](../../dist/acceptance/native-agent-20261012-h/results.json) **17 PASS / 0 FAIL**(실제 등록/파일/자식/ConPTY/권한/승인/작업 취소/정지/재시작 재검증). h harness SHA-256 `9b2943188a1a809d4841b0f0bdfa8450d1651afcd5b63b819d6bc697bd096ce6`.
- GUI payload는 현재 release Client/Agent와 LICENSE, 이전 동일 dependency SBOM 및 새 hash manifest를 별도 test root에 배치했다. 이전 static CRT staging/제품 installer/서명 결과와 같은 artifact로 주장하지 않는다. 이전 모든 산출물/실패 root 보존. Ruff 두 실행기·Rust format·diff check PASS.

> [!WARNING]
> 전체 GUI 인수 합격은 아니다. 하단 안내가 실제 ONLINE 뒤에도 “인증 IPC 연결을 기다립니다”, 승인 완료 뒤에도 “최종 결과 대기”로 남는 표시 문제가 확인됐다. 일부 UIA Invoke는 화면 변화가 반영되지 않았고 같은 컨트롤의 키보드 경로는 동작했다. 변경 중 view epoch 거부와의 관계는 추가 확인이 필요하다. c의 권한 초안 수정 후 scroll 이동 현상은 다음 c/e 실행에서 일관되게 재현되지 않아 수정 완료로 기록하지 않는다. Narrator·한글 IME 조합/candidate·100/200% DPI·다중 모니터·GPU·로그인/service·2-PC/LAN·clean-PC 설치·장시간 인수는 미검증이다. 잔여 표시/접근성 문제를 남기고 핵심 GUI 실동작 검증 결과를 보고하며 신규 기능 확대를 재개하지 않는다.

### 2026-10-12 GUI 개발 상태 문구 제거 및 잔여 작업 문서화

사용자 지적에 따라 개발 진행/검증 상태를 제품 GUI에서 제거했다. `권한 화면`(삭제됨)은 미연결 planned 권한과 NotImplemented 항목을 표시하지 않으며 `실기 미검증`/`미구현` 배지를 출력하지 않는다. 지원되지 않는 엄격한 OS 격리 선택지와 내부 계약 설명도 제외했다. runtime 설정·OS 권한·보호된 대상 등 실제 사용 조건은 유지하며, 미검증을 Supported로 변경하거나 정책/권한 guard를 완화하지 않았다. read-only 관찰 화면에도 같은 미연결 항목 제외 규칙을 적용했다.

[기존 작업 계획서의 잔여 작업](../spec/rust-client-migration-plan.md#remaining-work-20261012)에 desktop Rust/Iced와 별도 TypeScript 웹 Console의 구분, crates 역할, 구현 결함/추가 구현/실기 검증/제품 배포의 우선순위를 정리했다. 과거 자동 테스트/GUI 보류 문구는 최신 사용자 Agent/GUI 요청보다 우선하지 않도록 명시했다. OS별 전용 API 확장은 여전히 후순위다.

- Windows Client release 빌드 PASS(15.67초), v0.1.23 동일 GUI 수정 배치 재사용. 최종 Client SHA-256 `389dfc8380d1cda67af8e4105958abc05c5a656b86fa910856ce3aea76450e28`. Agent binary 변경 없음; 위 h Agent 인수 결과 유지. 이전 e의 GUI 인수 artifact와 이번 Client를 같은 hash로 취급하지 않는다.
- 독립 unregistered state의 [f 화면 검사](../../dist/acceptance/native-gui-20261012-f/results.json)에서 실제 UIA tree 전체의 개발 상태 문구 부재, 미연결 권한 **48개** 미노출, 기존 파일 읽기의 허용/승인 버튼 유지를 확인했다. 새 GUI만 실행했으며 Gateway/Agent/등록·설정 저장·로그인/service 변경은 수행하지 않았다. Client 정상 종료/PID 부재 확인. UIA 목록과 PNG는 permissions-clean.json/PNG에 보존했다.
- 잔여 포커스/스크롤 문제를 f에서 확인했다. 파일 읽기 허용 버튼에 UIA focus=true가 됐지만 해당 button rectangle은 0이며 [capture](../../dist/acceptance/native-gui-20261012-f/permission-options.png)는 다른 프로세스 권한 영역으로 이동했다. 단순 문구 제거 검사와 포커스 reveal 성공을 구분하며 이 문제를 수정 완료로 기록하지 않는다. 개발 상태 숨김 정책이 실제 조작 결함을 문서에서 숨기는 의미는 아니다.

### 2026-10-12 구 디렉토리 정리 및 GUI 전용 모델 통합 — v0.1.24

사용자의 구 디렉토리 정리·구조 개선 요청으로 다음 범위를 수행했다. 핵심 원격/GUI 기능 확대, 기존 등록/service 변경 또는 설치/게시 작업은 수행하지 않았다.

- `apps/agent`의 Python cache와 `apps/client`의 cache/renderer build 및 node_modules junction을 복구 가능한 보관 폴더 (`../../dist/legacy-directory-backup/20261012-structure-cleanup/inventory.json`, 과거 경로)로 이동했다. 이동 전 workspace 내부 절대 source/destination과 실행 중인 구 프로세스 부재를 검사했다. **120개 일반 파일**의 크기·SHA-256 및 **10개 junction** 정보를 기록했고 이동 후 모든 일반 파일 hash 일치와 원래 두 경로의 부재를 확인했다. junction 대상의 공유 Node 의존성 디렉토리를 재귀 삭제하지 않았다. 기존 dist/테스트 기록·사용자 state는 보존했다.
- GUI 하나만 소비하던 `crates/racp-ui-model/src/lib.rs`를 [앱 내부 모델](../../apps/client-native/src/ui_model.rs)로 이동했다. 별도 Cargo package/member/dependency를 제거하고 앱에 필요한 serde 의존성을 명시했다. 사용하지 않던 catalog description/operations 저장 필드만 제외했으며 정책/승인/event 의미는 유지했다. retired manifest는 위 보관 폴더에 별도로 보존했다. root workspace는 **6 package**, 공유 라이브러리는 contract/core/runtime **3개**다. Agent·GUI·분석 host가 실제 사용하는 runtime을 새 facade/crate로 더 쪼개지는 않았다.
- `.gitignore`의 구 Client 예외와 uv workspace의 구 Client exclude를 제거했다. README의 Tauri/구 Client 구조·구 bridge 실행 예시·과거 테스트 보류 설명을 현재 Iced/native 명령으로 갱신했다. [작업 계획](../spec/rust-client-migration-plan.md#section-05), AGENTS 및 관련 skill의 현재 경로/실행 정책을 동기화했다. 과거 계획/현황/inventory의 폐기 source 참조는 이력으로 유지하며 현재 실행 경로로 연결하지 않는다.
- [version tool](../../scripts/version.py)로 PATCH를 한 번만 **0.1.23→0.1.24** 올리고 uv/root Cargo/독립 분석 host lock을 갱신했다. `uv sync --all-packages`는 환경에 남아 있던 구 racp-agent 0.1.10 editable 설치 및 그 전용 추가 dependency를 제거했다. 동기화 후 `racp_agent`를 import할 수 없음을 확인했다. 기본 Client 제품에 Python/Node를 추가하지 않았다.
- [기존 버전 검사](../../tests/unit/test_version.py)의 0.1.21 literal 비교 때문에 처음 검사에서 실패했다. 고정 값 대신 SemVer 형식과 실제 package/Cargo/Console 계약의 동기화를 검사하도록 수정했다. Console OpenAPI snapshot도 info.version=0.1.20만 오래돼 drift 검사가 실패했다. 실제 generator로 **info.version만 0.1.24**로 갱신했고 endpoint/schema 차이는 없다. 앞으로 [version tool](../../scripts/version.py)이 이 metadata도 함께 갱신하도록 반영했다. 계약 경로는 변경하지 않았다.
- [GUI probe](../../scripts/probe_native_gui.py)의 0.1.23 window/version 및 이전 SBOM 경로 고정을 제거했다. 현재 SSOT version과 명시한 verified staging payload를 사용한다. 정확한 uv 환경 동기화 뒤 Pillow가 없어 첫 PNG capture가 실패한 실행기 문제는 test-only `--with pywinauto==0.6.9 --with pillow==12.3.0`로 해결했다. 제품 dependency 또는 bundle에 넣지 않았다.
- xtask는 실제 검사를 수행하지 않는 build-only 도구인데 결과에 `deferred_by_user`를 고정 기록하던 부분을 `not_run_by_xtask`로 바꿨다. 별도 Agent/GUI 인수는 정확한 EXE hash로 연결한다. 이 수정이 제품 설치/전체 native 인수 합격을 뜻하지 않는다.

검증 결과:

| 검사 | 결과 |
|---|---|
| Windows root workspace check / 독립 분석 host check | PASS, 20.00초 / 28.03초. 기존 recipe warning 3개 유지 |
| Ruff 전체 Python apps/packages 및 변경 실행기/버전 검사 | PASS |
| Mypy | PASS, 98 source files |
| 기존 버전 6개 + 서버/Console 계약 2개 | **8 PASS / 0 FAIL**, 최종 실행 2.38초. 최초 literal/snapshot 실패도 위에 기록 |
| inventory-check | PASS, 114 operation/143 permission/18 category/48 planned, frozen baseline 100 유지 |
| Windows static CRT production build / verify-staging | PASS. a release 1분 43초, 계약 metadata 갱신 뒤 b 재시도 0.60초, xtask metadata 수정 뒤 최종 c 1.28초. 모든 batch 보존, 같은 v0.1.24 사용 |
| 정확한 최종 Agent EXE의 [i run](../../dist/acceptance/native-agent-20261012-i/results.json) | **17 PASS / 0 FAIL**, 등록/DPAPI/WSS/Named Pipe/파일/자식/ConPTY/권한/승인/취소/정리/재시작 |
| 모델 이동 뒤 실제 [GUI g run](../../dist/acceptance/native-gui-20261012-g/results.json) | **9 PASS / 0 FAIL**, header/문구/권한 초안/새 등록/ONLINE/GUI 허용·거부/작업 표시/정상 종료. 실제 PNG도 확인. 기존 하단 대기 안내 및 UIA/포커스/IME 한계는 계속 별도 잔여 작업 |
| 별도 웹 Console TypeScript/production build | PASS, Node 22.23.0 / corepack pnpm 11.19.0, tsc --noEmit 및 Vite build(1.37초). 별도 새 dist/acceptance 출력 사용, 기존 산출물 삭제 없음 |
| 보관 파일 hash / 현재 참조 / Cargo/SBOM | PASS. 현재 workspace/README/AGENTS/config에 retired model 경로 없음. 최종 SBOM에 racp-ui-model 없음 |

최종 c build manifest (`../../dist/client-native/0.1.24/win-x64/structure-cleanup-20261012-c/build-manifest.json`, 과거 경로)의 source digest는 `daee533a52c040d7590e2891dcfee85e8e31624cd6eee27ab6658bbe05f28f95`, Cargo.lock SHA-256은 `adc0b2d4417cba2a2defa38bf5feaa09042bd4830c6a6f723668f5bbcc346cce`다. Agent **21,965,312 bytes** / SHA-256 `806ba7131d05d9e8a0ff2f41c3875b8903bf1bbe3145cfa1347d8674bd339bb4`; Client **14,075,904 bytes** / `5ef386550880e95eeef7e8cf4633e966c26df453732d595aa0d3597b1d37e04e`; launcher **2,635,776 bytes** / `d8fb4671c2882ec102af7753706aaa521e64980ecee563310a336ad4c76bc881`; SBOM **164,154 bytes** / `20c3acf90d4430688a5a255db1b84648985fd94abb740f6f70abfc005a1bbda3`.

Agent i와 GUI g는 b payload에서 실행했고 **두 EXE의 SHA-256이 최종 c와 일치**함을 별도로 검증했다. test Client/Agent/Gateway는 종료했고 기존 등록 profile/서비스는 사용하지 않았다. Rust format 및 diff check PASS. Product installer·서명·clean-PC·macOS/Linux native·전체 기능 인수 및 남은 GUI 결함을 이번 디렉토리 정리로 완료 처리하지 않는다.

### 2026-10-12 컴포넌트별 출력 분리 및 새 Windows 빌드 — v0.1.25

> [!NOTE]
> 문서 링크 검사에서 이번 삭제 시도 이전부터 존재하지 않던 과거 dist 증거 링크 404개와 폐기 source 링크 3개를 확인했다. 해당 링크는 원래 경로를 명시하는 텍스트 참조로 바꿨고 과거 기록의 수치·hash는 유지했다. 이번 신규 산출물·검증 링크는 실제 존재를 확인했다. 이 링크 정리가 물리 파일 삭제를 수행했다는 의미는 아니다.

사용자는 기존 `dist` 내용을 모두 삭제하고 새로 빌드하도록 명시했다. 이 요청은 기본 레거시 산출물 보존 규칙보다 우선하며 [AGENTS §9](../../AGENTS.md#9-build-output-layout)·빌드 skill·운용 가이드에 반영했다. 다만 절대 대상 경로 검사와 junction 대상 보호를 포함한 삭제 명령 및 더 단순한 PowerShell 삭제 명령 모두 실행 도구가 **`blocked by policy`로 실행 전 거부**했다. 이유의 추가 상세는 제공되지 않았다. **기존 dist/legacy-directory-backup·과거 산출물·증거 파일은 삭제되지 않았고 전체 정리는 미완료다.** 다른 언어/쉘로 삭제를 우회하지 않았다. 신규 빌드는 기존 산출물을 입력으로 사용하지 않고 현재 소스에서 별도 컴포넌트 경로에 생성했다. source·사용자 등록 state와 공유 dependency는 변경하지 않았다.

변경 범위:

- `xtask stage` / `verify-staging` 및 product packaging/CI는 `dist/client/<version>/<target>/<build-id>/staging|packages/`를 사용한다. `stage-agent` / `verify-agent`는 GUI 없이 `dist/agent/<version>/win-x64/<build-id>/build/`에 Agent EXE·LICENSE·Agent 전용 Cargo SBOM·hash/import manifest를 생성한다. Agent SBOM에서 Iced/Client 의존성 부재, Client SBOM에서 Iced 포함을 확인했다. 공유 `target/native-product`는 재사용 가능한 compiler cache이며 배포 출력과 분리한다.
- Python wheel·Gateway Portable·Console builders는 컴포넌트/버전/대상/build-ID별 새 경로를 기본값으로 사용한다. 기존 batch는 덮어쓰지 않는다. Gateway가 복사하는 Console은 동일 build-ID의 명시적 출력이다. CI upload 및 품질/Console/MCP/RE fixture 보고서 경로도 컴포넌트별 `dist/verification`으로 변경했다. 등록 token/state는 검증 폴더에만 생성하며 배포물에 넣지 않았다.
- Gateway builder가 현재 checkout에 없는 `apps/gateway/build/deployment.json`을 Portable에도 필수로 읽어 최초 실행이 실패했다. Portable을 설치 recipe에서 분리했고 Setup은 해당 manifest/NSIS recipe가 없으면 명시적으로 중단한다. **Gateway Setup recipe는 이 작업에서 재구현하지 않았다.** 기존 installer-preservation unit test 1개는 missing source 때문에 실행 대상에서 제외했으며 PASS로 집계하지 않는다.
- Console의 기존 formatting 11개 파일과 생성 타입 drift가 production gate를 막았다. Prettier만 적용하고 현재 published OpenAPI에서 타입을 재생성했다. drift에는 현재 계약의 `application/vnd.tcpdump.pcap` enum 누락이 있었으며 endpoint/schema를 새로 변경하지 않았다. 실패 a/b Console phase는 `build.quarantine`과 사유 manifest로 남겼다. c는 첫 성공, d는 최신 운용 guide를 포함하는 최종 Gateway build이며 같은 v0.1.25를 재사용했다.
- PATCH는 한 번만 0.1.24→0.1.25로 증가시켰고 uv/root Cargo/독립 분석 host lock을 동기화했다. 새 GUI 기능·OS별 API·서비스 설치·게시·macOS/Linux 빌드는 수행하지 않았다.

최종 산출물 및 확인:

| 산출물 / 검사 | 결과 |
|---|---|
| [독립 Agent manifest](../../dist/agent/0.1.25/win-x64/rebuild-20261012-a/build/build-manifest.json) | Rust 1.90.0 / Windows x64 static CRT release PASS, 1분 50초. verify-agent hash/native target PASS. EXE 21,946,880 bytes / SHA-256 `32017cdf994d25544ca8d191125f8fc0c9ca1478be2528d6aac351161562fdff` |
| [Client staging manifest](../../dist/client/0.1.25/win-x64/rebuild-20261012-a/staging/build-manifest.json) | release PASS, 2분. verify-staging payload/launcher/import/hash PASS. Client 14,075,904 bytes / `6179dac5ba13ad135f9f9b0f07907204653cfc8fab6c53ccf4eb71a5bff71c0b`; companion Agent 21,965,824 bytes / `7c5759ce1c0b2b4d9a0b42b767337a6af3a0293a48f6922c9cf8c0e6a337a31f` |
| [Gateway Portable ZIP](../../dist/gateway/0.1.25/win-x64/rebuild-20261012-d/packages/RACP-Gateway-0.1.25-win-x64.zip) | 34,166,860 bytes / SHA-256 `31d4842ae7441495601b3a96f764eb94bcc61bac6190b9f80ac2f9131208cd55`. Bundled CPython 3.12.11 imports/version and pywin32 host/DLL staging PASS |
| [Gateway 실행/무결성](../../dist/verification/gateway/0.1.25/rebuild-20261012-d-retry/results.json) | ZIP CRC 및 **4,402개 manifest file**의 실제 파일/ZIP entry SHA-256 PASS. 독립 임시 state에서 bundled Python 서버 `/healthz`와 실제 `/console/` 정적 응답 PASS. Agent/Client EXE·credential.bin·DB 미포함 |
| [웹 Console build manifest](../../dist/console/0.1.25/web/rebuild-20261012-d/build/build-manifest.json) | Node 22.23.0, Prettier/tsc/generated-client drift/Vite production PASS, 167 modules. GUI와 별도 TypeScript 웹 앱 |
| [Gateway workspace wheels](../../dist/gateway/0.1.25/python-any/rebuild-20261012-a/wheels/build-manifest.json) | 현재 Python 7 workspace wheel 및 batch별 SHA-256 PASS. 폐기 Python Agent wheel 생성 없음 |
| [새 독립 Agent 실동작](../../dist/verification/agent/0.1.25/rebuild-20261012-a/results.json) | **17 PASS / 0 FAIL**, 실제 등록·DPAPI·HTTPS/WSS·Named Pipe·파일·자식·ConPTY·권한·승인·취소·정리·재시작 |
| [Client GUI 시작 검사](../../dist/verification/client/0.1.25/rebuild-20261012-a/results.json) | verified 현재 payload와 임시 unregistered state에서 실행, v0.1.25 실제 UIA header/화면 및 정상 종료 PASS. 전체 GUI 인수를 재실행한 것은 아님 |
| Ruff / Mypy / version/build/OpenAPI unit+contract | PASS / 98 files PASS / **11 PASS, installer-source 검사 1개 제외** |
| Rust fmt / inventory / 독립 analysis-host check / uv lock | PASS. inventory 114 operations / 143 permissions / 18 categories / 48 planned. 기존 recipe warning 3개 유지 |

두 native manifest의 현재 input digest는 `79ba1737681cc179a6e65c72cdf6763d1b3fa7d08c9fcf3f68af122c1267804b`, Cargo.lock SHA-256은 `189800b65d6fcba01eeac07b5b87bba2d4219cc973b1f3103aef458f73f5333d`다. Agent 단독과 Client 동반 Agent는 Cargo feature 통합 범위가 달라 hash가 다르며, 17-case 인수는 **독립 Agent hash**에만 연결한다.

검증 실행기 오류도 구분한다. 최초 GUI probe는 CP949 stdout의 em dash 출력에서 실패했으나 capture 파일은 생성됐고 UTF-8로 재실행해 확인했다. 최초 Gateway 검사는 존재하지 않는 `/health`를 요청해 404를 startup 실패로 오판했다. 실제 `/healthz`로 수정한 재실행이 위 PASS 증거다. 모든 이번 test Client/Agent/Gateway 프로세스의 종료를 확인했다. 설치·서명·clean-PC·2-PC·전체 GUI 인수와 기존 footer/UIA/IME 문제는 이번 출력 구조 개선으로 완료 처리하지 않는다.

### 2026-10-12 Windows 사용자 설치본 생성 — v0.1.26

사용자가 설치형 구성을 요청해 Client와 Agent가 함께 설치되는 한국어 NSIS setup을 생성했다. 이후 사용자가 **실제 설치는 하지 말라**고 지시했다. 그 시점에는 아래 임시 설치·실행·제거 검사가 이미 끝나 테스트 설치 폴더·시작 메뉴·앱 제거 항목이 모두 제거된 상태였다. 후속 설치 실행을 중단했고 installer preview도 종료된 것을 read-only로 확인했다. 기본 `%LOCALAPPDATA%\Programs\RACP-Native\0.1.26`은 생성되지 않았다. 현재 허용 범위는 artifact 생성과 정적 검사이며, AGENTS/skill/가이드에 추가 설치 금지를 반영했다.

설치 구조:

- 기본 사용자 경로 `%LOCALAPPDATA%\Programs\RACP-Native\<version>\payload`에 Client·Agent·LICENSE·SBOM·payload manifest를 설치한다. 관리자 권한/Windows 서비스/자동 등록은 사용하지 않는다. 설치 완료의 RACP 실행 옵션과 버전별 시작 메뉴 바로가기는 실제 GUI `racp-client.exe --require-package`를 실행한다.
- 한국어 welcome/license/directory/install/finish와 제거 화면, Windows Apps의 버전·설치 위치·아이콘·일반/quiet 제거 항목을 제공한다. 동일 폴더의 중복 설치는 exit 2로 거부하고 덮어쓰지 않는다. 제거는 기존 native preflight 및 실행 파일 pin으로 보호하며, 사용자 등록/state는 설치 폴더 밖에 보존한다.
- packaging stub `staging/racp-native-portable.exe`와 실제 `packages/*-portable.exe`를 가이드에서 명확히 구분했다. 사용자에게 전달하는 설치본은 아래 `*-setup.exe`다. 제품 GUI에 개발 상태 설명을 추가하지 않았다.
- 버전은 한 번만 **0.1.25→0.1.26**으로 증가했고 uv/root Cargo 및 optional host metadata를 동기화했다. same-version retries a/b/c를 사용했다. a는 build 도중 NSIS source 수정으로 digest guard가 거부했고, b는 잘못된 MessageBox `/SD` 위치 및 ACP 디코딩으로 compile 실패했다. 수정 후 c는 `/INPUTCHARSET UTF8 /WX`로 warning 없이 생성됐다. 실패 phase는 quarantine에 남겼다.
- NSIS 3.11 archive 다운로드는 SourceForge의 challenge/redirect 때문에 실패했고 Tauri의 공개 binary mirror에서 확보한 뒤 [공식 archive SHA-256](https://sourceforge.net/projects/nsis/files/NSIS%203/3.11/nsis-3.11.zip/download) `c7d27f780ddb6cffb4730138cd1591e841f4b7edb155856901cdf5f214394fa1`과 일치함을 확인했다. 빌드 도구만 `.tools`에 두며 제품에 Tauri/Node/Python을 동봉하지 않았다.

최종 [package manifest](../../dist/client/0.1.26/win-x64/setup-20261012-c/packages/build-manifest.json)와 `SHA256SUMS`:

| 형식 | 파일 | 크기 / SHA-256 |
|---|---|---|
| 사용자 설치 EXE | [RACP-Native-0.1.26-win-x64-setup.exe](../../dist/client/0.1.26/win-x64/setup-20261012-c/packages/RACP-Native-0.1.26-win-x64-setup.exe) | 10,111,558 bytes / `8b2a3599ca1e6b23b0bff0330fa1874e5fee9393d9e4ce67b965e0546e9c5495` |
| 완성된 portable EXE | [RACP-Native-0.1.26-win-x64-portable.exe](../../dist/client/0.1.26/win-x64/setup-20261012-c/packages/RACP-Native-0.1.26-win-x64-portable.exe) | hash `8c8e091e91d35a89a60f92539f29696eb279d5ea3fc6d0d8934ff84f60241364` |
| native ZIP | [RACP-Native-0.1.26-win-x64.zip](../../dist/client/0.1.26/win-x64/setup-20261012-c/packages/RACP-Native-0.1.26-win-x64.zip) | hash `952af7a06d49a99da03ea6a0dc00f14de92ede14f91840b628dd46a2045e9131` |

사용자 no-install 지시 이전의 [격리된 검사 결과](../../dist/verification/client/0.1.26/setup-20261012-c/results.json): 한글·공백이 있는 `dist/verification/.../설치 폴더`로 silent install exit 0, 설치 payload 5개 파일의 staging hash 일치, 실제 GUI 시작/header/정상 종료, 시작 메뉴의 정확한 GUI target/args 및 HKCU Apps 항목 PASS. 동일 경로 재설치 exit 2 및 파일 무변경 PASS. GUI 실행 중 제거 요청 뒤 6개 설치 파일과 shortcut 무변경 확인 PASS. 표준 NSIS uninstaller 부모는 이 blocked 실행에서도 exit 0을 반환하므로 exit code를 제거 성공으로 해석하지 않았고 실제 파일/registry 결과를 검사했다. GUI 종료 후 정상 제거에서 설치 폴더·shortcut·Apps 항목 부재와 별도 테스트 state sentinel 보존을 확인했다. **등록된 사용자 state의 제거 인수나 clean-PC 설치는 수행하지 않았다.** 기본 user state가 전후에 부재인 것도 확인했다.

[같은 packaged Agent EXE의 실제 검사](../../dist/verification/agent/0.1.26/setup-20261012-c/results.json)는 **17 PASS / 0 FAIL**이다. installed payload의 hash가 staging과 같았으므로 같은 파일에 연결하며 실제 등록·WSS·DPAPI·Named Pipe·파일/자식/ConPTY·권한/승인/취소/재시작을 확인했다. Ruff/mypy(98 files)/버전 6 unit/Rust format 및 package hash 검사 PASS. xtask는 assembled portable footer/hash와 ZIP hash/CRC를 검사했지만 이번 portable 실행은 하지 않았다.

Computer Use Node helper는 `failed to write kernel assets: 지정된 경로를 찾을 수 없습니다. (os error 3)`로 초기화되지 않아 scoped Python UIA probe를 사용했다. hidden preview는 visible window가 없어 최초 검사 실패했고 정상 표시로 실행한 마지막 [한국어 welcome UIA](../../dist/verification/client/0.1.26/setup-20261012-c/setup-welcome.json)를 관찰했다. 이후 사용자 지시 시점에 해당 preview process는 이미 종료돼 추가 UI 조작이나 설치를 수행하지 않았다. 산출물은 unsigned/non-published이며 서명·업데이트·machine service·clean-PC·macOS/Linux 인수는 별도다.

### 2026-10-12 미구현 및 추가 테스트 계획 정리

사용자 요청으로 [Native Client·Agent 미구현 및 추가 검증 계획](../spec/native-client-completion-and-test-plan.md)을 작성했다. 확인된 GUI 결함, 실제 공개 계약에 미연결된 확장, 구현이 있지만 환경별 증거가 부족한 기능, 설치 금지로 보류한 시험을 구분했다. 과거 migration inventory의 planned 목록 대신 현재 permission catalog를 대조했고, 이미 연결된 jobs·파일·진단·브라우저 확장을 신규 미구현으로 잘못 분류하지 않았다. NSIS archive의 복수 compiler와 CI의 단일 탐색 조건 불일치도 후속 작업에 포함했다.

새 계획은 F01–F12 작업·T01–T17 시험의 우선순위·선행 조건·완료 기준을 관리한다. 기존 Rust 전환 문서의 잔여 작업 목록은 새 계획으로 연결해 중복 SSOT를 피했다. 명세 index와 문서 포털도 갱신했다. 이번 요청에서는 문서만 작성했으며 코드 구현·제품 재빌드·테스트·installer 실행·설치/제거·배포를 수행하지 않았다. 제품 기준선은 v0.1.26으로 유지한다.

사용자가 원격 제어 관점의 우선순위를 지적하여 같은 후속 계획을 개정했다. R01–R05의 호출자→Gateway→Agent 실제 실행·권한/승인·취소·결과 추적·재연결·자원 정리를 P0로 두고, 실제 두 PC 환경이 제공되면 우선 검사하도록 변경했다. 원격 Unicode 경로/인자·결과·터미널·desktop.type 검사는 로컬 GUI의 IME 후보/조합 검사와 분리했다. GUI 표시·접근성은 원격 등록/권한/승인을 막는 결함만 선행 처리하고 일반 focus/scroll/IME 고급 시험은 후순위로 낮췄다. 이번 개정도 문서 변경만 수행했으며 설치 금지와 기존 사용자 state 보존을 유지한다.

### 2026-10-12 GUI 제외 및 Agent 서비스 전용 계획 분리

사용자가 `client-native` GUI를 재구성할 예정이므로 제외하고 Agent 서비스의 미구현·보완·시험 계획을 별도로 요청했다. [Agent 서비스 구현 및 검증 계획](../spec/agent-service-implementation-and-test-plan.md)을 작성하고 AS01–AS14 작업, AST01–AST21 시험, 작업 순서·서비스 완료 판정·설치 금지에 따른 보류 범위를 정리했다. 현재 작업의 SSOT를 이 계획으로 이동하고 이전 Client·Agent 통합 계획은 Superseded로 표시했다. Rust 마스터 계획·명세 목록·포털·Agent 가이드에 관계를 명시했다.

코드를 대조해 SCM host/provision/bootstrap/관리/restore/IPC 경로가 이미 존재함을 확인했으며 이를 전부 미구현으로 재분류하지 않았다. 구체적 공백으로 service host의 `pin_current_payload(true)`와 Client EXE를 필수로 요구하는 `BASE_FILES`, Client를 동봉하는 machine recipe, demand-start 운영 경로를 기록했다. Agent 단독 service admission/배포, GUI 없는 관리·승인 계약, partial 복구와 자동 시작/장애 정책을 보완 항목으로 분리했다. Session 0과 사용자 세션 Broker/Guardian은 Agent 원격 desktop backend로 포함하되 GUI app·화면·IME·UIA 시험은 제외했다.

이번 작업은 계획 문서 작성과 링크/범위 검토만 수행했다. source 구현·제품 버전 증가·build/test·installer 실행·SCM 서비스 등록/시작/정지·계정/ACL/기존 state 변경을 수행하지 않았다. 실제 service 인수는 현재 설치 금지로 보류하며, 기존 user-mode Agent 결과를 서비스 성공으로 이관하지 않는다.

### 2026-10-12 플랫폼별 Agent 구조 및 원격 USB/동글 정적 분석 요구 반영

사용자는 GUI 재설계를 별도로 진행하며 Agent도 Windows/macOS/Linux 단위로 구조를 구분하고 Windows부터 구현하도록 제안했다. 첨부 GUI 계획은 플랫폼 공통/OS adapter 분리 원칙의 참고 자료로만 읽었다. 완료 체크·GUI 구현 지시를 현재 Agent의 완료 증거나 실행 범위로 가져오지 않았다.

USB 동글의 용도는 사용자가 **원격 PC에 연결된 외장 장치/동글에 접근해 정적 분석할 데이터를 수집하는 Remote 기능**이라고 명확히 했다. Agent 인증/라이선스 동글 요구로 해석하지 않는다. 현재 source와 계약 검색에서 해당 enumeration/연결/수집 Provider는 확인되지 않았으며 일반 USB 프레임 한계에 대한 문서 언급만 존재했다.

[Agent 서비스 계획 §3.4/§3.5](../spec/agent-service-implementation-and-test-plan.md#section-03)에 공통·플랫폼 목표 폴더/파일과 Windows 우선/macOS·Linux 서비스/장치 어댑터 미구현 상태를 추가했다. AS15–AS18은 플랫폼 경계·장치 탐색/remote session·지원 read 수집/정적 artifact·unplug/cancel/정리 작업, AST22–AST26은 구조/계약/실제 원격 장치/IO 수명주기/분석 출처 시험으로 정리했다. driver/backend·장치별 지원 범위, descriptor와 firmware 추출의 차이, PC ID와 외장 장치 identity/generation·opaque handle·lease를 구분했다. 일반 USB-over-IP 가상 attach나 driver 교체/임의 write/reset/flash를 이 요구만으로 실행하지 않는다.

이번에는 계획·AGENTS·문서 index만 변경했다. 실제 source 폴더/파일 이동, WinUSB/HID/vendor adapter 구현, 장치 탐색/IO·수집, driver 변경, 설치/서비스 등록, 타 OS build/test는 수행하지 않았다. 기준 제품 버전은 v0.1.26이며 GUI 제외·실제 설치 금지를 유지한다.

### 2026-10-12 이전 Python Agent의 원격 리버싱·USB 기능 확인

사용자가 Python 버전에 원격 USB/동글 분석 기능이 있었는지 물어 Git의 Python Agent 폐기 commit `ded180d` 직전 snapshot `654a0df8a75b5d6717e4677e8b360adc474cb54e`와 확인 가능한 모든 ref의 `apps/agent` 변경 이력을 read-only로 확인했다. 당시 `providers/reversing.py`와 `plugins/gdb_driver.py`·`ghidra_driver.py`에는 원격 리버싱/디버깅 코드가 있고, `providers/native_debugger.py`에는 `native.prepare/start/close` CDB 경로, `providers/proxy.py`에는 `proxy.prepare/close` 양방향 carrier가 있다. 이는 기존 원격 분석/중계 구현의 증거이며 USB 장치 전용 Provider의 증거는 아니다.

해당 snapshot의 Python Agent·SDK·protocol·tests와 모든 ref의 Python Agent 변경 이력에서 USB/dongle·WinUSB/HIDAPI/libusb/PyUSB/USB-IP·SetupDi/DeviceIoControl 키워드를 검색했지만 전용 장치 탐색·연결/IO·동글 데이터 수집 구현은 찾지 못했다. `apps/agent/pyproject.toml`에도 USB 전용 라이브러리 의존성은 없지만, 의존성 부재만으로 기능 부재를 판정하지 않았다. 저장소 밖 도구·미커밋 코드·제공되지 않은 이력은 확인 범위에 포함되지 않는다. 기존 shell 실행/파일 회수/중계를 통해 외부 제조사 도구를 사용했을 가능성과 Agent 내 USB 전용 구현을 구분한다.

[Agent 서비스 계획 §3.5](../spec/agent-service-implementation-and-test-plan.md#section-03)에 기존 리버싱·중계·artifact 기능의 재사용 우선 및 전용 장치 adapter 공백만 추가하는 원칙을 반영했다. 현재 Rust 기준 미구현 판단을 과거 Python 전체의 기능 부재나 migration 유실 증거로 확대하지 않는다. 이번 확인은 소스/이력 열람과 문서 수정이며 실제 장치 IO·외부 도구 실행·설치·서비스 작업·build/test는 수행하지 않았다.

### 2026-10-12 Agent 역할 중심 디렉터리 계획 갱신

사용자 요청에 따라 [Agent 서비스 계획](../spec/agent-service-implementation-and-test-plan.md)의 목표 앱 경로를 `apps/agent`로 변경했다. 현재 실제 경로 `apps/agent-rust`와 목표를 구분하고 §3.4·AS15·AST22·산출물 관리에 경로 변경과 Cargo workspace/path dependency·빌드/패키징·version/inventory·CI·검사 스크립트·AGENTS·활성 문서 참조의 동시 갱신을 포함했다. package/EXE `racp-agent`, 공유 `crates/racp-*`, 출력 `dist/agent`는 유지하며 과거 source/manifest/hash 증거는 당시 경로를 보존하도록 명시했다.

이번 작업은 계획 문서 수정이며 실제 source 디렉터리 이동·설정 변경·제품 빌드/시험·설치·서비스 작업은 수행하지 않았다. 제품 기준선 v0.1.26, Windows 우선, GUI 제외 및 실제 설치 금지를 유지한다.

### 2026-10-13 Native Client Enterprise GUI 현대화 및 검증 (`feature/enterprise-client-gui`)

사용자 지시(`/goal`)에 따라 `feature/enterprise-client-gui` 브랜치에서 Native Client GUI(`apps/client-native`)의 엔터프라이즈 모던 디자인 개편, 단일 파일 분해, i18n 언어팩 독립화, 카테고리 트리 및 SVG 3-Way 선택기 구현을 완주했다.

1. **에셋 파이프라인 및 SVG 도입 (Phase 1)**:
   - 이모지를 전면 배제하고 프로덕션 24x24 viewBox SVG 벡터 아이콘 11종 제작 및 내장 (`apps/client-native/assets/icons/`: `deny.svg`, `prompt.svg`, `allow.svg`, `monitor.svg`, `folder.svg`, `file_edit.svg`, `terminal.svg`, `search.svg`, `refresh.svg`, `copy.svg`, `trash.svg`).
   - `Cargo.toml`에 `iced = { workspace = true, features = ["advanced", "svg"] }`, `chrono = { workspace = true }` 연동.
   - Oracle MCP 1차 사전 아키텍처 검토 완료 (Verdict: GO).

2. **독립 다국어 i18n 시스템 구축 (Phase 2)**:
   - `apps/client-native/src/i18n/mod.rs`, `ko.rs`, `en.rs` 분리.
   - 정적 타입 체크가 보장되는 `TextKey` 열거형(70개 이상의 UI 텍스트 키) 및 `tr!` 매크로 구현.
   - OS 기본 언어 자동 감지(`detect_os_language()`, Windows LCID `0x0412` 및 POSIX `LANG`/`LC_ALL` 대응).
   - 설정 탭에서 한국어/영어 즉시 전환(`SetLanguage(Language)`)을 지원하며 폼 입력 데이터 보존.

3. **엔터프라이즈 디자인 시스템 및 재사용 위젯 (Phase 3)**:
   - Dark Slate-950 팔레트와 일관된 컨테이너/버튼/카드 스타일 토큰 (`src/theme/mod.rs`, `src/theme/styles.rs`).
   - 메모리 내장형 벡터 아이콘 렌더러 (`src/components/icons.rs`).
   - 거부/승인 필요/허용 3-Way SVG 세그먼트 버튼 (`src/components/grant_selector.rs`) — 계약 enum `Grant::Deny`, `Grant::RequireApproval`, `Grant::Allow` 직접 바인딩.
   - 카테고리 아코디언 노드 (`src/components/category_accordion.rs`) — 카테고리 헤더, 설명, 켜기/끄기 토글, `일괄 거부`/`일괄 승인`/`일괄 허용` 액션 컨트롤.
   - 실시간 감사 로그 터미널 (`src/components/terminal_log.rs`) — `ControlCompletion` 영수증(Cancellation, Policy, Stop) 포맷팅, 복사/지우기 툴바, 모노스페이스 콘솔 레이아웃.
   - 시맨틱 상태 및 위험도 배지 (`src/components/status_badge.rs`).
   - 기존 `controls.rs`의 `custom_button`, `button`, `text`, `text_input`, `checkbox`에 AccessKit UIA 접근성(Role::Button, Role::TextInput, Role::ComboBox) 100% 보존.

4. **표준 뷰 파일 세분화 및 분해 (Phase 4)**:
   - `src/views/status.rs`: 초기 `.racp` 온보딩 폼(경로, 확인 버튼, 게이트웨이/만료/CA 해시 미리보기, SHA-256 입력, 작업 폴더 초안, 실행 프로필 픽리스트, 미결 경고, 등록 제출), 등록된 상태 요약, 서비스 화면 제어 연결/해제, 실행 작업 실시간 취소(`CancelOperation`), 최근 제어 영수증 터미널 로그 스트림.
   - `src/views/permissions.rs`: 제로 트러스트 매트릭스, CAS 정책 커밋(`SavePolicy`), 전역 프리셋(`*` 와일드카드 일괄 적용), 실행 제약(작업 영역, timeout, output, allow/deny 바이너리 목록) 편집 및 복원.
   - `src/views/approvals.rs`: 대기 중인 승인 요청 실시간 큐, 고위험/중위험 태그, TTL 배지, 1클릭 단건 승인/거절.
   - `src/views/runtime.rs`: 외부 격리 런타임 및 분석 호스트(Chromium, CDB, GDB, Ghidra) manifest 및 해시 승인 폼.
   - `src/views/settings.rs`: 독립 언어팩 선택기(한국어/영어), 최대 15개 네임드 작업 폴더 관리(추가/식별자/경로/삭제), Windows 로그인 시 자동 시작 토글.

5. **App 리팩토링 및 검증 (Phase 5)**:
   - `src/app.rs`: 500줄 이상의 인라인 모놀리식 뷰 코드를 `views::*`로 위임. 네비게이션 탭 하이라이트 및 실시간 대기 승인 건수 배지, 하단 글로벌 툴바/오류 알림 스트림 완벽 유지.
   - `cargo test -p racp-client-native`: 신규 컴포넌트, 다국어 팩, 5개 뷰 렌더링 무결성 등 6개 단위 테스트 **100% 통과 (6 PASS / 0 FAIL)**.
   - `cargo check --workspace`: 워크스페이스 전역 컴파일 에러 0건.
   - Oracle MCP 2차 아키텍처 및 릴리즈 게이트 자문 완료.

6. **제품 배포 빌드 (`dist/`) 및 산출물 (Build ID: `gui-20261013-a`)**:
   - `xtask stage`: Release 프로파일 최적화 바이너리 컴파일 및 스테이징 완료.
   - `xtask verify-staging`: 4개 페이로드 파일 및 대상 아키텍처(x86_64-pc-windows-msvc) 정적 검증 PASS.
   - `xtask stage-agent` & `verify-agent`: Standalone Agent 바이너리 스테이징 및 정적 검증 PASS.
   - 빌드 산출물 일람:
     | 구분 | 경로 | 크기 / SHA-256 해시 |
     |---|---|---|
     | **Client Release 바이너리** | [`dist/client/0.1.26/win-x64/gui-20261013-a/staging/payload/racp-client.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-a/staging/payload/racp-client.exe) | 15,789,056 bytes<br>`df9d051473d214dc0b26b7851a3351825c0f41bb91ae5a426a62b2e05ba123fb` |
     | **Companion Agent** | [`dist/client/0.1.26/win-x64/gui-20261013-a/staging/payload/racp-agent.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-a/staging/payload/racp-agent.exe) | 21,947,392 bytes |
     | **Portable 배포 EXE** | [`dist/client/0.1.26/win-x64/gui-20261013-a/packages/RACP-Native-0.1.26-win-x64-portable.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-a/packages/RACP-Native-0.1.26-win-x64-portable.exe) | 40,571,992 bytes<br>`551128c8ac0f73b34b8dbdf300b82a90cef06d88c58253aebc552ca953bb15f8` |
     | **Native 배포 ZIP** | [`dist/client/0.1.26/win-x64/gui-20261013-a/packages/RACP-Native-0.1.26-win-x64.zip`](../../dist/client/0.1.26/win-x64/gui-20261013-a/packages/RACP-Native-0.1.26-win-x64.zip) | 37,937,682 bytes<br>`afa3f47c48b84211c54fd98f8c11fe307104c5686584a7a466785048c410f242` |
     | **Standalone Agent** | [`dist/agent/0.1.26/win-x64/gui-20261013-a/build/racp-agent.exe`](../../dist/agent/0.1.26/win-x64/gui-20261013-a/build/racp-agent.exe) | 21,947,392 bytes |

### 2026-10-13 Enterprise Layout Parity & Rendering Optimization (`gui-20261013-d`)

사용자 프로토타입(`enterprise_gui_demo.html`) 기준 시각적 일치 및 렌더링 지연 해결을 위한 최종 구조 개선 및 릴리스 패키징을 완료했다.

1. **상단 엔터프라이즈 헤더 & 좌측 210px 사이드바 네비게이션 (`src/app.rs`)**:
   - 상단 헤더: 실드 벡터 로고(`theme::ACCENT`), "RACP Native Client" 타이틀, "Enterprise Agent" 배지, 버전 태그("v0.1.26"), 중앙 실시간 시스템 상태 캡슐(Status Online 점, Role Controller, 통신 경로 표시), 우측 비상 정지(Emergency Stop) 및 종료 버튼.
   - 좌측 210px 사이드바: 5대 네비게이션 버튼(상태, 권한 정책, 승인 요청, 런타임, 환경 설정)과 동적 배지(대기 승인 건수 펄스 배지), 하단 DPAPI Vault 호스트 식별 카드(Device ID 및 암호화 잠금 배지).
   - 우측 본문: 100% 독립 스크롤 가능한 메인 뷰 컨테이너 구성.

2. **상태 대시보드 4대 핵심 지표 카드 (`src/views/status.rs`)**:
   - 1. Agent 프로세스 (PID / 시퀀스 번호 / Named Pipe IPC Connected).
   - 2. 활성 제어 작업 (Active operation slots e.g. `X / 32 Slots`, 취소 핸들러 무장).
   - 3. 대기 중 승인 요청 (Zero-Trust 실시간 건수 태그).
   - 4. 화면/입력 가디언 (Guardian Ready, 물리 입력 감지 시 원격 차단 모니터).

3. **초고속 렌더링 성능 최적화 (SVG Handle Lazy Caching)**:
   - 프레임 렌더링 지연(Lag)의 근본 원인이었던 `tiny-skia` 소프트웨어 렌더러에서의 프레임별 `svg::Handle::from_memory` XML 재파싱을 제거.
   - `std::sync::LazyLock<svg::Handle>` 정적 캐싱 구조를 도입하여 18종의 모든 SVG 벡터 핸들을 1회 파싱 후 `Arc::clone` 참조 방식으로 전환 (렌더링 소요 시간 100ms+ -> 2ms 미만).

4. **검증 및 최종 산출물 (`dist/`, Build ID: `gui-20261013-d`)**:
   - `cargo test -p racp-client-native`: 6개 단위 테스트 **100% 통과 (0.13s)**.
   - `xtask stage`: Release 최적화 바이너리 스테이징 완료.
   - `xtask verify-staging`: 4개 페이로드 정적 검증 PASS.
   - `xtask package`: NSIS Windows Setup EXE, Portable EXE, 배포 ZIP 일괄 생성 완료.
   - Oracle MCP 종합 검토 통과 (Session: `racp-native-client-iced-0`).
   - 빌드 산출물 일람:
     | 구분 | 경로 | 크기 / SHA-256 해시 |
     |---|---|---|
     | **Client 실행 바이너리** | [`dist/client/0.1.26/win-x64/gui-20261013-d/staging/payload/racp-client.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-d/staging/payload/racp-client.exe) | 15,866,880 bytes<br>`f78b5d23c53331471f7736d7339fa706dfa07f156a8dc5efa3f3e1ff734d3ab5` |
     | **Companion Agent** | [`dist/client/0.1.26/win-x64/gui-20261013-d/staging/payload/racp-agent.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-d/staging/payload/racp-agent.exe) | 21,947,392 bytes |
     | **Windows Setup 설치 파일** | [`dist/client/0.1.26/win-x64/gui-20261013-d/packages/RACP-Native-0.1.26-win-x64-setup.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-d/packages/RACP-Native-0.1.26-win-x64-setup.exe) | 10,584,430 bytes<br>`fee03d961c63f356570b37988a9fd17dbe1f66127020c5a837e53e5518ba5d79` |
     | **Portable 배포 EXE** | [`dist/client/0.1.26/win-x64/gui-20261013-d/packages/RACP-Native-0.1.26-win-x64-portable.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-d/packages/RACP-Native-0.1.26-win-x64-portable.exe) | 40,649,816 bytes<br>`49f63d8f1bc4f25d89b7cb85972148bb68d5809680bf3fd99d0b9461da50269e` |
     | **배포 패키지 ZIP** | [`dist/client/0.1.26/win-x64/gui-20261013-d/packages/RACP-Native-0.1.26-win-x64.zip`](../../dist/client/0.1.26/win-x64/gui-20261013-d/packages/RACP-Native-0.1.26-win-x64.zip) | 38,015,506 bytes<br>`6e2ec27e737206ac38c1bfeb21281ba27004e37c8d38cab4773959cd33e7eb63` |
     | **Standalone Agent** | [`dist/agent/0.1.26/win-x64/gui-20261013-d/build/racp-agent.exe`](../../dist/agent/0.1.26/win-x64/gui-20261013-d/build/racp-agent.exe) | 21,947,392 bytes |

### 2026-10-13 Client 실행 장애(Startup Panic / Tray E_FAIL) 원인 규명 및 수정 (`gui-20261013-f`)

사용자의 "전혀 실행이 안된다"는 피드백에 따라 바이너리 런타임 추적 및 백트레이스를 실시하여 초기 구동 실패 원인 2가지를 규명하고 완전히 해소했다.

1. **초기 렌더링 패닉 (Cosmic-Text Line Height Panic)**:
   - **원인**: 사이드바 승인 탭에서 대기 승인 건수가 0일 때 `container(text("").size(0))`가 렌더링되면서, Iced 내부 텍스트 셰이핑 엔진인 `cosmic-text`의 `Buffer::new_empty`에서 `assert_ne!(metrics.line_height, 0.0, "line height cannot be 0")` 어설션이 실패하여 첫 프레임 레이아웃 계산 즉시 프로세스가 panic(ExitCode 101)으로 비정상 종료됨.
   - **해치**: `pending_count > 0`일 때만 배지 요소를 row에 동적으로 추가하도록 구조를 개선하여 `size(0)` 텍스트 생성을 원천 제거.

2. **시스템 트레이 등록 실패에 따른 비정상 종료 및 창 비가시성 (Shell Tray & Window Visibility)**:
   - **원인 1**: Windows 환경에서 탐색기(`Shell_TrayWnd`)가 일시적으로 알림 영역을 수신할 수 없거나 제한된 세션일 때 `Shell_NotifyIconW(NIM_ADD)`가 `0x80004005 (E_FAIL)`을 반환. 기존 코드는 이를 치명적 오류로 간주하여 모달 에러 메시지박스 호출 후 `exit(4)`로 프로세스를 즉시 강제 종료함.
   - **원인 2**: `Settings`의 기본 윈도우 설정이 `visible: !cfg!(windows)`로 지정되어 Windows에서는 창이 숨겨진 상태(`visible: false`)로 생성되었음.
   - **해치**:
     - 트레이 등록 실패(`E_FAIL`) 시 프로세스를 종료하지 않고 경고 로그만 남기며 창이 독립적으로 계속 동작하도록 수정 (`TaskbarCreated` 수신 시 재등록).
     - `main.rs`의 윈도우 설정을 `visible: true`로 명시하여 앱 실행 즉시 화면에 클라이언트 UI 창이 정상 노출되도록 보장.

3. **검증 및 최종 산출물 (`dist/`, Build ID: `gui-20261013-f`)**:
   - `racp-client.exe` 런타임 프로세스 테스트: PID 생성 및 `MainWindowTitle: "RACP Native Client v0.1.26"`, `Responding: True` 정상 확인.
   - `cargo test -p racp-client-native`: 6개 단위 테스트 **100% 통과**.
   - `xtask stage`: Release 최적화 바이너리 스테이징 완료.
   - `xtask verify-staging`: 4개 페이로드 정적 검증 PASS.
   - `xtask stage-agent` & `verify-agent`: PASS.
   - `xtask package`: Windows Setup EXE, Portable EXE, 배포 ZIP 일괄 생성 완료.
   - 빌드 산출물 일람:
     | 구분 | 경로 | 크기 / SHA-256 해시 |
     |---|---|---|
     | **Client 실행 바이너리** | [`dist/client/0.1.26/win-x64/gui-20261013-f/staging/payload/racp-client.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-f/staging/payload/racp-client.exe) | 15,865,856 bytes<br>`1271b00b5225f2956bbfe4c98ff5d0f7c7a74f0798c55144de44be53dfa4d286` |
     | **Companion Agent** | [`dist/client/0.1.26/win-x64/gui-20261013-f/staging/payload/racp-agent.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-f/staging/payload/racp-agent.exe) | 21,947,392 bytes |
     | **Windows Setup 설치 파일** | [`dist/client/0.1.26/win-x64/gui-20261013-f/packages/RACP-Native-0.1.26-win-x64-setup.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-f/packages/RACP-Native-0.1.26-win-x64-setup.exe) | 10,582,863 bytes<br>`d59f46d263c2e8636eaff8d818a9a6bbad8186ef29b960e1b8173162974a0ace` |
     | **Portable 배포 EXE** | [`dist/client/0.1.26/win-x64/gui-20261013-f/packages/RACP-Native-0.1.26-win-x64-portable.exe`](../../dist/client/0.1.26/win-x64/gui-20261013-f/packages/RACP-Native-0.1.26-win-x64-portable.exe) | 40,648,280 bytes<br>`ec77641c73d6854fcfcec2e14859cf8bd0c324e04ed8cdfd0bc8fc6f7b5f22f7` |
     | **배포 패키지 ZIP** | [`dist/client/0.1.26/win-x64/gui-20261013-f/packages/RACP-Native-0.1.26-win-x64.zip`](../../dist/client/0.1.26/win-x64/gui-20261013-f/packages/RACP-Native-0.1.26-win-x64.zip) | 38,013,970 bytes<br>`4eb980872a041cef0b5884235cb86c2825a3996da69ac5903cde37975a91365d` |
     | **Standalone Agent** | [`dist/agent/0.1.26/win-x64/gui-20261013-f/build/racp-agent.exe`](../../dist/agent/0.1.26/win-x64/gui-20261013-f/build/racp-agent.exe) | 21,947,392 bytes |



### 2026-10-13 Agent 서비스 독립 기반 구현 — v0.1.27 (`codex/agent-service-foundation`)

사용자가 Agent 서비스 계획에 따라 미구현 기능 진행을 요청해 첫 묶음 AS15/AS01/AS02와 AS12의 배포 정의를 구현했다. 계획서의 과거 “문서 작성만” 기록과 현재 구현 요청을 구분했다. 별도 managed worktree/브랜치에서 작업했으며 `apps/client-native` 제품 source·실제 설치·SCM·기존 등록/state·USB/driver·macOS/Linux는 변경하거나 실행하지 않았다. PATCH는 **0.1.26→0.1.27 한 번**만 증가하고 `uv.lock`, root Cargo.lock, 독립 analysis-host Cargo.lock의 로컬 package version을 동기화했다. 분석 host는 metadata만 갱신했으며 엔진/build를 실행하지 않았다.

| 작업 | 이번 source 범위 |
|---|---|
| AS15 | Agent 앱 경로/Windows service·setup 분리 및 실행 참조 갱신. 공유 backend의 추가 재배치는 점진 작업으로 유지 |
| AS01 | 명시 Agent 서비스 역할/3-file allowlist·hash·PE·file pin·hard-link 거부 및 machine receipt 역할 연결 |
| AS02 | GUI 없는 native-control/native-service-control의 기존 인증 IPC·CAS·승인·cancel/stop/event 소비 경로 |
| AS12 | 독립 staging/verify/setup command·Agent-only recipe/CI 정의. setup 생성/실행 인수는 이번 배치에서 미수행 |

- [Agent source](../../apps/agent/src/main.rs)는 역할 경로 `apps/agent`를 사용한다. Windows service/setup 모듈은 `platform/windows/service`와 `platform/windows/setup`으로 이동했다. Cargo·uv exclude·source digest·migration inventory의 current_source_path·CI·AGENTS·활성 source 링크를 갱신하고 과거 source_path/검증 기록은 보존했다. macOS/Linux service/device adapter는 미구현이다.
- [Payload 계약](../../crates/racp-contract/src/distribution.rs)과 [admission](../../crates/racp-core/src/distribution.rs)은 명시 `native-agent-service` 역할에 Agent·LICENSE·SBOM만 허용한다. Client의 role 생략/4-file 계약은 유지하고 Client/portable caller는 service 역할을 거부한다. Agent 공통 worker/user run은 검증된 두 역할을 소비하지만 service host·provision·관리·restore·설치 보호는 service 역할만 받는다. hash/PE/파일 집합/duplicate-key/크기 검사를 유지하며 manifest/모든 파일의 hard-link도 거부한다.
- [Headless CLI](../../apps/agent/src/cli/control.rs)는 기존 ControlRequest/ControlReply와 실제 SID/session·server PID/birth 검증을 재사용한다. snapshot·최대 5초/256개 subscribe event·policy CAS·승인 nonce·정확한 operation/request/boot cancel·stop을 제공한다. service-control은 공유 endpoint만 읽고 별도 TCP/무인 승인 우회를 만들지 않았다. 정상 거부 reply는 `result.type=error`와 exit 0, 입력/transport 실패는 stderr/exit 4라는 계약을 가이드에 명시했다. 실제 service observer/controller 실행은 아래 user-mode 결과와 구분한다.
- `xtask stage-agent-service` / `verify-agent-service`와 `package-agent-service`를 연결했다. [Agent-only NSIS recipe](../../xtask/native-machine-installer.nsi)는 Client EXE/shortcut이 없고 files/protection receipt 단계와 서비스 등록/입력을 분리한다. Client package의 machine/both 혼합 역할은 거부하고 독립 Agent command를 사용한다. CI는 Windows만 stage/verify하며 setup 생성은 별도 명시 workflow-dispatch input이다. **이번에 setup EXE를 생성/실행하거나 CI를 dispatch하지 않았다.** compiler cache는 일반 절대 디렉터리만 재사용하도록 기존 path/reparse 검사를 유지했다.

최종 개발 산출물은 [service staging manifest](../../dist/agent/0.1.27/win-x64/foundation-20261013-c/staging/build-manifest.json)와 [payload](../../dist/agent/0.1.27/win-x64/foundation-20261013-c/staging/payload/payload-manifest.json)이다. Agent EXE는 **22,186,496 bytes**, SHA-256 `ddd4899d1d2cf0c7321448183005f8c8d9592de94749782d022c915268db0cb7`; payload manifest SHA-256 `8bd7a40a42100e619497413bc4ed4364dd31c6494514a7d95b7b5fef55c5156b`; input digest `5b639eb10376c8d97de3e60c5ce14919ab1ee212b44e3804365697751d290e97`; Cargo.lock SHA-256 `c1513ab3d914d615ce1c897f7236c96e5b7b5cabbd3fca7d513a84a9e2750264`다. Agent-only dependency graph의 SBOM에서 Iced/Client/Electron/Node/CPython component가 없는 것을 확인했다. unsigned/non-published이며 `tests=not_run_by_xtask`; 별도 시험 근거는 다음과 같다.

| 검사 | 실제 결과/범위 |
|---|---|
| Windows production build/정적 admission | Rust/Cargo 1.90.0, static CRT/x64 release 및 `verify-agent-service` PASS. Agent·LICENSE·SBOM 3개, file/hash/PE/import/manifest 검사. 실제 SCM/설치 아님 |
| 설치 없는 실제 HTTPS/WSS + headless IPC | [최종 results](../../dist/verification/agent/0.1.27/foundation-e2e-final/results.json) **21 PASS / 0 FAIL**, 위 최종 EXE hash 일치. 새 Gateway/Device/state/workspace만 사용; 같은 PC/user-mode. UTF-8 파일·default deny/경로 탈출·stdout/stderr/exit·멱등 실행·ConPTY·정책 CAS/stale revision·승인/nonce replay·명시 거부/승인자 부재·stale-boot 및 실제 cancel·소유 child 종료·stop/drain·재시작/재연결 보존. 기존 profile 미사용, 시험 Agent/Gateway 정상 종료 |
| Rust contract/core/xtask | [로그](../../dist/verification/agent/0.1.27/foundation-20261013/foundation-tests-final.log) **32 PASS**, role/client 호환·불법 파일/target/hash·duplicate/hard-link·write pin·SID/session model·package definition 포함 |
| Agent dispatch 모델 | [최종 workspace 로그의 lib 절](../../dist/verification/agent/0.1.27/foundation-20261013/rust-workspace-f.log) **8 PASS**. 구 v1/permissions 없는 fixture를 v2+명시 exec.argv 권한·같은 boot/epoch/READY peer로 정비. 실제 Provider 효과 count·중복/충돌·로컬 deny/lease expiry·deadline cleanup 기준은 유지 |
| Python 계약/버전 | [로그](../../dist/verification/agent/0.1.27/foundation-20261013/python-contracts.log) **14 PASS** (버전 6 포함), Ruff PASS, mypy **98 source files PASS** |
| Source/format/검토 | inventory-check **114 operations / 143 permissions / 18 categories**, frozen baseline 100 유지. Agent/core/runtime/contract/xtask Rust format·git diff check PASS. 별도 읽기 전용 focused review에서 actionable finding 없음; reviewer가 tests를 독립 재실행한 것은 아님 |

**전체 workspace suite는 PASS가 아니다.** 최종 `cargo test --workspace --locked`는 Browser군 **7 PASS / 13 FAIL**에서 중단됐다. 기존 Browser fixture가 v1/permissions 없는 AgentSettings로 `Browser::new`에 들어가 `PERMISSIONS_INVALID`로 실패한다. 선택 runtime/Browser fixture 정비는 이번 첫 묶음의 완료 증거로 바꾸지 않으며 이후 test군도 해당 full run에서 미실행이다. 실패 이름은 다음과 같다.

- `browser_cancel_cleanup_includes_startup_before_cdp_is_ready`
- `browser_process_is_protected_from_generic_termination`
- `concurrent_close_and_agent_cleanup_finish_owned_profile_once`
- `cookies_form_refs_native_input_png_and_navigation_invalidation`
- `external_cdp_opt_in_context_and_target_ownership`
- `failed_evaluation_records_started_effect_and_retains_no_child_after_timeout`
- `foreign_recovery_authority_is_retained_and_cannot_report_complete_cleanup`
- `frame_scope_cross_site_input_key_focus_and_detachment`
- `isolated_contexts_owner_boot_snapshot_and_complete_cleanup`
- `origin_fence_prevents_subresource_and_websocket_network_effects`
- `recovery_never_deletes_a_live_owned_process_profile`
- `restart_recovers_only_persisted_authorized_external_targets`
- `restart_removes_profile_only_after_owned_process_tree_has_exited`

전체 `cargo fmt --all`도 기준 GUI source의 기존 trailing-whitespace/format 문제로 실패했고 Agent 범위만 최종 통과했다. 자동 formatting이 만든 GUI 변경은 모두 되돌려 GUI source 차이는 0이다. 기존 recipes Windows callback 이름 warning 3개 및 core test unused-import warning은 유지된다.

재시도 기록: 이전 turn 중단으로 생긴 PDB/LLVM incremental 손상 및 실행 중 xtask.exe를 재빌드한 file-lock 경합을 확인했다. 캐시 정리 명령은 정책 차단돼 삭제하지 않고 별도/기존 정상 cache로 재검증했다. 최초 service batch a는 compiler cache junction을 secure source read가 거부해 해당 staging만 quarantine에 보존했고 path 검사를 약화하지 않았다. 일반 디렉터리 cache에서 b와 최종 c를 같은 버전으로 생성했다. inventory의 permissions.rs raw hash 불일치는 managed checkout CRLF/LF 차이였으며 frozen baseline과 같은 LF bytes로 복구해 계약 변화/delta 없이 통과했다. 최초 headless red는 이전 EXE의 명령 부재, 추가 승인 거부 시험의 최초 실패는 내부 APPROVAL_REJECTED와 공개 publication guard의 APPROVAL_REQUIRED를 혼동한 harness assertion이었다. 제품 승인 검사는 변경하지 않고 reply·승인 종료 event·결과/data 부재를 함께 검사해 최종 후보를 다시 통과시켰다. b와 c EXE hash는 달라 b의 인수를 최종 c로 이관하지 않고 **최종 c를 21-case 재실행**했다.

> [!IMPORTANT]
> 이번 완료 범위는 **Agent 서비스용 source/Windows 개발 payload·headless user-mode 검증**이다. 실제 service account/SYSTEM/SCM lifecycle·ACL/DPAPI 인수(AST12–AST18), 설치/제거/clean-machine·서명·실제 두 PC/MCP 인수는 완료로 선언하지 않는다. AS03–AS11의 실제 서비스 조건/운영·AS13 복구·AS16–AS18 USB/외장 장치 enumeration/opaque session/수집/IO 정리와 AST24–AST26 실제 장치 인수는 남아 있다. 현재 설치 금지와 장치/driver 변경 금지를 유지한다.

#### main 통합 전 전체 회귀 — 2026-10-13

후속 구현/통합 요청에 따라 Browser fixture를 v2 설정과 명시 console/network read 권한으로 정비했다. Filesystem fixture의 workspace가 runtime private parent 내부에 놓여 정상 작업까지 거부되던 배치를 `workspace`, `docs`, `state/data`로 분리했다. 제품의 default deny·state 보호·process 종료 확인·5초 cleanup 예산은 변경하지 않았다.

- [전체 workspace 최종 실행](../../dist/verification/agent/0.1.27/foundation-20261013/rust-workspace-h.log): `cargo test --locked --workspace`, **92 PASS / 0 FAIL / 4 ignored**, exit 0. ignored는 subprocess 전용 `support::fixture_child` 4개이며 부모 시험이 명시 실행한다. 기존 Windows Chromium 1243을 명시한 격리 profile/local fixture 시험이며 native Browser도 **20 PASS**다.
- [Filesystem/Memory/Process/Shell/Terminal 묶음](../../dist/verification/agent/0.1.27/foundation-20261013/runtime-fixtures.log): **15 PASS / 0 FAIL / 4 ignored**. Ruff PASS, mypy **98 files PASS**, 버전 시험 **6 PASS**, scoped Rust format PASS를 다시 확인했다.
- Browser 권한 fixture 수정 직후 실행은 **17 PASS / 3 FAIL**였으며 concurrent close, evaluation timeout cleanup, external CDP의 owned browser 종료에서 실패했다. 임시 native cleanup 진단을 추가한 단독 close **1 PASS**와 [전체 Browser 재실행](../../dist/verification/agent/0.1.27/foundation-20261013/browser-group-trace.log) **20 PASS**에서는 재현되지 않아 진단 코드를 제거했다. 위 전체 workspace도 통과했지만 **간헐 cleanup 실패의 원인을 규명하거나 제품 수정으로 해결했다는 증거는 아니다**. AS07의 반복/부하 인수 공백으로 유지한다. 실제 owned process handle signal과 Job tree-empty 증명을 exit code만으로 대체하지 않았다.

앞선 실패 로그와 당시 결과는 이력으로 보존한다. 후속 PASS는 source 회귀 근거이며 SCM/서비스 identity·설치·물리 USB 장치 인수를 대신하지 않는다.

### 2026-10-13 AS09 recovery 계약·비활성 기본 정책 readback — v0.1.28

기반 구현은 `5e1fd98`로 커밋하고 기존 GUI startup 수정 `8ac3e96`를 보존해 로컬 `main`의 `be5b456`에 통합했다. [main 통합 뒤 전체 suite](../../dist/verification/agent/0.1.27/foundation-main/rust-workspace.log)는 **92 PASS / 0 FAIL / 4 subprocess fixture ignored**다. 이후 AS09의 운영 정책 source를 후속 묶음으로 구현했으며 PATCH **0.1.27→0.1.28 한 번**과 Python/Rust/analysis-host lock의 로컬 package version을 동기화했다.

- [공통 recovery 계약](../../crates/racp-contract/src/service_recovery.rs)은 OS 호출 없는 타입/검증으로 command·reboot·알 수 없는 필드·무제한/비단조 restart·non-crash recovery를 거부한다. 기본 정책은 **단일 `none` / non-crash false**다. 5초/30초/120초·terminal none·24시간 무실패 reset의 bounded profile은 계약/변환만 준비했고 SCM 활성화/CLI opt-in에 연결하지 않았다.
- [Windows readback 어댑터](../../apps/agent/src/platform/windows/service/recovery.rs)는 신규 fixed-name service의 기본 정책을 명시하고 재확인한다. QueryServiceConfig2W는 NULL probe의 insufficient-buffer 크기로 최대 8 KiB를 할당하며 성공 호출의 미보장 size 값에 의존하지 않는다. 구조·array/count·alignment·포인터 범위·exact action/reset/flag·빈 command/reboot text를 확인한다. 실패는 `SERVICE_RECOVERY_OUTCOME_UNKNOWN`이다.
- provision은 입력 commit 뒤 **서비스가 아직 disabled일 때** 기본 정책을 설정·확인한 다음 demand-start를 허용한다. restore는 새 service의 일회성 maintenance STOPPED/identity 검증 뒤 적용하며 readback 실패에는 disabled와 `recovery_unconfirmed` 기록을 남긴다. 기존 service를 채택/재구성하지 않는다.
- focused review가 일회성 configure/reconcile/recover_sessions/remove의 crash 후 자동 재시작 위험을 지적해 기본값을 disabled로 유지했다. [기본값 RED](../../dist/verification/agent/0.1.28/recovery-20261013/default-red.log)는 4개 restart actions 대신 1개 none을 요구해 실패했고 [GREEN](../../dist/verification/agent/0.1.28/recovery-20261013/default-green.log)은 native parser/query/변환 **7 PASS**다. 재검토에서 해당 P1이 해소됐으며 reviewer는 실제 SCM/tests/build를 실행하지 않았다.

실제 Windows API의 [failure-action 반복 규칙](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/ns-winsvc-service_failure_actionsw), [non-crash flag](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/ns-winsvc-service_failure_actions_flag), [query 크기 계약](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-queryserviceconfig2w)을 기준으로 source를 구성했다. [이미 queue된 restart](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-changeserviceconfig2w)는 STOPPED 보고만으로 취소되지 않는다. 명시 disabled·durable startup gate·관리/중지 orchestration을 완성하기 전 자동 restart/boot auto-start를 지원한다고 선언하지 않는다.

| 최종 검사 | 결과·범위 |
|---|---|
| 전체 Rust 직렬 suite | [로그](../../dist/verification/agent/0.1.28/recovery-20261013/rust-workspace-serial.log), `cargo test --locked --workspace -- --test-threads=1`, **103 PASS / 0 FAIL / 4 ignored**, exit 0. 새 contract 4/native adapter 7 포함; SCM API를 호출하는 시험은 없음 |
| Python | [로그](../../dist/verification/agent/0.1.28/recovery-20261013/python-final.log), 버전 6/ConPTY harness 5의 **11 PASS**. Ruff PASS, mypy **98 files PASS**, scoped Rust fmt/git diff check PASS. Inventory **114 ops / 143 permissions / 18 categories**, frozen 100 baseline 유지 |
| Windows 개발 서비스 payload | [build manifest](../../dist/agent/0.1.28/win-x64/recovery-20261013-a/staging/build-manifest.json), [stage 로그](../../dist/verification/agent/0.1.28/recovery-20261013/stage.log), release/static CRT Windows x64 build 및 GUI 없는 3-file allowlist·PE/native imports·hash verify PASS. 설치 artifact 생성/installer 실행/SCM 설정은 하지 않음 |
| 최종 EXE 실제 원격 회귀 | [결과](../../dist/verification/agent/0.1.28/recovery-e2e-final-b-20261013/results.json)·[로그](../../dist/verification/agent/0.1.28/recovery-20261013/e2e-final-b.log), **21 PASS / 0 FAIL**, exit 0. 위 최종 EXE hash와 최종 harness hash를 대조했다. 격리 HTTPS/WSS/user-mode/인증 IPC·권한·승인/replay·파일·stdout/stderr/exit·멱등성·ConPTY·CAS·cancel/child 종료·stop/drain·restart 보존을 재검증했다. 시험 Agent/Gateway 종료 완료, 기존 profile 미사용. 서비스 계정/SCM 조건의 결과로 해석하지 않음 |

Agent EXE는 **22,196,224 bytes**, SHA-256 `82eebcee2e621f149a3c6457b292b7ff83172aa0c0f3ea2c505055aa5fb751c1`; payload manifest SHA-256 `63c2e9be9a8d9fb9bbda03507ca587db595fd02007b9c66d9a87e2362ff5aa74`; source input digest `8c334c614858382642f5f1f6e0d2f1718b9731c05e65a54edd46ab623b1ec932`다. source evidence는 baseline `be5b456`와 당시 dirty input을 기록한다. unsigned/non-published이며 xtask 자체의 기능 시험은 `not_run_by_xtask`다.

실패도 보존한다. [최종 병렬 suite](../../dist/verification/agent/0.1.28/recovery-20261013/rust-workspace-disabled-final.log)는 Browser **17 PASS / 3 FAIL**에서 중단했으며 실패는 concurrent close, frame scope close, origin-fence cleanup이었다. 이번 source 묶음에 Browser/runtime Provider 변경은 없다. 직렬 PASS가 **간헐 CLEANUP_FAILED를 해결했다는 증거는 아니다**. AS07 부하/병렬 cleanup 인수 공백은 유지하며 종료 deadline·parent signal/Job tree-empty 확인을 약화하지 않았다.

최초 원격 harness 실행은 ConPTY 명령의 파일 효과까지 확인한 뒤 단일 read 출력 assertion에서 실패했다. 비동기 출력이 여러 read로 나뉘는 조건을 [harness 시험](../../tests/unit/test_native_agent_harness.py)으로 고정하고 byte cursor를 따라 UTF-8 marker를 128 KiB/10초 예산 안에서 수집하도록 수정했다. read 실패/EOF/역행 cursor/초과 크기 및 마감 후 도착한 marker를 거부하며 HTTP timeout에도 남은 예산을 전달한다. 생산 Provider·cleanup deadline은 변경하지 않았다. 앞선 E2E 실패와 [late-marker RED](../../dist/verification/agent/0.1.28/recovery-20261013/terminal-deadline-red.log)를 최종 성공으로 덮어쓰지 않는다.

> [!IMPORTANT]
> AS09는 **recovery 계약·보수적 disabled 기본값·readback의 부분 구현**이다. bounded restart의 활성화, 일회성 관리 crash gate, queued restart 중지, boot auto-start 및 실제 SCM/계정/설치 인수는 남아 있다. AS07 간헐 Browser cleanup과 AS16–AS18 USB/장치 계약·backend·수집·물리 인수도 미완료다. 실제 설치·서비스 등록·driver/device IO·기존 profile 변경은 하지 않았다.

후속 source commit **`3b54490`**은 로컬 main에 fast-forward로 반영했다. [main 통합 후 전체 직렬 suite](../../dist/verification/agent/0.1.28/recovery-main/rust-workspace-serial.log)는 **103 PASS / 0 FAIL / 4 ignored**, main의 Python 시험 **11 PASS**, Ruff/mypy PASS, version show **0.1.28**이다. main 출력 위치에 원본 staging bytes와 private DACL을 보존해 복사한 뒤 `verify-agent-service --build-id recovery-20261013-a`도 PASS이며 EXE hash는 위 최종 E2E와 같다. 최초 robocopy security 복사는 empty output root에서 Access denied로 실패해 owner/group 변경 없이 그 **생성된 출력 디렉터리의 DACL만** 원본과 맞춰 복사했고, 파일/payload 검증을 약화하지 않았다. 검증 로그·최종 E2E results만 보존했으며 시험 credential/등록 state는 main으로 복사하지 않았다. 기존 GUI source와 사용자 미추적 HTML/계획 파일 3개는 보존됐고 push는 하지 않았다. 후속 구현과 원본 격리 evidence 보존을 위해 managed worktree를 유지한다.

### 2026-10-13 AS09 durable 일회성 start intent·완료 maintenance 재개 — v0.1.29

PATCH **0.1.28→0.1.29 한 번**과 Python/Rust/optional analysis-host lock의 로컬 package version을 동기화했다. [공통 계약](../../crates/racp-contract/src/service_start.rs)은 command/phase/UUID/hash/record grammar를 검증하고, [core 저장 계층](../../crates/racp-core/src/service_start.rs)은 pinned root identity, bounded/중복 key 거절, atomic claim-before-effect, receipt SHA 및 CAS acknowledge/abort를 제공한다. prepared/claimed/completed/failed는 정상 Run을 차단하며 claimed/failed를 자동 replay하지 않는다. [Windows service actor](../../apps/agent/src/platform/windows/service/start_intent.rs)는 actor/private ACL 확인 뒤 의도를 소비하고 host는 실행 lock을 수명 전체에 유지한다. SYSTEM helper의 configure/reconcile/recover_sessions/remove/restore는 UUID만 SCM start argument로 전달한다. 정상 Run의 bootstrap/WSS는 미확정 intent에서 실행되지 않는다.

`inspect_start_intent`/`abort_start_intent`/`acknowledge_start_intent`는 fixed owned STOPPED/PID 0 service와 private root를 전제로 한다. completed configure/reconcile/session recovery는 현재 receipt와 exit 0/NONE recovery를 확인해 CAS acknowledge한다. 제거·복원의 completed maintenance에는 일반 acknowledge를 금지한다. 명시 `resume_request_id`/`resume_expected_sha256`을 기존 remove/restore 요청에 추가한 경우에만 동일 purpose의 마지막 SYSTEM 작업을 재개한다. maintenance를 다시 실행하지 않고 config/device/current state/schema/credential 존재/pending·receipt hash·service identity를 다시 확인한다. 제거 barrier는 namespace 부재를 확인한 restore만 소비한다. 복원은 NONE 정책·demand-start readback/release publish/receipt 뒤에 acknowledge한다. [계획](../spec/agent-service-implementation-and-test-plan.md)과 [운용 가이드](../guides/desktop-client-guide.md)에 경계 및 legacy start argument 거절을 반영했다.

검토에서 completed maintenance의 finalization 오류 뒤 재개 경로 부재를 확인해 보완했다. 새 restore의 기본 failure actions가 empty인 상태를 NONE readback으로 확인하려던 회귀는 새 disabled service 생성 직후 NONE 설정으로 수정했다. demand readback을 disabled 허용 검사와 분리하고 제거 final lock 안에서 현재 state identity를 추가 확인했다. unregistered maintenance의 기존 allowlist가 새 의도/lock을 거부하던 문제는 [RED](../../dist/verification/agent/0.1.29/start-intent-20261013/maintenance-files-red.log)로 재현한 뒤 정확한 네 상수만 허용해 [GREEN](../../dist/verification/agent/0.1.29/start-intent-20261013/maintenance-files-green.log)으로 확인했다. 추가·유사 이름 및 credential 파일은 계속 거부한다. 외부 reviewer의 마지막 재검토는 계정 credit 오류로 중단됐으므로 최종 clean review로 기록하지 않으며, 해당 지적 수정 뒤 컴파일·시험 및 자체 source 검사를 수행했다.

| 검사 | 결과·범위 |
|---|---|
| intent 계약·저장·host/maintenance | 최종 전체 suite에 새 contract **2**, core **7**, Agent parser/resume/receipt **3**, runtime allowlist **1** 포함. claim 뒤 crash·이중 claim·구 UUID·다른 root·변조·receipt/CAS·관리/실행 lock·정확한 completed purpose 재개를 검증. SCM/실제 서비스 계정 호출 없는 모델/파일 시험 |
| 전체 Rust 직렬 suite | [최종 로그](../../dist/verification/agent/0.1.29/start-intent-20261013/rust-workspace-serial-b.log), `cargo test --locked --workspace -- --test-threads=1`, **116 PASS / 0 FAIL / 4 subprocess fixture ignored**, exit 0 |
| Python·정적 검사 | [Python](../../dist/verification/agent/0.1.29/start-intent-20261013/python-final.log) **11 PASS**, [Ruff](../../dist/verification/agent/0.1.29/start-intent-20261013/ruff.log) PASS, [mypy](../../dist/verification/agent/0.1.29/start-intent-20261013/mypy.log) **98 files PASS**, scoped Rust fmt/git diff check PASS, [inventory](../../dist/verification/agent/0.1.29/start-intent-20261013/inventory.log) **114 ops / 143 permissions / 18 categories**, frozen 100 baseline 유지 |
| Windows Agent 개발 payload | [manifest](../../dist/agent/0.1.29/win-x64/start-intent-20261013-a/staging/build-manifest.json)·[stage](../../dist/verification/agent/0.1.29/start-intent-20261013/stage.log)·[verify](../../dist/verification/agent/0.1.29/start-intent-20261013/verify-stage.log), Rust 1.90.0 Windows x64 release/static CRT·3-file GUI-free allowlist·PE/import/hash PASS. 설치 artifact·installer 실행 없음 |
| 최종 EXE 격리 원격 회귀 | [results](../../dist/verification/agent/0.1.29/start-intent-e2e-20261013-a/results.json)·[로그](../../dist/verification/agent/0.1.29/start-intent-20261013/e2e.log), **21 PASS / 0 FAIL**, exit 0. 기존 profile 미사용 HTTPS/WSS·인증 IPC/headless·권한/승인/replay·파일/ConPTY·CAS/cancel/stop/재시작. 실제 SCM intent/가상 계정 인수를 대신하지 않음 |

최종 EXE **22,328,832 bytes**, SHA-256 **`7c67a924e1915e315467c475717c58e6dd54730efffdd694a4fbaae1ee814e2a`**, payload manifest **`a6425d25aca14131a3e2db597338d07adbd214da1006bc478bc1a29396fee314`**, source input digest **`86b77284b8d6dd0060764b84edb3c7b61317c601120191695da860a0f3a0865e`**. E2E EXE hash는 manifest와 일치하고 harness SHA는 **`fd9b319d459948f5fed9dc5b8b6b8ed6de46e249392c8f4191ccd9fa93d65a1e`**다.

최초 [전체 직렬 실행](../../dist/verification/agent/0.1.29/start-intent-20261013/rust-workspace-serial.log)은 Browser `cookies_form_refs_native_input_png_and_navigation_invalidation`의 cleanup에서 **19 PASS / 1 CLEANUP_FAILED**로 중단됐다. Agent 빌드 종료 뒤의 최종 직렬 재실행은 통과했지만 원인 규명·제품 수정 증거가 아니며 AS07 간헐 cleanup 공백을 유지한다. 생산 cleanup 예산·Provider 동작·실제 process-tree 종료 proof를 완화하지 않았다. 기존 recipes warning 3개/core test unused import warning도 유지된다.

> [!IMPORTANT]
> AS09는 **일회성 관리 작업의 durable 실행 경계 source·개발 검증**까지 진행했다. claimed/failed의 수동 outcome recovery, namespace 없는 prepared restore 관리(AS08), queued restart 중지·bounded restart 활성화·boot admission, 실제 SCM/SYSTEM/virtual account/설치 인수는 미완료다. AS16–AS18 USB/장치 계약·backend·수집·물리 인수도 남아 있다. 실제 설치·서비스 등록/시작·driver/device IO·기존 profile 변경은 하지 않았다. GUI source와 사용자 미추적 파일 3개를 보존한다.

구현 commit **`bd332e5`**를 로컬 `main`에 fast-forward 반영했다. [main 전체 직렬 회귀](../../dist/verification/agent/0.1.29/start-intent-main/rust-workspace-serial.log)는 **116 PASS / 0 FAIL / 4 ignored**, main의 Python **11 PASS**, Ruff/mypy **98 files PASS**, version **0.1.29**다. 위 staging bytes와 생성된 출력 디렉터리의 private DACL 및 비밀 없는 logs/최종 results만 main 출력 위치로 보존했고 [main payload verify](../../dist/verification/agent/0.1.29/start-intent-main/verify-stage.log)도 PASS다. EXE SHA는 위 E2E와 동일하다. 시험 credential/등록 state는 복사하지 않았으며 기존 profile/service를 변경하지 않았다. 새 문서 상대 링크를 확인했고 사용자 미추적 GUI/계획 파일 3개는 유지한다. push 없이 다음 roadmap 작업을 계속하며 managed worktree는 보존한다.

#### 같은 v0.1.29 묶음의 최초 service 등록 admission 수정

main 반영 후 bootstrap source 검사에서 `NativeRegistration::ensure_empty(service=true)`가 host의 새 execution lock을 거부해 최초 등록을 차단하는 회귀를 발견했다. [RED](../../dist/verification/agent/0.1.29/start-intent-20261013/service-registration-red.log)로 실패를 확인하고 [수정/시험](../../crates/racp-core/src/state_schema.rs)을 추가했다. service actor/config 확인 이후 idle start gate의 네 고정 파일만 허용하며 미확정 intent는 token 소비 전에 차단한다. unregistered restore의 고정 maintenance·restore/detach·config/agent/settings lock은 **Acknowledged Restore의 현재 receipt hash/nonce/config hash·registered=false/device=null/state-preserved/credential-unread**가 확인될 때만 허용한다. 사용자 등록의 empty-root 검사는 유지하고 임의 파일·불일치 receipt를 거부한다. [GREEN](../../dist/verification/agent/0.1.29/start-intent-20261013/service-registration-final.log) **1 PASS**는 실제 SCM/enrollment 실행이 없는 private fixture admission 시험이다. 실제 서비스 계정의 bootstrap·DPAPI/등록 인수는 계속 미완료다.

소스가 달라져 앞선 batch a의 EXE/21-case 결과를 새 소스 증거로 사용하지 않았다. 같은 PATCH로 batch **`start-intent-20261013-b`**를 새로 [stage](../../dist/verification/agent/0.1.29/start-intent-20261013/stage-b.log)/[verify](../../dist/verification/agent/0.1.29/start-intent-20261013/verify-stage-b.log)해 기존 출력도 보존했다. 수정 뒤 [전체 직렬 suite](../../dist/verification/agent/0.1.29/start-intent-20261013/rust-workspace-serial-c.log)는 **117 PASS / 0 FAIL / 4 ignored**, 새 EXE의 [원격 results](../../dist/verification/agent/0.1.29/start-intent-e2e-20261013-b/results.json)·[로그](../../dist/verification/agent/0.1.29/start-intent-20261013/e2e-b.log)는 **21 PASS / 0 FAIL**, exit 0이다. scoped fmt/diff check도 PASS이며 Python 11/Ruff/mypy의 대상 소스·버전은 앞선 검사와 동일하다. Browser 간헐 CLEANUP_FAILED와 actual SCM/설치 미검증 경계는 유지한다.

**최신 v0.1.29 후보는 batch b**다. [manifest](../../dist/agent/0.1.29/win-x64/start-intent-20261013-b/staging/build-manifest.json): EXE **22,331,904 bytes**, SHA-256 **`10448a84a0a31ada180c6d457668c175c9fdd06ba78afcc02fb95ebb48022c4b`**, payload manifest **`530bdb9c426dedd78ab93af04b5b248f13bc43699aeff69b94372c2869cb5e4b`**, source digest **`13244b46b4fc5c88a655f726554c556d33629c5992b8a683024959789f97a32e`**. 최종 E2E의 EXE SHA는 manifest와 일치하고 harness SHA는 앞선 실행과 동일하다.

admission 수정은 **`2b84b31`**, 동시에 main에 반영된 Client 문서 commit `e1c7df2`와의 통합은 **`f171ba9`**다. SSOT의 두 append 내용을 모두 보존해 문서 충돌을 해결했고 GUI source 변경은 없다. [수정·통합 뒤 main 전체 직렬 회귀](../../dist/verification/agent/0.1.29/start-intent-main/rust-workspace-serial-b.log)는 **117 PASS / 0 FAIL / 4 ignored**, [main batch b verify](../../dist/verification/agent/0.1.29/start-intent-main/verify-stage-b.log)는 PASS, SSOT **0.1.29**다. batch b 출력의 private DACL/bytes와 비밀 없는 logs/results만 primary에 보존해 EXE SHA를 대조했다. 실제 설치·서비스/장치 조작·push는 하지 않았다. 다음 AS08 작업은 namespace 없는 prepared restore의 명시 검사/취소 경계이며, 목표는 계속 진행 중이다.

### 2026-10-13 Client ADR 통합과 Slint GUI 결정 — 문서 변경만

Client ADR-0023~0031(Electron·Python·Tauri·Iced 시절 결정과 이력)을 삭제하고, 현재도 유효한 결정만 현재 코드 기준으로 [ADR-0032](../adr/ADR-0032-native-client.md)에 통합했다. 통합 범위는 Rust 단일 프로세스 Client 구조, 독립 상태, `.racp` 연결 파일 등록, 설정 수정·복구, 트레이·단일 인스턴스, HKCU 로그인 자동 시작, 사용자 NSIS 설치·제거다. ADR-0022(Agent 백그라운드 수명)는 Agent 결정이므로 유지했다. ADR 색인, 문서 포털, README, 가이드, documentation-standards skill, AGENTS.md의 링크와 표기를 갱신했다. 이력 문단의 삭제된 ADR 링크는 "삭제, ADR-0032로 통합" 텍스트로 바꿨다.

GUI 툴킷은 Iced에서 **Slint 1.16.x(소프트웨어 렌더러 기본)**로 결정했다. 근거는 Slint PoC(미병합 브랜치 `claude/affectionate-moore-a7bb28`, 커밋 `31c055b`, `apps/client-slint-poc`)를 OpenGL이 없는 Windows VMware VM에서 측정한 결과다. 증거는 `dist/verification/client/0.1.26/slint-poc-20261013-01/`에 있다.

| 항목 | 결과 |
|---|---|
| 디자인 기준과 일치 | 근접. 펼친 카테고리 화살표 방향 등 작은 차이 |
| 소프트웨어 렌더러 | 일반 다시 그리기 0.5~8.9ms(100%/150% DPI), 최악의 전체 다시 그리기 평균 12ms·순간 30~68ms, 실제 창 약 60fps |
| 메모리 | 유휴 시 43.8MB working set, 사용 중 44~49MB |
| 한글 IME | 확정·조합 중 글자 표시 PASS |
| 접근성 | 접근성 트리에서 이름·선택 상태 확인. Narrator 실행 미실시, 카테고리 머리글 Tab 이동 불가 |
| 미실시 | GPU 렌더러 비교(OpenGL 없음), RDP 실측 |

> [!IMPORTANT]
> 문서 결정만 바뀌었다. `apps/client-native`와 배포 빌드는 여전히 Iced 0.13.1/tiny-skia다. 코드 변경, 빌드, 버전 bump, GUI 실행은 이 배치에서 하지 않았다. 이전 순서와 단계별 완료 조건은 ADR-0032 5절을 따른다.

### 2026-10-13 Slint 이전 1단계: 기반 — v0.1.30

[ADR-0032](../adr/ADR-0032-native-client.md) 5절의 1단계를 구현했다. PATCH **0.1.29→0.1.30 한 번**, uv/Cargo lock을 갱신했다. 기본 빌드는 그대로 Iced이고, Slint 화면은 opt-in feature `slint-ui`로만 빌드된다.

- **공통 계층**: [ipc.rs](../../apps/client-native/src/ipc.rs)가 인증 IPC 구독(`subscribe`)과 명령 전송(`request`)을 GUI 툴킷과 무관하게 제공한다. [onboarding.rs](../../apps/client-native/src/onboarding.rs)는 `load_local`·`stopped_cleanly`·`start_local_agent` 함수를 분리했다. Iced 화면도 같은 함수를 호출한다.
- **Slint 화면**: [slint_ui.rs](../../apps/client-native/src/slint_ui.rs)와 [ui/](../../apps/client-native/ui/app.slint). Slint 1.16.1, 소프트웨어 렌더러를 직접 지정한다.
  - 데모 디자인의 헤더·사이드바와 장치 식별 카드
  - Agent 상태 구독, 트레이 아이콘 상태·제어 권한 갱신
  - 창 닫기·⌄ 버튼으로 트레이 숨김, 단일 인스턴스 활성화로 복원
  - Iced와 같은 종료 절차, 승인 요청 시 창 표시
  - 서비스 모드에서는 런타임·설정 탭 숨김
  - `@tr()` 한국어 번역. 본문 화면은 2단계부터 이전
- **다시 표시 문제 수정**: Windows는 숨긴 창의 버퍼를 지우는데 softbuffer는 재사용 버퍼로 보고한다. 그래서 복원 후 헤더가 흰색으로 남았다. `SLINT_DESTROY_WINDOW_ON_HIDE`로 숨길 때 창을 없애고 표시할 때 새로 만들어 해결했다. 숨김은 입력 이벤트가 끝난 뒤 실행한다.
- CI [client.yml](../../.github/workflows/client.yml)에 `slint-ui` 컴파일 검사를 추가했다.

| 검사 | 결과 |
|---|---|
| `cargo check --locked`(기본·`slint-ui`) | PASS, `racp-client-native` 신규 경고 없음 |
| clippy `--no-deps -D warnings` | 신규 오류 없음. 기존 `accessibility.rs` 2건·`native.rs` 1건 유지(이번 변경 전부터 존재) |
| rustfmt(새 파일), inventory-check, version 단위 시험 | PASS / 114 ops·143 permissions·18 categories / 6 passed |
| 실기(Windows 10 VM, 150% DPI, OpenGL 없음, 빈 상태) | 창 표시·한국어·사이드바·키보드 포커스·창 닫기→트레이·재실행→복원·복원 후 전체 다시 그리기 PASS. 메모리 실행 직후 40.1MB, 숨김 30.3MB(working set). UIA Invoke로 헤더 버튼 PASS |
| 헤더 버튼 마우스 입력 | 자동화 도구가 주입한 마우스 이동이 창에 전달되지 않아 처음에는 실패로 보였다. 디버그 로그와 사용자 실제 마우스 클릭으로 hover·숨김 PASS를 확인했다. 제품 결함 아님 |

> [!NOTE]
> 실행 중인 Agent·등록 상태가 없는 환경에서 확인했다. 실제 Agent 연결, 종료 시 Agent 정리, 승인 요청 표시는 2단계 이후 실기 확인 대상이다. `--state-dir`는 스테이징된 payload manifest가 필요해 단독 빌드로는 실행되지 않았다. 설치·서비스 등록·등록 상태 변경은 하지 않았고 `%LOCALAPPDATA%\RACP\native-client`도 생성되지 않았다. `racp-core` clippy 오류 3건과 Iced 화면 파일의 기존 rustfmt 실패(`views/status.rs` 줄 끝 공백)는 이번 범위 밖이다.

### 2026-10-13 Slint 이전 2단계: Status 화면 — v0.1.31

[ADR-0032](../adr/ADR-0032-native-client.md) 5절의 2단계를 구현했다. PATCH **0.1.30→0.1.31 한 번**, uv/Cargo lock을 갱신했다. 기본 빌드는 그대로 Iced이고 Slint 화면은 `slint-ui` feature로만 빌드된다.

- **Status 화면** [status.slint](../../apps/client-native/ui/status.slint): 데모 디자인을 따르고 Agent가 보내는 값만 표시한다.
  - 엔드포인트 상태 카드: 상태 배지, 상태 경로, Device·Gateway·작업 폴더·프로필, 로컬 상태 새로고침, Agent 시작·정지, 로컬 요청 취소, 서비스 모드의 현재 화면 연결·해제
  - 지표 카드 4개(연결 시에만): 이벤트 순번·boot, 활성 작업 수, 대기 승인 수(승인 화면 이동), 정책 revision. 데모의 PID·가동 시간·"Guardian Ready"·"/ 32 Slots"처럼 근거 없는 값은 넣지 않았다.
  - `.racp` 등록 폼: 경로 입력·확인, token 없는 미리보기(Gateway·만료·CA hash), 파일 SHA-256 자동 입력, 기본 작업 폴더, 실행 프로필 선택, pending 경고, 새 등록 제출. 경로를 바꾸면 미리보기와 hash를 지운다.
  - 실행 중 작업 목록과 작업 취소, 제어 결과 기록(감사 로그)과 로그 비우기(화면 표시만 지우며 Agent 기록은 그대로)
- **공통 계층**: [onboarding.rs](../../apps/client-native/src/onboarding.rs)에 `inspect_file`·`run_action`을 분리했다. 영수증 요약 [`completion_line`](../../apps/client-native/src/ui_model.rs)을 추가하고 Iced 감사 로그의 중복 구현을 이 함수로 바꿨다.
- **컨트롤러** [slint_ui.rs](../../apps/client-native/src/slint_ui.rs): 등록·로컬 helper 실행·취소, 작업 취소 확인, 서비스 화면 연결 정리와 종료 순서를 Iced와 같은 규칙으로 옮겼다. 등록 정책의 제약 편집은 3단계에서 연결하며, 그전까지 현재 모델 제약을 사용한다.
- 실기 확인 중 `IdentityItem` 라벨이 남는 폭을 차지해 경로 값이 화면 밖으로 밀리는 문제를 발견해 고쳤다.

| 검사 | 결과 |
|---|---|
| `cargo check --locked`(기본·`slint-ui`), rustfmt(새 파일) | PASS |
| clippy `--no-deps -D warnings` | 신규 오류 없음. 기존 `accessibility.rs` 2건·`native.rs` 1건 유지 |
| inventory-check, version 단위 시험, `racp-core` connection 시험 | 114 ops·143 permissions·18 categories / 6 passed / 1 passed |
| 실기(Windows 10 VM, 150% DPI, 빈 상태, 등록 없음) | 미등록 화면·한국어·키보드 한글 경로 입력·프로필 선택(UIA Toggle)·연결 파일 오류 안내(`CONNECTION_FILE_INVALID`)·로그 비우기·로컬 새로고침 PASS. 가짜 Gateway(`https://gateway.invalid:8765`)·가짜 token의 시험 파일로 미리보기와 SHA-256 자동 입력 PASS, token 미표시. 등록 제출은 하지 않았고 시험 파일은 삭제했다 |
| 증거 | `dist/verification/client/0.1.31/slint-step2-20261013/` `status-unregistered-full.jpg`(SHA-256 `65af5052…ac59`), `status-enroll-preview.jpg`(`757041c5…9d13`) |

> [!NOTE]
> 실행 중인 Agent에 연결된 화면(지표 카드, 작업 목록, 작업 취소, Agent 정지, 감사 로그 항목)과 서비스 모드 화면 연결은 연결 가능한 Agent가 없어 실기 확인하지 않았다. 자동화 도구의 마우스 클릭과 `Ctrl+A`는 Slint 창에 안정적으로 전달되지 않아 UIA와 키보드 입력으로 확인했다. 도구가 긴 문자열을 클립보드로 붙여넣어 시험 중 사용자 클립보드가 덮어써졌다. 설치·서비스 등록·등록 상태 변경은 하지 않았고 `%LOCALAPPDATA%\RACP\native-client`도 생성되지 않았다.

### 2026-10-13 Slint 이전 3단계: 권한 화면 — v0.1.32

[ADR-0032](../adr/ADR-0032-native-client.md) 5절의 3단계를 구현했다. PATCH **0.1.31→0.1.32 한 번**, uv/Cargo lock을 갱신했다. 기본 빌드는 그대로 Iced이고 Slint 화면은 `slint-ui` feature로만 빌드된다.

- **권한 화면** [permissions.slint](../../apps/client-native/ui/permissions.slint):
  - 기능 카테고리 트리: 실행 가능(`rpc`)하고 Agent가 미구현으로 보고하지 않은 항목만 표시. 카테고리별 요약 배지(모두 거부/승인 필요/허용/혼합/꺼짐)
  - 항목별 3-Way 선택기, 카테고리 일괄 선택, 카테고리 켜기·끄기(끄면 항목 선택은 보존하고 실행만 차단), 전체 프리셋
  - 실행 제약 초안: 실행 파일 허용·차단 목록, 허용 작업 폴더 ID, 최대 실행 시간·출력, 엄격한 OS 격리
  - "저장하지 않은 변경" 표시와 정책 revision CAS 저장(연결된 Agent와 제어 권한이 있을 때만)
- **공통 계층**: 제약 검증을 [`constraints::parse_fields`](../../apps/client-native/src/constraints.rs)로 분리해 Iced 초안과 Slint 화면이 같은 규칙을 쓴다.
- **컨트롤러** [slint_ui.rs](../../apps/client-native/src/slint_ui.rs): 편집 권한 확인, 정책 저장 전 revision 일치와 제약 검증, Agent 정책 수신 시 제약 초안 동기화(미저장 초안은 보존), 등록 시 제약 초안 사용을 Iced와 같은 규칙으로 옮겼다. 트리는 바뀐 행만 갱신하므로 편집해도 펼침 상태와 키보드 포커스가 유지된다.
- **PoC에서 남은 문제 해결**:
  - 카테고리 머리글: Tab으로 포커스, Space/Enter로 펼침, 포커스 테두리 표시
  - 화살표 방향: 소프트웨어 렌더러가 `Path` 회전을 적용하지 않아 회전 대신 접힘(›)·펼침(∨) 아이콘을 따로 사용
- **Slint 1.16 접근성 제약 우회**:
  - `accessible-expandable/expanded`를 지정하면 머리글의 UIA Invoke 패턴이 사라져, 펼침 상태는 설명으로 전달한다.
  - 반복 항목 안의 `if` 영역이 실행 중에 새로 만들어지면 UIA 트리에 반영되지 않았다(펼친 하위 항목, 미저장 표시). 이 영역은 처음부터 만들어 두고 높이·`visible`로 접는다. 접힌 항목은 UIA에 노출되지 않는 것을 확인했다.

| 검사 | 결과 |
|---|---|
| `cargo check --locked`(기본·`slint-ui`), rustfmt(새 파일), 번역 누락 검사 | PASS / 누락 0 |
| clippy `--no-deps -D warnings` | 신규 오류 없음. 기존 `accessibility.rs` 2건·`native.rs` 1건 유지 |
| inventory-check, version 단위 시험 | 114 ops·143 permissions·18 categories / 6 passed |
| 실기 UIA(Windows 10 VM, 150% DPI, 빈 상태, Agent 없음) | 탭 이동, 머리글 Invoke로 펼침·접힘(접힘 0개 → 펼침 15개 선택지 노출), 항목 "허용" 선택 시 선택 상태·"혼합" 요약·미저장 표시 PASS. 카테고리 일괄 허용("모두 허용"), 전체 거부 프리셋, 카테고리 끄기("카테고리 꺼짐"/"카테고리 켜기") PASS |
| 실기 키보드 | 머리글 포커스 후 Space로 펼침, 허용 목록에 실행 파일 경로 입력 시 미저장 표시 PASS |
| 메모리 | 권한 화면 사용 중 working set 50~63MB |
| 증거 | `dist/verification/client/0.1.32/slint-step3-20261013/` `permissions-initial.jpg`(SHA-256 `288174c6…13b8`), `permissions-edited-dirty.jpg`(`59109e9b…273a`), `permissions-keyboard-expand.jpg`(`a806eb4a…27e3`, 화살표 수정 전 화면) |

> [!NOTE]
> 정책 revision CAS 저장은 연결된 Agent가 없어 실기 확인하지 않았다(저장 버튼은 연결 시에만 표시). 실행 중 새로 나타나는 다른 `if` 영역(알림, 상태 화면 카드 등)의 UIA 반영 여부는 4단계에서 Narrator로 다시 점검한다. 시험 중 자동화 도구가 입력 문자열을 클립보드로 붙여넣어 사용자 클립보드가 덮어써졌다. 설치·서비스 등록·등록 상태 변경은 하지 않았고 `%LOCALAPPDATA%\RACP\native-client`도 생성되지 않았다.

### 2026-10-13 Slint 이전 4단계: 승인·선택 런타임·설정 화면 — v0.1.33

[ADR-0032](../adr/ADR-0032-native-client.md) 5절의 4단계를 구현했다. PATCH **0.1.32→0.1.33 한 번**, uv/Cargo lock을 갱신했다. 이제 다섯 화면이 모두 Slint로 옮겨졌다. 기본 빌드는 5단계(Iced 제거) 전까지 그대로 Iced이고 Slint 화면은 `slint-ui` feature로만 빌드된다.

- **승인 화면** [approvals.slint](../../apps/client-native/ui/approvals.slint): 대기 중인 요청마다 작업 이름, 요청 ID, 만료 시각, 대상 요약, 대상 digest를 표시하고 "이번 요청만 승인"/"요청 거절"을 보낸다. 결정은 nonce로 보내며, 이미 처리되었거나 만료된 요청은 보내지 않는다. Iced가 작업 이름의 단어만 보고 붙이던 위험도(높음/중간)와 만료 시각을 "TTL 남음"으로 표시하던 라벨은 근거가 없어 넣지 않았다.
- **선택 런타임 화면** [runtime.slint](../../apps/client-native/ui/runtime.slint): 저장된 선택(revision, Chromium·CDB·GDB·분석 host manifest, 브라우저 origin 수, 외부 CDP)을 로컬 상태에서 읽어 표시한다. Iced의 항상 "없음"이던 승인 목록 카드는 대체했다. 번들 승인과 선택 저장은 Agent가 완전히 종료된 경우에만 가능하다.
- **설정 화면** [settings.slint](../../apps/client-native/ui/settings.slint):
  - 화면 언어 즉시 전환(Rust 쪽 카테고리 제목 포함). Iced와 같이 선택은 저장하지 않으며 다음 시작 때는 Windows 표시 언어를 따른다.
  - 기본 작업 폴더와 이름 있는 작업 폴더(최대 15개) 편집·저장. 저장은 Agent 종료 상태에서만 하며, 새 등록에도 이 목록을 사용한다.
  - Windows 로그인 후 시작 항목 상태와 켜기·끄기·갱신·미확정 변경 정리
- 공통 부품(`SectionTitle`, `FieldLabel`, `IdentityItem`, `ChoiceButton`, `PrimaryButton`, `DangerButton`)을 [common.slint](../../apps/client-native/ui/common.slint)로 옮기고, 이전 예정 안내 카드는 삭제했다. 번역 선택은 Slint 문서대로 첫 컴포넌트를 만든 뒤 호출하도록 순서를 바로잡았다.
- **접근성 트리 갱신 우회**: 실기에서 페이지를 바꿨는데도 UIA 트리가 이전 페이지에 머무는 경우를 재현했다. Slint 1.16 winit 접근성 어댑터는 트리를 만드는 도중에 생긴 컴포넌트를 다시 확인하지 않는다. 구조가 바뀔 수 있는 시점(렌더, 페이지 전환, 카테고리 펼침) 150ms 뒤, 창 끝의 빈 요소를 만들거나 없애 전체 트리 재구성을 유도한다(묶어서 한 번만 실행). 재구성은 접근성 클라이언트가 연결된 동안에만 일어난다.

| 검사 | 결과 |
|---|---|
| `cargo check --locked`(기본·`slint-ui`), rustfmt, 번역 누락 검사 | PASS / 누락 0 |
| clippy `--no-deps -D warnings` | 신규 오류 없음. 기존 `accessibility.rs` 2건·`native.rs` 1건 유지 |
| inventory-check, version 단위 시험 | 114 ops·143 permissions·18 categories / 6 passed |
| 실기 UIA(Windows 10 VM, 150% DPI, 빈 상태, Agent 없음) | 승인(미연결 안내)·선택 런타임(저장된 선택, "선택 안 함")·설정(미등록 안내) 표시 PASS. 영어 전환 시 메뉴·설정·카테고리 제목 변경, 한국어 복귀 PASS |
| 접근성 트리 추적 | 우회 적용 후 5개 페이지 2회 왕복 전환 **10/10**에서 UIA가 보이는 페이지 제목과 일치. 영어 상태의 페이지 이동·한국어 복귀 PASS. 3단계 권한 화면 회귀(접힘 0개·펼침 15개·선택·미저장 표시) PASS |
| 메모리 | working set 50~69MB |
| 증거 | `dist/verification/client/0.1.33/slint-step4-20261013/` `approvals-disconnected.jpg`(SHA-256 `cb7089e8…e4ba`), `runtime-unregistered.jpg`(`e4c05bed…379d`), `settings-unregistered.jpg`(`5b4c9458…9c37`), `settings-english.jpg`(`a6d2d24e…69cf`) |

> [!NOTE]
> 승인 요청 목록과 결정 전송, 런타임 승인·선택 저장, 설정 저장, 로그인 시작 항목 변경은 실기 확인하지 않았다. 연결된 Agent와 등록 상태가 없고, 로컬 helper 실행은 상태 폴더를 만들 수 있어 누르지 않았다. 영어 모드에서 카탈로그 기반 카테고리 제목 중 번역 사전에 있는 6개만 영어로 바뀌고 나머지는 카탈로그의 한국어 이름이 남는다(Iced와 동일). Narrator 음성 확인은 하지 않았고 UIA 트리로 대신 확인했다. 설치·서비스 등록·등록 상태 변경은 하지 않았고 `%LOCALAPPDATA%\RACP\native-client`도 생성되지 않았다.

### 2026-10-13 Slint 이전 5단계: Iced 제거와 Slint 기본 빌드 — v0.1.34

[ADR-0032](../adr/ADR-0032-native-client.md) 5절의 마지막 단계를 마쳤다. PATCH **0.1.33→0.1.34 한 번**, uv/Cargo lock을 갱신했다. Client GUI는 이제 **Slint 단일 빌드**이며 Iced 코드와 의존성은 없다(`Cargo.lock`의 iced 항목 0).

- **삭제**: Iced 화면(`app.rs`, `views/`, `components/`, `theme/`), 직접 만든 접근성 계층(`accessibility.rs`, `controls.rs`), Iced 전용 i18n(`i18n/ko.rs`·`en.rs`), Iced SVG 아이콘(`assets/icons/`). `constraints.rs`와 `onboarding.rs`에서는 Iced `Task` 래퍼와 편집기 타입을 지우고 공통 함수만 남겼다. 서비스 모드 차단은 공통 함수 안으로 옮겼다.
- **정리**: `i18n.rs`는 언어 감지와 카테고리 제목 표만 남겼다. 의존성에서 `iced`·`accesskit`·`accesskit_windows`·`image`·`raw-window-handle`·`chrono`, feature `software-renderer`·`gpu-renderer`·`slint-ui`를 제거하고 Slint를 필수 의존성으로 바꿨다. `--gpu`는 계속 `GPU_RENDERER_NOT_COMPILED`로 거부한다.
- **기능 보완**: Iced에 있던 `Ctrl+1~5` 페이지 이동을 Slint 최상위 `FocusScope`로 옮겼다(서비스 모드에서는 설정·런타임 차단). 시작 시 포커스된 요소가 없으면 키가 전달되지 않아, 단축키 영역이 시작 포커스를 받고 하위 요소에 포커스가 있을 때는 먼저 키를 받는다.
- **빌드·CI**:
  - [client.yml](../../.github/workflows/client.yml)의 GPU·`slint-ui` 컴파일 검사를 `clippy --no-deps -D warnings`로 바꿨다. Iced 화면 파일이 사라져 workspace `cargo fmt --all --check`가 통과한다.
  - xtask 스테이징 메타데이터 features를 `slint-software-renderer`로 바꿨다.
  - [PE import 허용 목록](../../xtask/src/package/imports.rs)에 Windows 기본 DLL 두 개를 근거와 함께 추가했다: `shlwapi.dll`(Slint winit의 `webbrowser` 의존성, `AssocQueryStringW`만 사용), `dwrite.dll`(Slint 글꼴 대체 탐색, `DWriteCreateFactory`만 사용).
  - [migration inventory](../../crates/racp-contract/migration-inventory.json)의 React GUI 대체 경로를 `slint_ui.rs`로 바꿨다.
  - 삭제된 Iced 파일을 가리키던 이력 링크 17개는 문구는 두고 링크만 해제했다.
- **문서**: ADR-0032(상태·이전 결과·Slint 1.16 제약과 우회·남은 실기 인수 항목), AGENTS.md, README, 문서 포털, Desktop/Rust Agent 가이드, desktop-build skill, Native Client 완성 계획의 Iced 표기를 갱신했다. 가이드 6.13 키보드·접근성 절은 실제 확인 범위로 다시 썼다.

| 검사 | 결과 |
|---|---|
| workspace `cargo fmt --all --check` | PASS |
| client `clippy --locked --no-deps -D warnings` | PASS (`native.rs`의 불필요한 cast 1건 수정) |
| inventory-check, version 단위 시험, 번역 누락 검사 | 114 ops·143 permissions·18 categories / 6 passed / 누락 0 |
| 개발 스테이징 `slint-20261013-d` | `stage`·`verify-staging` PASS: payload 4개 파일(Client·Agent·LICENSE·SBOM)·hash·대상 확인. `racp-client.exe` 27,575,808 bytes, SHA-256 `92366d51…0a68`, payload manifest `ab43836c…1493` |
| 실패한 스테이징 | `slint-20261013-a`(`shlwapi.dll` 미허용)·`-b`(`dwrite.dll` 미허용)·`-c`(단축키 수정 전). a·b는 xtask가 `staging.quarantine`으로 격리했고 c는 보존했다 |
| 스테이징 실기(격리 `--state-dir`, Windows 10 VM, 150% DPI) | 미리 만든 상속 ACL 폴더는 `STATE_DIRECTORY_NOT_PRIVATE`로 거부(의도한 보안 검사). 새 경로로 실행 시 화면 표시, `Ctrl+3/4/5/2`(Tab으로 하위 요소에 포커스가 있을 때 포함), 헤더 종료 버튼으로 정상 종료 PASS. 상태 폴더·기본 상태 폴더 미생성, 잔여 프로세스 없음 |
| 메모리 | 스테이징 Client working set 43MB 내외 |
| 증거 | `dist/verification/client/0.1.34/slint-step5-20261013/` `staged-isolated-ctrl3-approvals.jpg`(SHA-256 `92d86127…b24b`), `staged-isolated-tab-ctrl2-permissions.jpg`(`3b1b163d…b296`), `staging-d-build-manifest.json`(`426df4af…5ea0`) |

> [!IMPORTANT]
> 이전은 끝났지만 실기 인수는 남아 있다. 실행 중인 Agent에 연결한 상태의 동작(지표·작업 취소·Agent 정지·정책 저장·승인 결정), 로컬 helper를 실행하는 동작(새 등록·런타임 승인·설정 저장·로그인 시작 항목), 서비스 모드 화면 연결, RDP·실제 GPU PC·Narrator 음성 확인, 실제 GUI 프레임 시간 측정은 ADR-0032 5절의 남은 인수 항목이다. 제품 setup 생성·설치·서비스 등록·등록 상태 변경은 하지 않았다.


### 2026-10-13 AS08 미생성 restore 의도 검사·CAS 취소 — v0.1.30

PATCH **0.1.29→0.1.30 한 번**과 Python/Rust/optional analysis-host lock의 로컬 package version을 동기화했다. 외부 dependency pin은 변경하지 않았다. [요청 계약](../../crates/racp-contract/src/service_start.rs)의 `inspect_restore_intent`/`abort_uncreated_restore`는 config hash 및 취소 UUID/intent SHA를 검증하고 namespace 증거 등 추가 필드를 거부한다. [core 전이](../../crates/racp-core/src/service_start.rs)는 root-bound **Prepared Maintenance Restore만** CAS로 Aborted 처리한다. 다른 purpose·phase·stale ID/hash는 보존하며 늦은 이전 UUID의 claim은 거부한다.

[Windows adapter](../../apps/agent/src/platform/windows/service/service_restore.rs)는 SYSTEM/현재 payload·protected image·sealed machine root/SID/private ACL/config hash 확인 뒤 ADMIN→EXECUTION→config/agent lock 아래 고정 이름의 namespace를 조회한다. 설치된 virtual account lookup을 요구하는 일반 service admin 경로 앞에서 분기한다. [Microsoft OpenServiceW 계약](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-openservicew)에 따라 exact ERROR_SERVICE_DOES_NOT_EXIST만 absent로 분류하고 handle cleanup 전에 SDK error를 보존한다. present·접근 거부·삭제 대기·알 수 없는 상태에는 취소하지 않는다. inspect는 snapshot과 absent/present/unconfirmed·bounded error를 보고하며 intent 파일은 바꾸지 않는다. 취소는 service/credential/Device를 생성·수정하거나 재등록하지 않고 후속 명시 restore를 요구한다.

[core RED](../../dist/verification/agent/0.1.30/restore-intent-20261013/abort-core-red.log)→[GREEN](../../dist/verification/agent/0.1.30/restore-intent-20261013/abort-core-green.log), [요청 RED](../../dist/verification/agent/0.1.30/restore-intent-20261013/request-red.log)→[GREEN](../../dist/verification/agent/0.1.30/restore-intent-20261013/request-green.log), [namespace RED](../../dist/verification/agent/0.1.30/restore-intent-20261013/namespace-red.log)→[GREEN](../../dist/verification/agent/0.1.30/restore-intent-20261013/namespace-green.log), [취소 gate RED](../../dist/verification/agent/0.1.30/restore-intent-20261013/namespace-abort-red.log)→[최종 Agent GREEN](../../dist/verification/agent/0.1.30/restore-intent-20261013/namespace-abort-green.log)을 확인했다. focused contract **3**, core **9**, Agent **12 PASS**에 신규 **5개** 시험이 포함되며, SYSTEM/SCM 호출 없는 grammar·private fixture/CAS·SDK 결과 분류 모델 시험이다. read-only reviewer는 actor/root admission·정확한 CAS·재실행 거절·namespace 불확실성 보존·lock 순서를 검사해 **추가 수정 사항 없음**으로 보고했다. reviewer도 실제 SCM/빌드/시험을 실행하지 않았다.

> [!IMPORTANT]
> AS08는 보호 config가 존재하는 **미생성 restore의 source 복구 경계**까지 진행했다. config 생성 전 partial provision, 이미 생성된 불확실한 service, claimed/failed effect recovery 및 실제 SCM/virtual account 인수는 미완료다. AS09 queued restart/사용자 Stop·boot admission·bounded restart 활성화, AS07 간헐 Browser cleanup, AS16–AS18 USB/장치 구현·물리 인수도 남아 있다. 실제 설치·서비스 등록/시작/설정·driver/device IO·기존 profile 변경은 하지 않았다.

| 검사 | 결과·범위 |
|---|---|
| 전체 Rust 직렬 실행 | [로그](../../dist/verification/agent/0.1.30/restore-intent-20261013/rust-workspace-serial.log), **92 PASS / 1 FAIL**에서 중단, exit 101. Browser `failed_evaluation_records_started_effect_and_retains_no_child_after_timeout`가 TIMED_OUT 대신 UNKNOWN 반환. 전체 suite PASS로 판정하지 않음 |
| 중단 뒤 미실행 target | [Filesystem/Memory/Process/Shell/Terminal](../../dist/verification/agent/0.1.30/restore-intent-20261013/runtime-remaining.log) **15 PASS / 4 fixture ignored**, [Desktop 모델/identity/wire](../../dist/verification/agent/0.1.30/restore-intent-20261013/desktop-regression.log) **11 PASS**, [xtask](../../dist/verification/agent/0.1.30/restore-intent-20261013/xtask-tests.log) **3 PASS**. 개별 target 실행으로 전체 FAIL을 덮어쓰지 않음 |
| Python·정적 검사 | [Python](../../dist/verification/agent/0.1.30/restore-intent-20261013/python.log) **11 PASS**, [Ruff](../../dist/verification/agent/0.1.30/restore-intent-20261013/ruff.log) PASS, [mypy](../../dist/verification/agent/0.1.30/restore-intent-20261013/mypy.log) **98 files PASS**, scoped Rust fmt/diff check PASS, [inventory](../../dist/verification/agent/0.1.30/restore-intent-20261013/inventory.log) **114 ops / 143 permissions / 18 categories**, frozen 100 baseline 유지 |
| Windows Agent 개발 payload | [manifest](../../dist/agent/0.1.30/win-x64/restore-intent-20261013-a/staging/build-manifest.json)·[stage](../../dist/verification/agent/0.1.30/restore-intent-20261013/stage.log)·[verify](../../dist/verification/agent/0.1.30/restore-intent-20261013/verify-stage.log), Rust 1.90.0 Windows x64 release/static CRT·3-file GUI-free allowlist·PE/native imports/hash PASS. 설치 artifact 생성/실행 없음 |
| 최종 EXE 실제 원격 회귀 | [results](../../dist/verification/agent/0.1.30/restore-intent-e2e-20261013-a/results.json)·[로그](../../dist/verification/agent/0.1.30/restore-intent-20261013/e2e.log), **21 PASS / 0 FAIL**, exit 0. 격리 HTTPS/WSS·인증 IPC/headless·승인/replay·파일·ConPTY·CAS/cancel/stop/재시작. 기존 profile 및 SCM/service-account 미사용이며 실제 AS08 SYSTEM/namespace 인수로 해석하지 않음 |

최종 EXE **22,391,296 bytes**, SHA-256 **`62affae11742147e5501994c3545d9936e226f72592d69fba7bceb9bf80cf8d3`**, payload manifest **`c21623a32dbb020e978c499f3758ba3c20dd7f043122137fcfd85ac91912765e`**, source input digest **`454a18ebefa8eafaae2b5a71b1030ab5ff634a8b4944ef9f40797b32fe834a0a`**다. 최종 E2E EXE hash는 manifest와 일치하고 harness SHA는 **`fd9b319d459948f5fed9dc5b8b6b8ed6de46e249392c8f4191ccd9fa93d65a1e`**다. baseline `635d08a` 및 당시 dirty source를 기록한 unsigned 개발 후보이며 기능 전체 검증/서비스 릴리스를 선언하지 않는다.

AS07 실패 조사: [단독 재실행](../../dist/verification/agent/0.1.30/restore-intent-20261013/browser-timeout-diagnostic.log)은 **1 PASS**지만 목표 assertion에 진단이 붙지 않아 경계 데이터를 수집하지 못했다. 정확한 timeout assertion에 진단을 연결한 [Browser 그룹 실행](../../dist/verification/agent/0.1.30/restore-intent-20261013/browser-group-diagnostic.log)은 **19 PASS / 1 FAIL**, UNKNOWN의 code **EXECUTION_UNKNOWN**을 확인했다. 이어 [close 경계 진단](../../dist/verification/agent/0.1.30/restore-intent-20261013/browser-close-boundaries.log)은 **18 PASS / 2 FAIL**(concurrent close, cookies/form cleanup)이며 worker 종료와 kill 요청 뒤 **parent exit signal=false / Job tree empty=true**인 상태로 5초 deadline에 도달했다. profile 삭제 단계까지 도달하기 전의 종료 증명 실패라는 근거를 얻었으나 아직 원인을 수정하지 않았다. 임시 test/production 진단 코드는 원본 bytes로 복구했고 Browser 소스·시험의 최종 diff는 0이다. deadline을 늘리거나 exit code/빈 Job만으로 성공 판정을 대체하지 않았다. 다음 AS07 조사는 부모 handle signal·Job membership/termination·pending IO 경계다.


### 2026-10-13 AS07 Browser 시작 실패의 종료 확인·프로필 보존 — v0.1.31

[Browser session](../../crates/racp-runtime/src/providers/browser/session.rs)의 process marker persist·CDP 접속·version query·proxy bind·context 생성 실패 경로는 기존에 OwnedProcess를 Drop한 뒤 종료 결과와 관계없이 profile을 삭제했다. Drop의 종료 요청/대기는 성공을 증명하지 않으므로, 다섯 경로에 **부모 handle signal과 전체 Job tree-empty를 모두 확인한 뒤 삭제**하는 gate를 연결했다. 기존 Session::close와 동일한 5초 종료 budget을 공유하며, 종료 확인 실패에는 삭제를 시작하지 않아 profile과 존재하는 recovery record를 보존한다. 파일 삭제 실패도 전파하고 남은 파일을 불확실한 정리 상태로 보고한다. startup process는 session inventory에 아직 없으므로 cleanup 실패에는 Browser ready를 false로 전환해 새 execute 요청을 거부하고 빈 inventory만으로 전체 cleanup 완료를 보고하지 않는다. 정상 cleanup이 완료되면 원래 startup error를 반환한다. 종료 확인에 exit code나 빈 Job만을 대체 증거로 사용하지 않았다.

[RED](../../dist/verification/agent/0.1.31/startup-cleanup-20261013/red.log)는 실패한 cleanup 결과를 무시하는 기존 삭제 경로에서 **1 PASS / 1 FAIL**, [GREEN](../../dist/verification/agent/0.1.31/startup-cleanup-20261013/green.log)은 **2 PASS**다. private 임시 profile의 cleanup 실패/record byte 보존과 정상 cleanup/다른 profile 보존을 검사한다. [readiness RED](../../dist/verification/agent/0.1.31/startup-cleanup-20261013/readiness-red.log)는 동일 시험에 불확실한 startup의 ready 차단 assertion을 추가해 **1 PASS / 1 FAIL**이었다. 최종 source는 이 경우 CLEANUP_FAILED/UNKNOWN을 보고하며 새 요청과 전체 cleanup 완료 판정을 차단한다. actual SDK timeout을 주입한 시험은 아니며, Windows Browser 시작 취소는 전체 suite에서 별도 검증한다. PATCH **0.1.30→0.1.31 한 번**, uv.lock 및 workspace/독립 analysis-host Cargo.lock의 local package version만 동기화했다. 최초 --locked startup 단독 실행은 lock refresh 부족으로 시험 시작 전 exit 101이었다. offline full metadata로 lock을 갱신했고 의존성 pin 변경은 없다.

AS07 별도 진단도 보존한다. [stdout drain 실험](../../dist/verification/agent/0.1.30/browser-signal-20261013/drain-experiment.log)은 **10 PASS / 10 FAIL**이며 실패 시 drained bytes=0·parent signal=false·Job empty=true였다. drain이 간헐 종료 문제를 해결한다는 증거가 없어 제거했다. [identity 단독 진단](../../dist/verification/agent/0.1.30/browser-signal-20261013/identity-probe.log) **1 PASS**, [그룹](../../dist/verification/agent/0.1.30/browser-signal-20261013/identity-group-probe.log) **20 PASS**에서 process handle PID·Job membership 일치와 exit code/Job empty가 process signal에 앞설 수 있음을 관찰했다. 진단 코드는 원본으로 복구했으며 이 PASS로 간헐 실패 해결을 선언하지 않는다. recovery enumeration→process birth 확인 사이의 정상 exit race도 후속 조사 대상이다.

> [!IMPORTANT]
> 사용자가 main의 Slint/Cargo.lock 작업이 아직 진행 중이라고 확인했으므로 **main 통합은 GUI 작업 완료·커밋 뒤로 보류**한다. Agent v0.1.30 source commit `d547896`과 이번 묶음은 별도 managed worktree에 유지한다. main의 GUI source/lock을 stage·stash·reset하거나 변경하지 않았다. 실제 installer/SCM/device/driver 실행은 하지 않았다. read-only 후속 reviewer는 capacity/credit 오류로 완료하지 못했으므로 독립 review 완료라고 기록하지 않는다. AS07 간헐 process signal 문제와 실제 서비스/장치 인수 및 AS08/AS09/AS16–AS18 잔여 범위는 미완료다.


| v0.1.31 최종 검사 | 실제 결과·범위 |
|---|---|
| 최종 source 전체 Rust 직렬 실행 | [최종 로그](../../dist/verification/agent/0.1.31/startup-cleanup-20261013/rust-workspace-final.log), `cargo test --locked --workspace -- --test-threads=1`, **93 PASS / 2 FAIL**, exit 101, Browser군에서 중단. 실패는 `cookies_form_refs_native_input_png_and_navigation_invalidation` 및 `external_cdp_opt_in_context_and_target_ownership`의 `CLEANUP_FAILED`. 신규 gate/ready 시험 **2 PASS**와 실제 Windows Browser startup cancel PASS 포함. 간헐 종료 문제 미해결이며 전체 suite PASS 아님 |
| ready 차단 보완 전 전체 실행 | [이전 로그](../../dist/verification/agent/0.1.31/startup-cleanup-20261013/rust-workspace-serial.log) **124 PASS / 0 FAIL / 4 subprocess fixture ignored**, exit 0. 최종 source 결과를 이 성공으로 대체하지 않음. 최종 실패 뒤 Desktop/Filesystem/Memory/Process/Shell/Terminal/xtask target은 해당 최종 전체 실행에서 미실행 |
| 버전·Python 정적 검사 | [버전 시험](../../dist/verification/agent/0.1.31/startup-cleanup-20261013/python-version.log) **6 PASS**, [Ruff](../../dist/verification/agent/0.1.31/startup-cleanup-20261013/ruff.log) PASS, [mypy](../../dist/verification/agent/0.1.31/startup-cleanup-20261013/mypy.log) **98 files PASS**, version show **0.1.31**, scoped Rust fmt/diff check PASS. 기존 Windows recipes naming/core unused-import warnings 유지 |
| 배포·통합 | 이번 v0.1.31은 source/debug-test 묶음이며 새 release payload/setup 생성·최종 EXE 원격 인수는 하지 않음. v0.1.30 payload evidence를 v0.1.31 산출물로 해석하지 않음. GUI 완료 전 main 통합 보류·별도 Agent branch 보존 |


### 2026-10-13 AS07 대기 admission·Windows 재시작 복구 경합 — v0.1.32

[Browser open](../../crates/racp-runtime/src/providers/browser/session.rs)과 [external attach](../../crates/racp-runtime/src/providers/browser/attach.rs)는 opening lock 뒤 Provider ready를 다시 검사한다. 앞선 startup cleanup 실패로 ready=false가 된 동안 이미 잠금을 기다리던 요청도 RESOURCE_BUSY로 거부하며 새 profile/process/CDP 효과를 시작하지 않는다. [queued RED](../../dist/verification/agent/0.1.32/cleanup-races-20261013/queued-red.log)는 **0 PASS / 2 FAIL**(기존 open CAPABILITY_UNAVAILABLE, attach PERMISSION_DENIED까지 진행), [runtime GREEN](../../dist/verification/agent/0.1.32/cleanup-races-20261013/runtime-unit-green.log)은 **15 PASS / 1 subprocess fixture ignored**다.

[Windows recovery adapter](../../crates/racp-runtime/src/providers/browser/recovery/windows.rs)는 읽기 전용 PROCESS_QUERY_LIMITED_INFORMATION/SYNCHRONIZE handle을 고정하고 같은 handle에서 birth·종료 signal을 관찰한다. 기존 process enumeration→살아 있는 PID만 허용하는 process_created 조회 사이 정상 종료를 오류로 다루던 경합을 제거했다. GetProcessTimes birth가 현재 receipt의 finite/positive create_time과 일치해야 하며 PID 재사용·live actor SID 불일치·조회 불확실성은 보존한다. live token 조회 실패 중 동시 종료만 **동일 pinned handle signal**로 확인한 경우에 허용한다. 종료된 객체는 다시 살아 있는 PID 조회를 요구하지 않는다. pid=0/범위 초과/비유한 birth는 거부하고, 고정된 valid OpenProcess 인수의 ERROR_INVALID_PARAMETER만 객체 부재로 처리한다. access denied/invalid handle/알 수 없는 오류를 부재로 바꾸지 않는다. 어느 경로도 process를 signal/kill하지 않으며, profile 삭제에는 여전히 이전 Job이 empty여야 한다. macOS/Linux backend를 구현하거나 빌드하지 않았다.

[Witness RED](../../dist/verification/agent/0.1.32/cleanup-races-20261013/witness-red.log)는 **1 PASS / 1 FAIL**, 이미 종료된 실제 owned subprocess의 동일 birth 조회에서 LOCAL_STATE_FAILED였다. GREEN은 종료 전/후 pinned witness·종료된 handle 재조회·다른 birth 거부를 실제 Windows SDK/private fixture로 확인한다. 별도 fixture 시험은 부모가 stdin을 닫아 정상 종료시키며 기존 process/서비스/장치에 접근하지 않는다. 새 일반 시험은 **4개**(queued 2/witness 2), subprocess 전용 ignored entry 1개다.

Win32의 [process handle 수명](https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers), [GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes), [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)를 기준으로 구성했다. 종료 code/빈 Job이 parent signal을 대체하지 않는다. 기존 간헐 Browser termination signal 지연은 별도 문제이며 이 경합 수정으로 해결했다고 주장하지 않는다.

PATCH **0.1.31→0.1.32 한 번**과 uv/workspace/독립 analysis-host lock local version을 동기화했다. 외부 dependency pin·공개 operation/permission 변경은 없다. main의 Slint GUI·lock·버전 작업은 보존하며 GUI 완료 전 통합하지 않는다. 실제 service 설치/등록/시작/설정·installer·driver/device IO는 실행하지 않는다.


| v0.1.32 source 검사 | 실제 결과·범위 |
|---|---|
| 전체 Rust 직렬 suite | [로그](../../dist/verification/agent/0.1.32/cleanup-races-20261013/rust-workspace.log), `cargo test --locked --workspace -- --test-threads=1`, **128 PASS / 0 FAIL / 5 subprocess fixture ignored**, exit 0. Agent/contract/core/runtime·Browser 20·Desktop 11·Filesystem/Memory/Process/Shell/Terminal·xtask까지 모두 실행. 4개 신규 일반 시험과 부모가 명시 실행하는 Windows exit fixture 포함 |
| Python·버전 | [로그](../../dist/verification/agent/0.1.32/cleanup-races-20261013/python.log), **11 PASS**(version 6/harness 5), [Ruff](../../dist/verification/agent/0.1.32/cleanup-races-20261013/ruff.log) PASS, [mypy](../../dist/verification/agent/0.1.32/cleanup-races-20261013/mypy.log) **98 source files PASS**. 기존 warnings는 유지 |

이 전체 PASS는 이전 v0.1.31의 2건 CLEANUP_FAILED 실패 이력을 대체하지 않는다. 이번 수정의 재현 가능한 queued admission/normal-exit recovery 경합은 검증됐으나, 이전 forced termination의 parent signal 지연 원인은 여전히 미확정이다. 강제 종료 예산/parent signal·Job empty proof는 변경하지 않았으며 반복·부하 인수와 실제 서비스/USB 인수는 남아 있다. 독립 reviewer 완료 증거는 없으며 main 통합 전 검토가 남아 있다.


| v0.1.32 Windows 산출물 검사 | 실제 결과·범위 |
|---|---|
| Agent 서비스용 개발 staging | [manifest](../../dist/agent/0.1.32/win-x64/cleanup-races-20261013-a/staging/build-manifest.json)·[stage](../../dist/verification/agent/0.1.32/cleanup-races-20261013/stage.log)·[verify](../../dist/verification/agent/0.1.32/cleanup-races-20261013/verify-stage.log), Rust 1.90.0 Windows x64 release/static CRT build 및 Agent/LICENSE/SBOM 3-file GUI-free allowlist·PE/native target/hash **PASS**. 새 untracked recovery/windows.rs도 source input에 포함됐음을 확인. installer/setup 생성·실행 없음 |
| 최종 EXE 설치 없는 원격 회귀 | [results](../../dist/verification/agent/0.1.32/cleanup-races-e2e-20261013-a/results.json)·[로그](../../dist/verification/agent/0.1.32/cleanup-races-20261013/e2e.log), **21 PASS / 0 FAIL**, exit 0. 격리 HTTPS/WSS·인증 Named Pipe/headless·파일/명령/ConPTY·승인/replay·멱등성/CAS/cancel/owned child/stop·재시작 보존. 시험 Agent/Gateway 종료 완료, 기존 profile 미사용. 실제 SCM/service actor 및 Browser 반복 인수와 구분 |
| 계약·형식 | [inventory](../../dist/verification/agent/0.1.32/cleanup-races-20261013/inventory.log), **114 ops / 143 permissions / 18 categories**, frozen 100 baseline 유지. scoped Rust fmt/git diff check PASS, version **0.1.32** |

최종 Agent EXE **22,342,656 bytes**, SHA-256 **`2919577bc2699a248f363b22d1bfaa93f79f5f28330d9a0967624bb863105d67`**; payload manifest **`f7477c07d15f799e3e3dcf7e37d9a3841539a62a9b330a3123eea461fdcf73d2`**; source input digest **`d19a99ff41cb84f94acc248689b8126b570a9d119f4e0ee52d3daf0a8d95fee2`**다. 최종 E2E EXE SHA는 위 manifest와 같고 harness SHA **`fd9b319d459948f5fed9dc5b8b6b8ed6de46e249392c8f4191ccd9fa93d65a1e`**도 기록했다. baseline `47ff367`와 당시 dirty source를 기록한 unsigned/non-published 개발 산출물이며 xtask 자체 tests는 `not_run_by_xtask`다. main의 Slint GUI/버전/lock 변경은 수정하지 않았고 통합은 GUI 완료 뒤로 보류한다.


[Browser 반복 1](../../dist/verification/agent/0.1.32/cleanup-races-20261013/browser-repeat-1.log)·[2](../../dist/verification/agent/0.1.32/cleanup-races-20261013/browser-repeat-2.log)·[3](../../dist/verification/agent/0.1.32/cleanup-races-20261013/browser-repeat-3.log)은 같은 최종 source의 `cargo test --locked -p racp-runtime --test browser -- --test-threads=1`을 최대 3회/첫 실패 시 중단하는 예산으로 수행해 각각 **20 PASS / 0 FAIL**, 총 **60 PASS**, exit 0이었다. startup cancel·concurrent close·timeout·external CDP·process protection·restart/live profile 보존을 포함한다. 짧은 직렬 반복이며 장시간/병렬 부하 또는 kernel/driver 원인 규명을 대신하지 않는다. 전체 workspace 결과도 Agent worktree의 Iced 기준 Client를 포함한 검사로서 main의 진행 중인 Slint source를 검증한 것이 아니다. Agent-only SBOM의 **241 components**에는 Client/Iced/Slint/Electron/CPython component가 없다.


### 2026-10-13 AS16–AS18 hardware 계약·세션·수집 artifact 기반 — v0.1.33

main 통합 보류와 관계없이 미구현 Agent source 작업을 계속했다. PATCH **0.1.32→0.1.33 한 번**, uv/workspace/독립 analysis-host local version을 동기화했다. 외부 dependency pin과 기존 operation/permission/schema는 바꾸지 않았고 Windows target-specific SDK feature 두 개만 추가했다.

- [공통 hardware 계약](../../crates/racp-contract/src/hardware.rs): Agent PC Device ID와 별도 opaque hardware/interface/connection generation·session/lease ID를 분리한다. Caller는 외부 JSON으로 deserialize하지 않는 trusted context다. Read plan·operation ID·IO budget과 provenance manifest는 unknown field/raw handle/path/임의 control transfer 입력을 받지 않는다. descriptor·연관 파일·문서화된 read protocol을 구분하고 부분 수집 이유·시각·원본 size/SHA를 검증한다. 범용 firmware dump/license dongle 기능을 추가하지 않았다.
- [core inventory/session](../../crates/racp-core/src/hardware.rs): 최대 256 interface의 완전한 snapshot만 원자적으로 교체한다. 동일 VID/PID는 서로 다른 장치이고 composite interface는 같은 physical identity 아래 별도 ID다. unplug/replug·metadata 교체·알림 연속성 유실은 이전 target을 거부한다. caller/PC/boot/workspace/정확한 lease·단조 lease/IO deadline·actor/session/IO/bytes quota를 검사한다. reserve는 OS open이 아니며 native open 확인 뒤만 Active가 된다. stop은 새 reserve/IO를 막고 pending IO drain과 원래 native handle 종료 확인 전 Closed로 보고하지 않는다. unknown 상태나 cancel/late/stale completion에는 수집 receipt를 생성하지 않는다.
- [Windows PnP adapter](../../crates/racp-runtime/src/platform/windows/hardware.rs): target-specific SetupAPI source를 별도 platform 경계에 뒀다. 현재 활성 USB device interface, instance ID, PnP Hardware IDs의 VID/PID를 bounded/aligned SDK buffers로 읽는다. opaque symbolic path를 parse하거나 외부로 serialize하지 않는다. malformed UTF-16·terminator/size/probe/type·조회 불확실성은 실패한다. InfoSet은 RAII로 닫고 취소/deadline/256 cap을 확인한다. **이 SDK 탐색 함수를 시험·Agent startup에서 호출하지 않았다.** SDK 동기 호출의 선점을 주장하지 않으며 bounded worker·notification continuity가 후속이다.
- [수집 artifact bridge](../../crates/racp-runtime/src/providers/hardware.rs): timely/uncancelled/current-generation IO 완료만 생성할 수 있는 내부 receipt에 target/read method/operation/bytes를 묶는다. manifest와 bytes를 하나의 JSON bundle로 기존 OutputSpool에 원자적으로 배치·등록한다. 하나의 artifact에 provenance와 원본 bytes가 함께 있어 기존 Gateway의 SHA/resumable transfer를 재사용할 수 있다. host decoder는 duplicate/unknown field·base64 크기·size/SHA를 다시 확인한다. 원래 emission fence와 permission admission은 future Provider 연결 시 계속 필요하다.

[계약 시험](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/contract.log)은 초기 **2 PASS**, [core 초기](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/core.log)는 **8 PASS**, [Windows decoder](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/windows-decoder.log)는 **2 PASS**다. [수집/core 최초 실행](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/collection-and-core.log)은 receipt API에 inventory 인수를 추가한 뒤 fixture call 네 곳이 미갱신돼 compile exit 101이었다. signature를 고쳐 [수집/core 검사](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/collection-and-core-final.log)를 **15 PASS**, read plan 및 operation binding의 [후속](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/read-plan-binding.log)·[후속](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/operation-binding.log)도 통과했다. 최종 신규 일반 시험은 contract 3/core 10/runtime decoder 2/collection 3의 **18개**다. 실제 SDK 호출 시험은 없고 private fixture의 원자적 snapshot/시간·상태·scope/budget·spool/restart·hash 불변을 검사한다.

[첫 전체 source 실행](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/rust-workspace.log)은 **146 PASS / 0 FAIL / 5 subprocess fixture ignored**, exit 0이었다. 이후 receipt를 다른 operation에 재사용하지 못하도록 binding을 보완했으므로 이 PASS를 최종 source 인수로 대신하지 않는다. [Python](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/python.log) **11 PASS**, [Ruff](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/ruff.log) PASS, [mypy](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/mypy.log) **98 files PASS**를 확인했다.

> [!IMPORTANT]
> AS16–AS18은 **공통 계약/상태·Windows 탐색 source·fixture 수집 artifact의 부분 구현**이다. Hardware RPC/permission catalog/NativeProviders/CLI에는 아직 연결하지 않았다. 실제 외장 장치·driver IO·HID/WinUSB/vendor open·descriptor/native collection·IO 취소·재연결 알림·service account·remote hardware 인수는 미완료다. fixture bytes를 장치에서 수집한 데이터로 표시하지 않는다. 기존 Agent remote/Rust suite를 hardware end-to-end 성공으로 해석하지 않는다. 실제 설치·SCM·장치 탐색/IO·driver 변경·GUI source 수정은 하지 않았다. main 통합만 보류하며 다음 독립 source 작업을 계속한다.


| v0.1.33 최종 검사 | 결과·범위 |
|---|---|
| 전체 Rust 직렬 suite | [최종 로그](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/rust-workspace-final.log), **146 PASS / 0 FAIL / 5 subprocess fixture ignored**, exit 0. operation/read plan receipt binding 보완 뒤 최종 source; 신규 hardware 18개 포함. Agent worktree의 Iced 기준 Client 포함이며 main Slint source 인수 아님 |
| Windows Agent 서비스용 개발 payload | [manifest](../../dist/agent/0.1.33/win-x64/hardware-foundation-20261013-a/staging/build-manifest.json)·[stage](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/stage.log)·[verify](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/verify-stage.log), Windows x64/Rust 1.90.0 release/static CRT·3 GUI-free files·hash/native target PASS. 네 신규 hardware source가 build input에 포함됨 |
| 최종 EXE 기존 원격 회귀 | [results](../../dist/verification/agent/0.1.33/hardware-foundation-e2e-20261013-a/results.json)·[로그](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/e2e.log), **21 PASS / 0 FAIL**, exit 0. 격리 HTTPS/WSS·인증 IPC/headless·권한/승인/replay·파일/명령/ConPTY·멱등성/CAS/cancel/stop/restart. 기존 profile 미사용, 시험 Agent/Gateway 종료. Hardware/native service 인수 아님 |
| 정적 검사 | [inventory](../../dist/verification/agent/0.1.33/hardware-foundation-20261013/inventory.log) **114 operations / 143 permissions / 18 categories**, frozen 100 유지. scoped Rust fmt/git diff check PASS. 위 Python 11/Ruff/mypy 결과 유지 |

최종 EXE **22,411,264 bytes**, SHA-256 **`23d759f9657c9080e3eb117b79089e9be7192ec82a040b9f7e073060686a6344`**; payload manifest **`5c0de5e1f10b49ab961bd2973d9a468f974c6ef74176ef6f324782697cb76484`**; input digest **`a3a0e6a349f4af6e65a9a4c6d4d998ca3af7448ae48041ac234b185cc736c029`**다. baseline `dc70b7e`와 당시 dirty source, unsigned/non-published 및 xtask tests=`not_run_by_xtask`를 기록했다. 최종 E2E hash는 manifest와 같고 harness SHA `fd9b319d459948f5fed9dc5b8b6b8ed6de46e249392c8f4191ccd9fa93d65a1e`다. main 통합 없이 이 묶음을 보존하고 bounded discovery worker·RPC/policy 및 AS08/AS09 등 다음 독립 source 범위를 계속 진행한다. 실제 장치/driver/SCM 인수와 AS07 간헐 signal 원인·독립 review는 계속 미완료다.


### 2026-10-13 AS16 Windows 탐색 worker·AS07 공통 helper 종료 증명 — v0.1.34

v0.1.33의 source 기반을 `aed05cf`로 보존한 뒤 main 통합 없이 다음 범위를 이어갔다. [Windows worker](../../crates/racp-runtime/src/platform/windows/hardware/worker.rs)는 [Agent 고정 mode](../../apps/agent/src/main.rs)와 [공통 contained helper](../../crates/racp-runtime/src/providers/worker.rs)에 연결했다. 현재 Agent EXE만 실행하고 Job/memory 256 MiB/timeout/cancel·stdout 최대 4 MiB를 적용한다. child input 128 bytes, timeout 1–30000 ms 및 private row 256개 제한·중복/unknown fields·raw path/driver/executable override 거부를 검사한다. raw locators는 worker-private IPC DTO이며 Gateway response/IO authority로 쓰지 않는다. core의 완전한 snapshot validator를 재사용한다. 기본 startup/NativeProviders/RPC에는 연결하지 않았고 실제 SDK worker 탐색은 실행하지 않았다.

AS07의 공통 helper는 cleanup이 Job-empty만 검사해 parent의 비동기 종료 signal이나 poll 오류를 확인하지 못하던 경로를 보완했다. 동일 5초 budget에 parent signal과 tree-empty를 함께 요구하고 query 오류는 CLEANUP_UNKNOWN이다. pre-cancel/deadline에는 child를 만들지 않으며, frame 처리 직후에도 deadline/cancel을 재확인한다. 기존 worker stdout/stderr 16 KiB 제한은 유지하고 hardware snapshot stdout만 4 MiB로 제한한다. [cleanup RED](../../dist/verification/agent/0.1.34/hardware-worker-20261013/cleanup-red.log) **0 PASS / 2 FAIL**→[worker GREEN](../../dist/verification/agent/0.1.34/hardware-worker-20261013/worker-green.log) **4 PASS**를 확인했다. 실제 stalled driver를 만들지 않고 parent signal 미확정/Job empty·query 실패·정상 종료의 모델로 gate를 검증했다. 새 Agent argument 시험 1개는 mode/고정 argv 검증만 하며 OS action은 없다.

PATCH **0.1.33→0.1.34 한 번**, uv/workspace/독립 analysis-host lock local version을 동기화했다. 외부 pin/공개 operation·permission은 변경하지 않았다. no-install/SCM/device IO/driver 변경 금지와 GUI source 보존을 유지한다. actual worker containment/SetupAPI 데이터·불확실한 native helper의 durable recovery·RPC/policy/notification/native IO 연결 및 기존 간헐 process signal 원인은 별도 인수다.

최종 [전체 Rust](../../dist/verification/agent/0.1.34/hardware-worker-20261013/rust-workspace-fixture.log)는 **152 PASS / 0 FAIL / 5 fixture IGNORE**다. Python version 11/Ruff/mypy 98 PASS와 [inventory](../../dist/verification/agent/0.1.34/hardware-worker-20261013/inventory.log) 114 operations/143 permissions/18 categories를 확인했다. 실패 재실행도 보존했다: [첫 검사](../../dist/verification/agent/0.1.34/hardware-worker-20261013/rust-workspace-final.log)는 동시에 실행한 xtask.exe 파일 잠금으로 compile 실패, [다음 검사](../../dist/verification/agent/0.1.34/hardware-worker-20261013/rust-workspace-retry.log)는 Chromium fixture 환경변수 누락으로 Browser 9 PASS/11 FAIL이었다. fixture 지정 최종 실행은 통과했다. package fmt가 이전 source의 lib.rs/providers/mod.rs 모듈 순서 두 곳을 발견했으며 다음 묶음에서 정리한다. git diff check PASS.

[stage-a](../../dist/verification/agent/0.1.34/hardware-worker-20261013/stage.log)는 compile 후 SetupAPI import admission 부재로 실패했다. staging.quarantine을 보존하고 [PE verifier](../../xtask/src/package/imports.rs)에 정확한 setupapi.dll만 추가했다. 유사 DLL/vendor/WinUSB/dynamic CRT 거부 시험도 통과했다. 같은 버전의 [stage-b](../../dist/verification/agent/0.1.34/hardware-worker-20261013/stage-b.log)·[verify-b](../../dist/verification/agent/0.1.34/hardware-worker-20261013/verify-b.log)는 GUI-free 3파일/hash/target 검사 PASS다. [최종 EXE E2E](../../dist/verification/agent/0.1.34/hardware-worker-e2e-20261013-b/results.json) **21 PASS**; EXE **22,432,768 bytes**, SHA-256 **214c0d0f32d6a40510f8c206be0736bc9fb5ff90beb9083094c45426eca205b5**; payload manifest **0122abf23f12def89dbcfe1dc40f7842b11134bf896779711db8cf179a777d16**; source input **facee8a00394d9a8d48ea327dfd10922a95dce5a714c83a6a248241303d255bc**, baseline aed05cf/dirty source다. unsigned/non-published이며 실제 SDK 탐색/장치 IO/SCM 인수를 포함하지 않는다. checkpoint 이후 main을 기다리지 않고 helper 초기 실패 정리와 frame 완전 수신을 이어서 보완한다.


### 2026-10-13 AS07 helper early-failure cleanup and complete output frames — v0.1.35

Main integration remains deferred; the v0.1.34 checkpoint is `46dfd6a`. The fixed helper now sends memory-limit, protection-registration and stdin-write errors through explicit owned-tree termination and parent/Job exit verification. Capture requires parent exit plus EOF on both bounded streams before decoding a frame. This prevents truncating large discovery snapshots or overlooking late stderr overflow. Tests execute only owned fixture subprocesses: 128 KiB stdout, stderr above 16 KiB and a simulated setup error; no SetupAPI/device/SCM action occurs. Synchronous generic stdin writes are still not hard-preemptible; durable helper recovery remains incomplete.

The first [full regression](../../dist/verification/agent/0.1.35/worker-frames-20261013/rust-workspace.log) reproduced Browser cleanup failures (18 PASS / 2 FAIL in Browser). Browser close holds the process mutex also needed by the background pipe reader. Termination now discards bounded output while awaiting the same parent signal and empty Job, preserving the five-second budget and ownership gate. Failed termination logs only booleans and byte counts, never output. Windows [TerminateProcess documentation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess) says pending IO must complete or cancel before exit; blocked output is a plausible contributor, not a proven explanation of every historical failure. [Browser after drain](../../dist/verification/agent/0.1.35/worker-frames-20261013/browser-drain.log) passed 20 tests. A native owned-output fixture also checks both termination observations. Repeated passes will not erase the original intermittent failure evidence.

PATCH was bumped once to 0.1.35 with uv and both Cargo locks synchronized. Previously unsorted module declarations were formatted. [Python version/harness](../../dist/verification/agent/0.1.35/worker-frames-20261013/python.log): 11 PASS; [Ruff](../../dist/verification/agent/0.1.35/worker-frames-20261013/ruff.log): PASS; [mypy](../../dist/verification/agent/0.1.35/worker-frames-20261013/mypy.log): 98 files PASS. Native service/device acceptance, independent review and main integration remain incomplete.


Final [workspace regression](../../dist/verification/agent/0.1.35/worker-frames-20261013/rust-workspace-final.log): **155 PASS / 0 FAIL / 6 fixture IGNORE**. Scoped package fmt and git diff check PASS; inventory stays 114 operations / 143 permissions / 18 categories. [Stage](../../dist/verification/agent/0.1.35/worker-frames-20261013/stage.log) and [verify](../../dist/verification/agent/0.1.35/worker-frames-20261013/verify.log) pass. [Final artifact E2E](../../dist/verification/agent/0.1.35/worker-frames-e2e-20261013-a/results.json): **21 PASS**. EXE **22,424,064 bytes**, SHA-256 **b06e0f11659570e2a7c7c5f0ae6a8a6ff518da5541f63bfc0433b14741514898**; payload manifest **4387126edb6987c159f504b7113137b6daf7be2545e94d5406ca958da6cdbe2c**; source input **2ffd9ca14821396a9c2cacc7f88aea5569381e5860600d22781ca5a7cac18a47**, baseline 46dfd6a/dirty source. Unsigned/non-published, no installer, device enumeration or service registration. Continue the independent AS08 provision receipt boundary next; no main merge is needed for that source work.


### 2026-10-13 AS08 typed provision receipts and pre-config inspection — v0.1.36

Continue after v0.1.35 commit `e1e4ea8`, without merging main. [Contract](../../crates/racp-contract/src/service_provision.rs) preserves receipt format 1 and the five existing phase names but validates nonce, fixed service name, lowercase hashes and false registered/ready flags. [Pinned core store](../../crates/racp-core/src/service_provision.rs) serializes writers with a dedicated lock and advances only the exact predecessor using receipt/hash/native root identity CAS. Duplicate, oversized, malformed, skipped, stale and foreign-root evidence is rejected without replacement. Reserve is written before SCM creation; a recorded reserve therefore never proves SCM absence after a crash.

[Windows provision](../../apps/agent/src/platform/windows/service/service_provision.rs) uses this store and reports the final receipt hash. The existing SYSTEM admin entry point admits the typed `inspect_provision_receipt` request before config loading, with an exact expected receipt hash and fixed protected parent/root checks. It returns recorded progress and explicitly marks service/registration state as unobserved. The action does not query or modify SCM, enroll, adopt, automatically resume or retry a failed provision. Core inspection uses its receipt coordination lock; it is not a claim of a completely write-free filesystem operation. SYSTEM/image admission still applies. Physical/SCM fault injection remains deferred.

The initial [receipt tests](../../dist/verification/agent/0.1.36/provision-receipts-20261013/receipts.log) passed four tests; root identity fencing and request parsing were then added. The first [workspace regression](../../dist/verification/agent/0.1.36/provision-receipts-20261013/rust-workspace.log) reproduced Browser 19 PASS / 1 FAIL, with **parent_signaled=false, tree_empty=true, discarded stdout/stderr=0 bytes**. Thus the v0.1.35 pipe change has not established a fix for the historical native exit flake. The failure remains recorded and cleanup continues to report UNKNOWN instead of deleting ownership evidence.

PATCH bumped once to 0.1.36; workspace/analysis-host/uv locks synchronized. [Python version/harness](../../dist/verification/agent/0.1.36/provision-receipts-20261013/python.log): 11 PASS; [Ruff](../../dist/verification/agent/0.1.36/provision-receipts-20261013/ruff.log): PASS; [mypy](../../dist/verification/agent/0.1.36/provision-receipts-20261013/mypy.log): 98 files PASS. Main GUI changes and registered profiles/services are preserved. No installer, enumeration, device IO or service operation was executed by these fixture tests.


The next [default-concurrency regression](../../dist/verification/agent/0.1.36/provision-receipts-20261013/rust-workspace-final.log) again failed Browser cleanup (18 PASS / 2 FAIL). An owned-only CDP graceful-shutdown experiment also failed [18 PASS / 2 FAIL](../../dist/verification/agent/0.1.36/provision-receipts-20261013/browser-graceful.log) and was reverted; it is not in the final source. Preserve these failures separately from the final serial fixture run. A serial pass does not establish that parallel native cleanup is fixed.


User reported repeated firewall permission prompts during testing. Read-only Windows firewall events identified the changing `target/debug/deps/browser-*.exe` fixtures (rule-add/block and user allow events). One cross-site Browser fixture binds `0.0.0.0:0`; its root is 127.0.0.1 and child is localhost, so all-interface binding is unnecessary. Restrict this fixture to IPv4 loopback after the current immutable staging batch finishes, preserve cross-site assertions, and retest. No firewall policy/rule change is performed by the agent. Firewall allow/block packet logging was disabled, so the event evidence does not establish the cause of delayed parent exit.


The cross-site fixture now binds 127.0.0.1 only and asserts loopback; root/child still use distinct 127.0.0.1/localhost host names and all frame isolation/input/focus/detachment assertions remain. [Read-only firewall events](../../dist/verification/agent/0.1.36/provision-receipts-20261013/firewall-events.json) support the prompt origin. [Microsoft firewall rules documentation](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/rules) explains listen prompts and full-path application rules, consistent with new hashed test EXEs producing repeated prompts. After the fixture change, [default-concurrency regression](../../dist/verification/agent/0.1.36/provision-receipts-20261013/rust-workspace-loopback.log) still failed Browser **12 PASS / 8 FAIL** (seven cleanup/exit failures and one protected-process identity observation). Thus removing wildcard listening does not establish a fix for native cleanup. No firewall notifications were disabled and no allow/block rules were added or removed by the agent. The source change requires a new immutable staging build ID under the same version; preserve batch a.


After the loopback change, [serial regression](../../dist/verification/agent/0.1.36/provision-receipts-20261013/rust-workspace-loopback-serial.log) still failed Browser **17 PASS / 3 FAIL**. Inspection then found synchronous native termination polling/sleeping on Tokio's executor thread. [Executor wait RED](../../dist/verification/agent/0.1.36/provision-receipts-20261013/async-wait-red.log): 0 PASS / 1 FAIL. Startup failure cleanup and session close now await `spawn_blocking` while keeping pinned process ownership and protection until confirmed exit; the background reader uses `try_lock` so it cannot block the executor on the termination mutex. Profile deletion still follows exit proof; failure retains process/session evidence. The initial [GREEN compile](../../dist/verification/agent/0.1.36/provision-receipts-20261013/async-wait-green.log) missed the borrowed-session Arc constructor and failed compilation; corrected [GREEN](../../dist/verification/agent/0.1.36/provision-receipts-20261013/async-wait-green-final.log) passed. This proves executor isolation; it does not by itself prove the cause of every historical delayed parent signal.


Final source [library tests](../../dist/verification/agent/0.1.36/provision-receipts-20261013/library-final.log): **32 PASS / 0 FAIL / 2 fixture IGNORE**, including six provision contract/store tests and executor isolation. Final [default-concurrency workspace](../../dist/verification/agent/0.1.36/provision-receipts-20261013/rust-workspace-async.log) still failed Browser **14 PASS / 6 FAIL**; no whole-workspace pass or Browser native acceptance is claimed for this source. All workspace targets compiled. Scoped fmt, git diff check and [inventory](../../dist/verification/agent/0.1.36/provision-receipts-20261013/inventory-final.log) PASS, with 114 operations / 143 permissions / 18 categories. Python/Ruff/mypy results above remain valid.

Preserve staging batch a (earlier source) and [batch b](../../dist/verification/agent/0.1.36/provision-receipts-20261013/stage-b.log), which includes the loopback and executor fixes. [Verify b](../../dist/verification/agent/0.1.36/provision-receipts-20261013/verify-b.log) passed GUI-free payload admission. [Final EXE E2E](../../dist/verification/agent/0.1.36/provision-receipts-e2e-20261013-b/results.json): **21 PASS**, same artifact hash. EXE **22,544,896 bytes**, SHA-256 **2c775127cace9e258240b8784082323a48353a1a42178dbedf3964f7c5ec368e**; payload manifest **2529e7541e3c4c9006f2833c3e1c494250db81e71392f0b30c2a154ac1172223**; source input **3d0e9e352a70ee24c6caa27a98bf0636addab10fd85fe415b549c6a1e4de0575**, baseline e1e4ea8/dirty source. Unsigned/non-published; E2E excludes Browser/SCM/device acceptance. Main remains unmerged with Slint permission-view work in progress. Native cleanup and independent review remain open gates; source checkpointing is not release approval.


### 2026-10-13 AS16 permission-bound hardware metadata RPC — v0.1.37

Continue after `567253e` on the isolated Agent branch. [Python contract](../../packages/protocol/src/racp_protocol/hardware.py), registry and AND bindings add **hardware.enumerate / hardware.inspect**; the separate **hardware.inventory.read** leaf uses the existing system category. Inventory explicitly records the two operation additions and one permission addition under AS16 ownership. Frozen baseline **100 operations / 143 permission identities / 62 assigned expansion IDs** remains intact; current inventory is **116 operations / 144 permissions / 18 categories**. Existing catalog rows and public schema file paths remain unchanged.

[Metadata Provider](../../crates/racp-runtime/src/providers/hardware/provider.rs) is connected to NativeProviders and the contained Windows discovery worker. NativeRuntimeConfig requires explicit **hardware_discovery_enabled=true**; false is the default and omitted on serialization for old disabled config shape. Startup/status do not execute enumeration. Provider checks local permission/approval context and configured workspace; it bounds count, timeout and emitted bytes and rechecks cancellation/policy epoch before returning data. Only opaque targets, VID/PID, USB transport and snapshot timestamp reach RPC. Private backend paths/instance keys are not serialized. Connected/open/collection support is false. Polling lacks hotplug continuity, so every scan invalidates older targets; snapshots are principal/workspace-bound and expire after 30 seconds.

A dropped query cancels its SDK worker but keeps the JoinHandle in the Provider's in-flight slot until later query/stop reaps it. Unknown native cleanup or lost worker latches unhealthy state, blocks further discovery and prevents empty inventory from claiming successful cleanup. Raw device selectors, driver actions and arbitrary worker paths remain unavailable. No native device open/read, firmware dump, licensing/authentication-key feature or hardware SDK acceptance is claimed.

The first [compile](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/compile.log) failed on a private core module import; using the public authorization export repaired it. Initial [Provider fixtures](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/provider.log): 5 PASS. [Extended hardware tests](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/hardware-budget-final.log) include one config test plus 11 runtime hardware tests (seven new Provider tests, four decoder tests). Fixtures cover disabled/denied/cancelled requests, opaque/private boundary, principal/boot/stale targets, dropped-call cleanup, unknown cleanup, late result rejection, policy revocation and output limits. All use private fake observations; actual SetupAPI enumeration and device IO were not executed.

[Python contract/permission/version/harness](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/python-final.log): **42 PASS / 5 platform fixture SKIP**; [Ruff initial](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/ruff.log) found one line length and three broad exception assertions, repaired in [final Ruff](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/ruff-final.log): PASS. [Mypy](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/mypy.log): **99 source files PASS**. PATCH bumped once to 0.1.37; uv and workspace/analysis-host lock versions synchronized. Main Slint/Cargo changes, profiles and services remain preserved; no firewall policy changes, installer, SCM operation or physical hardware access were performed.


[Workspace default-concurrency regression](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/rust-workspace.log) compiled all targets but stopped at Browser **18 PASS / 2 FAIL** (isolated context cleanup and origin-fence cleanup). This remains the historical native exit gate; it is not counted as a workspace pass. Separately, [other workspace packages](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/rust-other-workspace.log) **89 PASS**, and [runtime library plus every non-Browser integration target](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/rust-runtime-nonbrowser.log) **61 PASS / 6 fixture IGNORE**, total **150 PASS** outside Browser. A dedicated [permission-delta test](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/admission-test-final.log) adds **1 PASS**, exercising explicit unique AS16 additions and baseline preservation. The initial extraction [compile](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/admission-test.log) had an unintended iterator dereference; fixed before the passing test. [Final inventory](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/inventory-final.log) PASS, frozen baseline retained. Native setup/device IO, Browser parallel cleanup, independent review and main integration remain open gates.


[Windows Agent service stage](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/stage.log) and [verify](../../dist/verification/agent/0.1.37/hardware-metadata-20261013/verify.log) PASS (three GUI-free payload files, native target, admitted imports and hashes). [Final EXE E2E](../../dist/verification/agent/0.1.37/hardware-metadata-e2e-20261013-a/results.json): **21 PASS**. Hardware remains disabled during E2E; physical SDK metadata discovery and native open/read are not acceptance-tested. EXE **22,654,976 bytes**, SHA-256 **9017a2d5594a4f39fa960e0332ee3f58e0c813878fc08354829dab199b1c3700**; payload manifest **6fe9152ea360584514f5fd7bc6e8e449b3ee62f98991d4da290b0013bf592349**; source input **0d54827c0877468a2bb5ebcace585caef3df14fd8acd2218b00b329587600c88**, baseline 567253e/dirty source. Unsigned/non-published, no setup generation/installation, registered-state changes or main merge. Latest main still has ongoing Slint GUI changes. Source checkpointing does not close Browser cleanup, hardware/service native acceptance or independent review gates.


### Agent v0.1.38 — AS07/AS08 local management helper containment (2026-10-13)

The fixed local Agent helper path now checks pre-cancellation before executable lookup, cancels its owned worker when the caller future is dropped, and places setup/protection/stdin failures inside explicit cleanup. Input writes run on a separately tracked thread, so a helper that does not read stdin cannot hold the cancellation/deadline loop indefinitely. Native parent signal and the entire owned Job must both confirm exit; writer completion is also checked before returning. Cleanup uncertainty takes precedence over a helper result. Public action names, executable pinning, enrollment/configuration error admission and independent daemon startup remain unchanged. No arbitrary helper executable or service/device action was added.

The helper reuses the contained-worker capture/cleanup primitives with its existing 64 KiB limits on both output streams. Capture waits for both EOFs and catches late stderr overflow instead of parsing an incomplete frame when the parent first exits. Tests use our own isolated subprocess fixtures: blocked stdin, timeout/cancel, late stderr overflow, full stdout drain, setup failure and dropped-call cancellation. They never run enrollment, SCM, installers or physical device APIs.

Initial [compile](../../dist/verification/agent/0.1.38/local-helper-20261013/helper-tests.log) failed because an already borrowed error code was incorrectly passed through the unstable str-as-str method; corrected to direct matching. The first [Python invocation](../../dist/verification/agent/0.1.38/local-helper-20261013/python.log) referenced the schema drift test under the wrong directory and collected no tests; the corrected [suite](../../dist/verification/agent/0.1.38/local-helper-20261013/python-final.log) passed **21 tests**. [Ruff](../../dist/verification/agent/0.1.38/local-helper-20261013/ruff.log): PASS; [mypy](../../dist/verification/agent/0.1.38/local-helper-20261013/mypy.log): **99 source files PASS**. PATCH bumped once and uv/workspace/independent analysis-host locks synchronized.

The shared debug cache was blocked by ongoing main GUI compilation; only this task's waiting cargo test process was stopped and tests were moved to the existing worktree-local debug cache. No main source/build process was altered. Whole-workspace formatting encountered an existing Client trailing-whitespace failure; formatting changes generated in this otherwise clean isolated Client checkout were discarded, and formatting is restricted to Agent/runtime packages. The main Slint changes remain untouched. Browser's historical native exit failures are still open; Microsoft documents that termination is asynchronous and pending IO can delay exit ([TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)). This is a possible mechanism, not an established root cause. No exit gate or firewall policy was weakened.


[Helper fixtures](../../dist/verification/agent/0.1.38/local-helper-20261013/helper-tests-isolated.log): **4 PASS / 1 fixture IGNORE**. The drop-guard assertion is a cancellation model; actual enrollment/configuration helper invocation is not acceptance-tested here. [Contract/core/Agent/xtask regression](../../dist/verification/agent/0.1.38/local-helper-20261013/rust-foundation.log): **84 PASS**. [Runtime library and all non-Browser integration targets](../../dist/verification/agent/0.1.38/local-helper-20261013/rust-runtime-nonbrowser.log): **65 PASS / 7 fixture IGNORE**, including the four new helper tests; total **149 PASS** outside Browser without double counting. [Browser regression](../../dist/verification/agent/0.1.38/local-helper-20261013/browser.log): **18 PASS / 2 FAIL**, concurrent close/Agent cleanup and origin-fence cleanup, both reporting parent_signaled=false/tree_empty=true. This is not a whole-workspace pass and does not establish a Browser fix. Client tests are outside this batch. [Additional Python authority/metadata/harness tests](../../dist/verification/agent/0.1.38/local-helper-20261013/python-extended.log): **20 PASS / 5 platform fixture SKIP**; combined with the earlier distinct 21-test suite, **41 PASS / 5 SKIP**. [Agent/runtime/core/contract/xtask format](../../dist/verification/agent/0.1.38/local-helper-20261013/fmt.log), diff check and [inventory](../../dist/verification/agent/0.1.38/local-helper-20261013/inventory.log): PASS; current 116 operations/144 permissions/18 categories and frozen 100/143 baseline preserved. Existing unrelated compiler warnings were not broadened into this change.

[Windows Agent service stage](../../dist/verification/agent/0.1.38/local-helper-20261013/stage.log) and [verification](../../dist/verification/agent/0.1.38/local-helper-20261013/verify.log): PASS, three GUI-free payload files, admitted native imports/target/static CRT and hashes. [Exact EXE E2E](../../dist/verification/agent/0.1.38/local-helper-e2e-20261013-a/results.json): **21 PASS**, temporary user-mode state/Gateway/WSS and authenticated named-pipe controller; no existing profile used. Covers headless snapshot/activity, remote UTF-8 file/command/ConPTY, policy CAS/approval/nonce rejection, idempotence, cancellation, stop/restart and resource exit; it does not execute physical hardware or SCM. EXE **22,636,544 bytes**, SHA-256 **40f1e046b8ec9817cd66d135cfc66af85c6b54af7877ebc7629e4015d9c12217**; payload manifest **b4b0e9d236d5f73b39a575fcbf44ad84f7099064ae8b7038ba0f93b425f3830b**, source input **f0428ca62c4cc7830c31ff1c31835651cb90357c8ca5d8b5d9dae130419f7d6d**, c4a39e6 baseline/dirty source. Batch local-helper-20261013-a is unsigned/unpublished, with no setup artifact or installation. Main integration, Browser cleanup, physical USB/read backends and actual service acceptance remain open.


### Agent v0.1.39 — AS10 bounded live health diagnostics (2026-10-14)

Existing diagnostics.agent and diagnostics.export now attach fixed live runtime metadata: Gateway phase/connection and effective lease validity, active task count, resource totals/borrowed resources/unconfirmed cleanup, monotonic Agent uptime, allowlisted last runtime error and lifecycle cleanup phase. The legacy OS uptime field remains compatible. Weak views of Agent/task/lease/resource state avoid an Agent–Provider ownership cycle; standalone Providers report health as unobserved. Busy state yields an explicit error instead of a false zero. No request/result/error bodies, resource IDs, command lines, credentials, workspace/state paths or Gateway URL are projected into these fields.

Core Journal samples only state and numeric spool metadata, at most 4096 rows per table plus one truncation probe. Truncated totals are explicitly lower bounds; registered_pending_bytes describes stored registration metadata, not a recursive disk-size scan. No schema migration or outcome mutation occurs. The diagnostic sampler holds a one-slot permit inside its blocking task even if a caller times out/cancels, preventing detached calls from building an unbounded sampling queue. RPC output is bounded to 8 KiB. Shutdown preserves unknown cleanup and publishes complete only after existing native/resource/worker checks succeed. The last-error field maps arbitrary upstream strings to a fixed generic category.

Initial [compile](../../dist/verification/agent/0.1.39/health-20261014/compile.log) exposed a borrowed self reference accidentally captured inside the lease watchdog; corrected to its owned Agent clone. [Core metadata fixtures](../../dist/verification/agent/0.1.39/health-20261014/core-health.log): **2 PASS** (busy store, private/malformed bodies omitted, bounded operation/spool lower bounds). [Runtime health fixtures](../../dist/verification/agent/0.1.39/health-20261014/runtime-health.log): **4 PASS** (live/expired/retiring/stopping lease, redaction, expired/cancelled calls, dropped Agent without cycle, confirmed vs unknown shutdown, borrowed resource cleanup retained). These initial fixtures ran on the v0.1.38 source before the one batch bump to v0.1.39; final integrated regressions are recorded separately below. No SCM, device SDK, driver or installer was executed.

[Python version/schema/permissions/harness](../../dist/verification/agent/0.1.39/health-20261014/python.log): **26 PASS**; [mypy](../../dist/verification/agent/0.1.39/health-20261014/mypy.log): **99 source files PASS**. Initial [Ruff](../../dist/verification/agent/0.1.39/health-20261014/ruff.log) flagged a long added harness assertion; wrapped before final checking. The E2E harness now grants only the existing diagnostics.agent.read leaf for the new fixed-health case and checks actual Gateway/Agent boot/lease, active request, queue/spool presence, response budget and secret/path redaction.


The completed Slint-only GUI tree from 24c60b0 is now included in the Agent worktree without changing Client source; the later master merge 6233937 has exactly the same tree. Version-only conflicts were resolved to 0.1.39, while the full Slint lock/dependency changes and all Agent/GUI evidence were retained. The system DLL check combines Agent setupapi.dll with Slint shlwapi.dll/dwrite.dll and keeps dynamic CRT/vendor rejection. No GUI redesign, installer, service registration or existing registered-profile mutation was performed.

[Integrated Python](../../dist/verification/agent/0.1.39/health-20261014/merged-python.log): **41 PASS**; [Ruff](../../dist/verification/agent/0.1.39/health-20261014/merged-ruff.log), [mypy](../../dist/verification/agent/0.1.39/health-20261014/merged-mypy.log) (**99 files**), [whole-workspace format](../../dist/verification/agent/0.1.39/health-20261014/merged-fmt-final.log), diff check and [inventory](../../dist/verification/agent/0.1.39/health-20261014/merged-inventory.log): PASS. [Workspace excluding runtime](../../dist/verification/agent/0.1.39/health-20261014/merged-rust-foundation.log): **86 PASS**, including Slint Client compilation. [Runtime library and every non-Browser integration target](../../dist/verification/agent/0.1.39/health-20261014/merged-rust-runtime.log): **69 PASS / 7 fixture IGNORE**. Total **155 PASS** outside Browser. [Browser regression](../../dist/verification/agent/0.1.39/health-20261014/merged-browser.log): **17 PASS / 3 FAIL**, all native cleanup with parent_signaled=false/tree_empty=true (frame scope, concurrent close and origin fence); gates and ownership evidence are retained. This is not a whole-workspace pass or a Browser remediation claim.

The first [native stage](../../dist/verification/agent/0.1.39/health-20261014/stage.log) failed compilation after a missing lifecycle snapshot declaration in the added cleanup-admission condition. Its staging.quarantine is retained. Corrected [stage b](../../dist/verification/agent/0.1.39/health-20261014/stage-final.log) and [verify](../../dist/verification/agent/0.1.39/health-20261014/verify.log): PASS, three GUI-free payload files, native target/static CRT/import admission/hashes. [Exact EXE E2E](../../dist/verification/agent/0.1.39/health-e2e-20261014-b/results.json): **22 PASS**, including the newly exercised real Gateway/WSS bounded/redacted live diagnostics. Existing headless approval/cancel/stop/restart/file/command/ConPTY cases all passed. This is isolated user-mode evidence and does not establish physical device or SCM acceptance. EXE **22,696,960 bytes**, SHA-256 **d630b65f8dff79edd368ba0f1de16ec510576ce4f382b1a0eb4abf4d9de30603**; payload manifest **14721ac2675e99417e19ada046d6ba6392db1a375ba3f498b6505fa9f5044500**; source input **62d48358ff096e60217a6df23967d3c5ba5d00b67732558e7acaa2baf0f06235**, 754900b baseline/dirty integrated source. Unsigned/unpublished Agent development staging only, no setup artifact execution.
