# RACP Windows 배포 인수 게이트 기준표 (Windows Release Gates)

> **문서 ID**: `DOC-QA-GATES`  
> **상태**: Active · **기준 버전**: v0.1.19\
> **최종 개정일**: 2026-10-09 · **분류**: Quality Assurance & Release Gates

---

## 개요 (Overview)

본 문서는 **RACP Windows 배포판**이 정식 릴리스(Release Acceptance) 판정을 받기 위해 반드시 충족해야 하는 필수 보안, 멱등성, 수명주기, 파일 시스템, 터미널 및 데스크톱 자동화 인수 게이트(Acceptance Gates) 기준과 현시점의 검증 상태를 정의합니다.

개발정의서 v1.1 부록 E14~E16에 기반하여 부분적 기능 동작과 완결된 제품 인수 상태를 엄격히 구분하여 관리합니다.

> [!IMPORTANT]
> 아래 평가 표는 **v0.1.8의 역사적 기준선 기록**이다. 해당 `VERIFIED`를 현재 source v0.1.19이나 새 원격 대상 121의 배포 인수로 이관하지 않는다. 현재 실행·결함·미검증 상태는 [구현 현황](implementation-status.md), 후보별 판정 절차는 [Windows 작업 및 테스트 계획](../spec/windows-engineering-plan.md#section-11)을 따른다. 실제 새 package 설치·display/session·부하/soak 등 요구 증거가 없는 항목은 완료로 판정하지 않는다.

---

## 목차 (Table of Contents)

- [1. 릴리스 게이트 평가 매트릭스](#1-릴리스-게이트-평가-매트릭스)
- [2. v0.1.8 릴리스 검증 범위 및 세부 기준](#2-v018-릴리스-검증-범위-및-세부-기준)
- [3. 배포 패키지 무결성 및 서명 원칙](#3-배포-패키지-무결성-및-서명-원칙)
- [4. v0.1.19 Gateway 관리 배포 게이트](#4-v0119-gateway-관리-배포-게이트)
- [5. 관련 문서](#5-관련-문서)

---

## 1. 릴리스 게이트 평가 매트릭스

| 게이트 ID | 영역 | 필수 요구 증거 (Required Evidence) | v0.1.8 기준선 기록 |
| :--- | :--- | :--- | :---: |
| **AUTH-01** | 인증 | 1회용 등록 토큰 만료/재사용 거부, 기기/소유자 토큰 혼용 차단 | **VERIFIED** |
| **AUTH-02** | 보안 | Origin/CSRF 방어, 외부 OAuth 서명/Audience/Scope 변조 차단 | **VERIFIED** |
| **AUTH-03** | 수명 | 토큰 취소(Revoke) 시 신규 접수 즉시 차단, Lease(60s)+정리(5s) 내 프로세스 종료 | **VERIFIED** |
| **RPC-01** | 멱등성 | 동일 `idempotency_key` 100개 동시 요청 시 실행 횟수 정확히 1회, 변조 충돌 감지 | **VERIFIED** |
| **RPC-02** | 복구 | ACK/결과 유실 및 Agent 비정상 종료 시 중복 실행 0건 및 `UNKNOWN` 상태 보존 | **VERIFIED** |
| **RPC-03** | 프로토콜 | 엄격한 Discriminated Schema, 프레임 크기 초과 차단, 에이전트 생존 보장 | **VERIFIED** |
| **SHELL-01**| 프로세스 | Unicode/공백 인자, stdout/stderr 완전 분리, nonzero exit code 수집 | **VERIFIED** |
| **LIFE-01** | 격리 | 하위 자식/손자 프로세스 타임아웃/취소 후 포트 및 프로세스 누수 0건 (Job Object) | **VERIFIED** |
| **POLICY-01**| 권한 | 기본 deny, 승인 토큰 만료/재사용 차단, 승인 비활성화 시 위험 작업 거부 | **VERIFIED** |
| **FS-01**   | 파일 | Junction/Symlink 우회 차단, Atomic Write, 한글 및 BOM 보존 | **VERIFIED** |
| **PROC-01** | 프로세스 | 프로세스 ID identity 변조 거부, 대기 타임아웃 시 임의 프로세스 종료 방지 | **VERIFIED** |
| **ART-01**  | 아티팩트 | 100 MiB 대용량 전송 중단 후 Range 재개(Resume), 최종 SHA-256 일치 | **VERIFIED** |
| **ART-02**  | 스풀링 | 디스크 한도 초과 시 안전 스풀링 및 가비지 컬렉션(GC) 경합 격리 | **VERIFIED** |
| **PTY-01**  | 터미널 | ConPTY 지속 터미널, 윈도우 크레딧 백프레셔, 유니코드 입출력 보존 | **VERIFIED** |
| **STATE-01**| 상태 복구 | 게이트웨이/에이전트 재시작 시 상태 복구, 터미널 종료 상태 불변성 | **VERIFIED** |
| **UI-01**   | 콘솔/UI | SSE 스트림 갭 복구, 민감 정보 마스킹, 비동기 안전 렌더링 | **VERIFIED** |
| **BROWSER-01**| 브라우저| 격리 Chromium 프로세스, 폼 입력, 스크린샷 아티팩트, 자원 안전 정리 | **VERIFIED** |
| **DESK-01** | 데스크톱 | 다중 DPI 배율, 다중 모니터 좌표계, 잠금 화면/UAC 상태 인지 | *Partial* |
| **DESK-02** | 입력 보호 | 사용자 물리 입력 감지 시 원격 입력 즉시 해제, Guardian 프로세스 안전망 | **VERIFIED** |
| **RE-01**   | 리버싱 | GDB/MI 및 Ghidra Headless 연동, 플러그인 크래시 시 코어 생존 | **VERIFIED** |
| **REL-01**  | 패키징 | NSIS 설치, 업그레이드 전 에이전트 정리, 자격 증명 보존, 백업 해시 | **VERIFIED** |
| **PERF-01** | 부하 | 30분 최대 부하 및 8시간 장기 Soak 테스트 시 메모리/핸들 누수 0건 | *Pending* |
| **HOST-01** | AI 연동 | 실제 Codex 앱을 통한 원격 기기 툴 호출, 파일/명령/ConPTY/화면 캡처 실증 | **VERIFIED** |

---

## 2. v0.1.8 릴리스 검증 범위 및 세부 기준

v0.1.8 버전에서는 현재 Windows 사용자 로그인 세션의 화면 캡처 및 관측, 일반 프로세스 메모리 영역 조회 역량이 강화되었습니다:

- **원격 화면 캡처 실증**: 실제 물리 141 PC에서 3840×2160 해상도의 데스크톱 화면 캡처 및 MCP 인라인 PNG 반환 통과.
- **물리 입력 보호 (Safety Fence)**: 원격 마우스/키보드 주입 시 호스트 안전 검토 및 물리 사용자 우선권 보장.
- **프로세스 메모리 관측**: 32바이트 안전 메모리 읽기 및 비인가 영역 세그멘테이션 폴트 방지 통과.

---

## 3. 배포 패키지 무결성 및 서명 원칙

1. **빌드 매니페스트 (Build Manifest)**:  
   모든 배포 패키지(`dist/client-desktop-*/`)는 `build-manifest.json`을 동반하며, 실행 파일 및 아티팩트의 SHA-256 해시를 엄격히 기록합니다.
2. **코드 서명 (Authenticode)**:  
   정식 엔터프라이즈 배포 전까지 현재 개발용 빌드는 `NotSigned` 개발 빌드로 명시되며, 프로덕션 전환 시 EV 코드 서명 인증서가 적용됩니다.

---

## 4. v0.1.19 Gateway 관리 배포 게이트

Gateway 웹 관리 계획의 G00~G12 결과는 기존 v0.1.8 데스크톱 기준선과 별도로 판정한다. v0.1.19 후속 인수에서 native Setup/current-host SCM, runtime-free clean Windows 설치·실제 reboot·uninstall/data-delete, exact Gateway Portable + 실제 121 Web Console 경로를 새 증거로 닫았다. 실제 interactive install cancel·logout, installed-service N→N+1, 외부 OAuth/Codex, 8시간 soak는 독립 gate로 남긴다.

| 게이트 | 현재 판정 | 필요한/확인된 증거 |
|---|---|---|
| Gateway source/API/Console | **PASS** | v0.1.19 G00~G12 구현, Ruff PASS, Mypy **165 source files** 오류 0, version/OpenAPI/generated-client drift PASS, Console production build, 실제 격리 Console E2E 14 passed + management/update Chromium 2 passed. 최종 전체 pytest 수치는 [구현 현황 §26](implementation-status.md#gateway-management-final-0119)에 기록 |
| Backup/restore/update failure boundary | **PASS** | isolated disk-low fail-fast, corrupt/incomplete restore 거절, restore credential/session invalidation·UNKNOWN, Ed25519 + protocol/schema/updater/key-id 검증, persisted updater receipt recovery/rollback |
| Setup/Portable 계약 | **PASS** | win-x64 dry-run, 자체 CPython 3.12.11/runtime/Console/service/updater manifest 계약, Portable은 서비스 등록 없음 |
| Portable 후보 smoke | **PASS** | final-native3 ZIP SHA `5e9920…d4f7`, manifest 4,391 files, hash/CRC, TLS/readiness, one-use enrollment/login, PowerShell status, loopback MCP 86 tools |
| Native NSIS Setup EXE / SCM | **PASS** | pinned NSIS v3.04 + compiler/archive SHA, `/WX`. current-host 3회 start→health 5.804~6.078초를 보존하고, 최신 frozen installer source를 다시 컴파일한 Setup SHA `099145…922b8`을 runtime-free clean Windows VM에서 install/SCM/readiness PASS |
| Clean Windows / actual reboot | **PASS** | Windows 11 Enterprise Evaluation VM에서 설치 전 RACP/Python/Node 없음·default route 0을 확인. 최종 Setup install 후 실제 guest reboot에서 Automatic `NT SERVICE\RACP Gateway`가 다시 Running, updater Manual LocalSystem, `/healthz` PASS |
| Uninstall / state / explicit data delete | **PASS** | 최종 Setup uninstall이 service/binary/registry를 제거하고 ProgramData를 보존. guest-local backup hash 확인 뒤 `%ProgramData%\RACP\Gateway`만 명시 삭제해 범위 확인 |
| Install cancel | **BLOCKED_ENV** | fresh-install abort/failure/cancel cleanup contract와 packaging test는 PASS. 그러나 clean VM에 로그인된 interactive desktop이 없고 Desktop 제어 tunnel도 unavailable이라 **실제 Cancel 버튼**을 누른 증거는 없음. 강제 process 종료로 대체하지 않음 |
| G11 격리 acceptance | **PASS** | 선택 Portable ZIP `5e9920…d4f7` runtime으로 50 synthetic device + multi-agent/upgrade/restore/15초 soak, 10 session/SSE, 10k metadata/1M audit p95, SQLite writer recovery, secret scan/cleanup PASS. 역사적 bounded 결과의 old Setup metadata는 새 Setup 인수로 재사용하지 않음. 8시간으로 확대하지 않음 |
| 실제 121 A→B 웹 | **PASS** | 최종 선택 Portable `5e9920…d4f7` + 실제 192.168.29.121의 별도 source Agent v0.1.19. 등록/ONLINE, file/artifact/shell/job/terminal, Web Console login/approval/job/management logs, revoke/process/folder/firewall/private cleanup 확인 |
| 직접 Gateway MCP OAuth/Codex | **BLOCKED_ENV** | Codex CLI에 `racp` MCP server가 미등록(`codex mcp get racp` not found), 외부 IdP/callback/TLS OAuth 미구성. loopback catalog는 대체 증거 아님 |
| 8시간 soak | **NOT_RUN** | 15초 bounded functional soak는 통과했으나 8시간 RSS/handle/task/SSE 추세 증거 없음 |
| 정식 공개 release | **NOT_RUN** | 공개 release/upload는 요청되지 않아 수행하지 않음. signed Setup/hash/release metadata, install cancel/logout, installed-service update/rollback, 8시간 soak, external OAuth/Codex 등 위 blocked/not-run gate 해소가 선행돼야 함 |

상세 실행 수치와 GT01~GT29/GA01~GA03 판정은 [구현 작업 및 검증 현황](implementation-status.md)을 SSOT로 사용한다.

---

## 5. 관련 문서

- [기술 문서 포털](../README.md)
- [구현 작업 및 검증 현황](implementation-status.md)
- [런타임 및 플랫폼 호환성 매트릭스](compatibility.md)
- [데스크톱 클라이언트 가이드](../guides/desktop-client-guide.md)
- [Windows Gateway 배포 및 운영](../guides/gateway-deployment-guide.md)
- [Gateway 웹 관리 서버 작업 계획서](../spec/gateway-web-management-plan.md)
