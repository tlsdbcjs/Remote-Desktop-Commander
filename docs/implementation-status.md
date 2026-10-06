# RACP 구현 작업 현황

기준일: 2026-10-06 KST · 상태: 진행 중 · 수행: Codex 구현/로컬 검증

전체 사용자 목표는 개발정의서에 따른 구현이다. 아래 기반 구현의 완료를 전체 MVP 또는
v1 완료로 간주하지 않는다. 원문 문서 두 개는 수정하지 않았다.

사용자 추가 지시에 따라 [원격 PC 핵심 흐름](remote-pc-core-plan.md)을 우선한다.
원격 연결 → 실제 AI/MCP client → PC 등록·폴더 권한 → 백그라운드 수명 → 실제 두 PC 인수시험 순서로
진행한다. 리버싱 추가 확장은 이 흐름 뒤로 두며 기존 전체 요구사항/phase gate는 유지한다.

Windows 후속 작업·상세 시험·합격 기준·증거 수집은
[작업 및 테스트 계획서 v1.0](RACP-Windows-작업및테스트계획서-v1.0.md)을 따른다.
이 계획은 최신 첨부의 실제 메모리 32바이트 읽기 성공과 Windows 341개 검사 통과를 반영하며,
과거 기록의 미검증 상태와 후속 성공 범위를 구분한다.

2026-10-06 사용자 정정에 따라 특정 디버거 선택·통합을 선행 조건으로 두지 않는다.
Client 0.1.8은 현재 로그인 세션의 Windows 화면·입력 허용 설정과 일반 프로세스 메모리
영역/읽기를 추가한다. A의 도구로 B의 자료를 활용하는 흐름이다.
실제 Windows 자체 창·프로세스 및 Electron HTTPS/WSS 검증을 통과했으며,
141 새 버전과 Gateway 갱신 후 실제 Codex 제어 인수시험은 남는다.
[0.1.8 사용·검증 범위](remote-pc-windows-0.1.8.md).

| 단계 | 구현 상태 | 검증과 남은 gate |
|---|---|---|
| Phase 0 | uv/pnpm monorepo, schema/registry/policy/logging, ADR, frozen lock, CI, wheel/Console build | Windows 10 로컬 품질·Node 22 gate 검증. 별도 Broker/pipe spike 검증. Windows 11/Ubuntu와 Service packaging 미검증 |
| Phase 1 | owner/Device 분리 인증, 외부 OAuth MCP resource server, Console/racp-connect·보호된 설정, 등록·WS/WSS·TLS, heartbeat/reconnect·epoch·revoke, 사용자 background 시작/상태/종료와 entry point 잠금 | HTTPS 등록·설정 resume·MCP core, 실제 background의 재연결/정상 종료/Windows crash UNKNOWN·비재실행과 Keycloak 확인. 실제 두 PC·AI host·공개 OAuth, installer/자동 시작과 full recovery inventory 확대 필요 |
| Phase 2 | argv/명시적 shell, cwd/env, timeout/cancel, journal dedupe, 승인 CLI/API, 최소 Job/Artifact, MCP adapter | 실제 Windows 실행/장애 주입 검증. Codex/ChatGPT host와 Ubuntu 검증이 남음 |
| Phase 3 | fs/process/CLI/MCP, 이름 있는 허용 폴더와 source/destination 선택, binary write, scoped/resumable transfer | 실제 MCP 폴더 workflow/CLI/Artifact/Handle cwd·승인·동시성 경계와 Windows 100 MiB disconnect/resume 검증. cross-volume/reference OS gate 남음 |
| Phase 4–5 | PTY/WS credit·gap, Handle 복구, Artifact, Job/queue/deadline, retention/resolution/출력 복구·퇴역 read의 correlation 재연결 | Windows 재시작·한도·출력 cap과 만료 read replay 뒤 core 연결/위조 거부 검증. reference OS/실제 host/soak 남음 |
| Phase 6 | Console PC 등록 토큰/허용 폴더 선택·profile·실제 capability 상태 표시, cookie/CSRF, SSE replay/gap·stale 감지, keyset 목록, Artifact preview, WS terminal viewer | 실제 Chromium 14개 흐름 검증. 상세 doctor/metrics·인수시험 확대와 참조 OS 남음 |
| Phase 7 | 격리 Playwright worker, browser/page Handle, 관측 ref, 폼 입력, screenshot, Artifact 파일, profile 정리, frames, CDP opt-in, evaluate 정리, 비동기 metadata event/ACK/gap, 파일 health | 실제 Chromium HTTP/CLI/MCP/CDP/frame/event 검증. 실행 health 확대·참조 OS gate 남음 |
| Phase 8 | 별도 Broker/pipe 인증, 로그온 등록·SCM host, Win32/PNG Artifact, UIA, 입력 hook/세션 mutex, foreground·로그온 Guardian/release gate, 조건부 공개 drag, 준비 실패 task 종료 확인 | 앞선 자체 GUI 11개 통과. 최신 회귀는 고유 10개 확인, Job termination 1개는 OS 활성화 거부로 skip. SCM/다른 계정·준비 전 orphan recovery·Notepad/Calculator·나머지 DPI/RDP/UAC gate 남음 |
| Phase 9 | allowlist supervisor, fixed typed RE registry/정책/CLI/MCP, Agent health/lease/Handle/target snapshot·pagination·stop wait·memory Artifact, 실제 GDB/MI와 Ghidra headless adapter/로컬 hash 승인 설정 | 실제 debugger와 static query/Unicode mutation/close 및 plugin Job crash/core 유지 확인. Linux Ghidra containment·attach/detach·durable event/storage·External MCP·전체 license gate 남음 |
| Phase 10 | 선택 기능, 미구현 | Frida 포함 시 별도 구현·검증 필요 |
| Phase 11–12 | 공통 Electron client와 native Agent staging, Windows EXE·NSIS/ZIP, 현황·Tray·완전 종료, opt-in 로그인 시작과 installer 정리/백업, macOS/Linux target·native CI 구성 | Windows UI·실제 두 PC 원격 작업과 native 설치/업그레이드/제거의 기본 흐름 확인. clean VM·실제 OS 로그온·macOS/Ubuntu native gate·서명/전체 migration/rollback/backup restore/security/performance/soak 남음 |

## 검토 가능한 산출물

사용자 추가 지시에 따른 [3 OS 데스크톱 클라이언트](desktop-client-guide.md)를 구성했다.
Windows의 실제 Electron E2E와 packaged EXE 실행, Python bridge 5개와 Node bridge 3개를 확인했다.
사용자가 141 PC에 client 0.1.0을 실행한 뒤 실제 두 PC의 파일·명령·ConPTY·동일 key 비재실행을
확인했다. [두 PC 결과](two-pc-141-result.md). 실제 AI host/전체 복구·설치 gate와 구분한다.
Windows client 0.1.1에 opt-in 로그인 후 연결을 추가하고 Node 6개/Electron saved Agent resume와
실제 고유 HKCU startup 등록/해제를 확인했다(ADR-0024). OS logoff/reboot는 남으며 installer 제거는 0.1.3에서 후속 검증했다.
0.1.2에 실제 작업/최근 활동 dashboard와 native Tray, 정리 확인 후 완전 종료를 추가했다(ADR-0025).
활동 tail/schema 경계와 tray lifecycle/stale unit, 실제 장기 Job 종료/복구와 native Tray 콜백 E2E,
packaged 0.1.2의 Tray/ICO/bridge/완전 종료를 확인했다. Node 9개와 Windows/Linux type 128개를 통과했다.
macOS/Linux CI와 target 구성은 해당 OS에서 실행한 증거가 아니다(ADR-0023).
0.1.3은 installer에서 background Agent를 정리하고 상태를 검증된 SHA-256 백업으로 보존한다.
제거 시 해당 설치의 startup 항목을 정리하고 등록 정보는 보존한다(ADR-0026).
실제 고유 fixture의 실행 파일 버전 전환, epoch/boot 변경, 저장된 Agent journal,
원격 작업 비재실행, backup hash와 제거 후 credential 보존을 확인했다.
Windows current-user의 기본 시험이며 clean VM/SCM/전체 migration·복구 gate와 구분한다.
0.1.4는 최초 등록 오류를 code별 안전한 안내로 구분하고, 저장된 등록을 읽지 못하면 재등록
form으로 넘어가지 않도록 수정했다(ADR-0027). Agent → Gateway 연결 방향은 유지한다.
실제 Electron의 만료/소비 token 거부·새 token 등록, 저장된 등록 재실행, 손상된 자기 fixture의
credential 보존/재등록 금지와 원격 작업·tray/Job 정리를 확인했다. packaged 0.1.4의 native runtime,
CA preflight와 정상 종료도 통과했다. 관련 Python 19개, Node 10개, TypeScript/Vite build를 확인했다.
0.1.5는 Gateway에서 발급한 연결 파일 선택으로 최초 setup을 간소화했다(ADR-0028).
주소·token·CA의 별도 입력은 직접 입력 모드로 남긴다. private CA는 Agent state에 보존하고
선택 뒤 파일 변경·만료를 거부하며 기존 credential을 덮어쓰지 않는다.
실제 Console 14개, private CA HTTPS/WSS Electron E2E, packaged preview와 140 Gateway의 파일
발급/패키지 preview를 확인했다. 관련 Python 27개, Windows/Linux mypy 131개와 Node 10개를 통과했다.

`apps/gateway`, `apps/agent`, `apps/cli`, `packages/domain`, `packages/protocol`,
`packages/policy`, `packages/observability`, `packages/sdk`에 실제 코드가 있다.
`scripts/check.py`는 format/lint/type/unit/integration 검증을 실행하고
`scripts/build.py`는 8개 wheel 및 SHA-256 manifest를 생성한다.
품질 결과는 `dist/test-results.xml`, 빌드는 `dist/build-manifest.json`에 남긴다.
Phase 0–2 기반 gate는 pytest 20 passed였으며, Phase 3 추가 검증 결과는
[Phase 3 결과](phase-3-result.md)에 기록한다.
Artifact/binary write의 후속 결과는 [Phase 3–5 기록](phase-3-5-artifact-result.md),
지속 터미널과 Handle 복구 결과는 [Phase 4 기록](phase-4-terminal-result.md)에 있다.
WS credit/control queue/CLI stream 결과는 [추가 기록](phase-4-stream-result.md)에 있다.
별도 Job과 admission/deadline 결과는 [Phase 5 기록](phase-5-job-result.md)에 있다.
보존·출력 첨부 복구 결과는 [추가 Phase 5 기록](phase-5-retention-output-result.md)에 있다.
PC 등록·여러 작업 폴더·background·desktop client와 installer·등록 진단·연결 파일 후속을 포함한 최종 전체 검사 결과는 **315 passed, 18 skipped**다.
건너뛴 시험은 GUI opt-in 11개, native GDB opt-in 2개, native Ghidra opt-in 2개와 실제 POSIX runner가
필요한 dirfd/symlink 시험 1개, 실제 Docker Keycloak opt-in 1개와 현재 Windows 계정에서 symlink를
만들 수 없는 activity log link 시험 1개다. 별도로 활성화한 실제 Keycloak의
로그인·동의·PKCE 후 MCP 파일 저장/명령 실행 시험은 **1 passed**, JWT/loopback 경계 unit은
**21 passed**다. JWT parser/URL 경계와 실제 CLI doctor의 비밀 비출력 후속은 별도 **22 passed**다.
초기 병행 검사에서는 Guardian 준비 확인 제한시간으로 1 failed/260 passed/17 skipped였지만,
관련 단독 3개 재검과 최종 전체 검사 모두 통과했다. [OAuth 결과](phase-1-2-oauth-result.md).
별도로 활성화한 실제 GDB HTTP integration 2개와 parser/hash unit
8개는 **10 passed**이며 `dist/gdb-native-results.xml`에 기록한다.
별도 Ghidra native 2개/runtime·containment unit 6개는 **8 passed**다.
원격 TLS/MCP workflow integration 2개와 TLS 경계 unit 5개는 전체 검사에서도 **7개 통과**했다.
별도 Gateway CLI process는 TLS를 사용해 0.0.0.0에 bind하고 같은 PC의 client/Agent로 검증했다.
실제 두 PC와 실제 AI host 연결을 대신하지 않는다. [원격 TLS 결과](phase-1-remote-tls-result.md).
PC 등록/설정/보존 후속 묶음은 **14 passed**, 실제 Chrome Console은 **13 passed**다.
별도 실제 Agent의 읽기 전용 등록과 저장한 설정으로 cwd가 달라도 재시작하는 경로를 확인했다.
보존 기간 뒤 Gateway가 삭제한 임시 읽기 결과의 replay로 연결이 끊기는 문제도 서버 감사 기록의
정확한 correlation 확인으로 수정했다. 위조/다른 epoch와 일반 result 경로는 계속 거부한다.
[PC 등록 결과](phase-1-pc-onboarding-result.md), [연결 안내](pc-connect-guide.md).
별도로 활성화한 자체 GUI 시험은 앞서 **11개 통과**했다. 최신 회귀는 9 passed/2 skipped,
건너뛴 2개 재검은 1 passed/1 skipped다. 현재 고유 10개를 확인했고 Job termination 시험은
OS가 자체 창을 활성화하지 않아 건너뛰었다. desktop timeout·등록·SCM host 경계와
프로세스 정리를 포함한 검증을 통과했다. frozen sync, Ruff format/lint,
Windows/Linux 타입 target의 128개 source file, 8개 개발 wheel 빌드를 통과했다.
Console의 별도 format/type/OpenAPI client drift/Node 22 production build도 통과했다.
실제 Chromium E2E 최신 **14개 통과**와 한계는 [Phase 6 기록](phase-6-console-result.md)에 있다.
이번 회귀에서 OS Agent 재시작과 UI event 대기 시간을 분리해 실제 새 epoch의 reconciliation을
확인하는 시험 fixture로 보완했다. ONLINE/UNKNOWN 상태를 직접 주입하지 않았다.
Linux 타입 검사는 실제 Ubuntu 실행 검증을 대신하지 않는다.
여러 작업 폴더 묶음은 **13 passed**다. 실제 MCP 저장/검색/폴더 간 복사·이동/실행/재시작,
CLI/바이너리 Artifact/terminal·process cwd와 선택 변경의 dedupe/승인 거부를 확인했다.
[여러 폴더 결과](core-workspace-result.md), [사용 안내](named-workspaces-guide.md).
사용자 Agent background 후속은 unit 6개와 실제 별도 process integration 1개를 전체 gate에서 확인했다.
controller 종료 후 유지, Gateway 재연결, 중복 entry point/journal 보호, 정상 종료의 cleanup receipt,
Windows crash 뒤 UNKNOWN과 같은 key의 mutation 비재실행을 검증했다.
Console 재시작에서 발견한 Windows 짧은 경로 alias의 lock 시작 오류도 canonical 경로로 수정했다.
[background 결과](core-background-result.md), [사용 안내](background-agent-guide.md).
이 진전은 서비스 설치/자동 시작/업데이트·복구·soak의 완료가 아니다.
desktop client 추가 회귀의 첫 실행은 기존 CDP fixture가 DevToolsActivePort 생성 전 읽기로
289 passed/17 skipped/1 error였다. 자체 Chromium의 port 게시를 제한 시간 안에서 기다리도록
보완하고 관련 11개 재검과 최종 전체 295 passed/17 skipped를 확인했다.
8개 wheel 재빌드와 SHA-256 manifest 생성을 확인했다. 자세한 범위는
[Phase 0–2 결과](phase-0-2-result.md)를 참조한다.

## 요구사항 추적

| ID | 현 시점의 증거 | 범위 |
|---|---|---|
| AUTH-01 | `test_auth_01_device_token_cannot_control_owner_api` | 실제 enrollment 재사용 거부, owner API Device token 거부. 만료 토큰 추가 coverage 필요 |
| AUTH-02 | Origin/Host, cookie/CSRF/session 만료, 외부 OAuth signature/issuer/resource/subject/client/expiry/scope와 Device 접근 거부 | 로컬 실제 Keycloak 후속 통과. 공개 OAuth·실제 AI host·객체 소유권 확대 검증 남음 |
| RPC-01 | `test_rpc_01_one_hundred_concurrent_mutations_execute_once` | 실제 100 동시 호출 side-effect=1, payload 변경 conflict |
| RPC-02 | lost-result 재연결 테스트, Agent journal restart unit test | 실제 Agent/Gateway crash 확대 필요 |
| RPC-03 | strict discriminated schema, frame/depth/string/HTTP body limit unit/integration | real oversized WS/epoch 부정 테스트 확대 필요 |
| AUTH-03 | revoke/lease watchdog 실제 프로세스 정리 테스트 | 60초 실시간 lease wall-clock와 clean runner 확인은 추가 gate |
| SHELL-01 | 한글·공백 argv/cwd, stdout/stderr, nonzero exit, env 삭제 테스트 | Windows 10 실제 프로세스. Ubuntu 미검증 |
| LIFE-01 | timeout child+grandchild, Job cancel, revoke/lease cleanup 테스트 | Windows Job Object 실제 검증. Linux process-group 한계 별도 검증 필요 |
| POLICY-01 | deny 우선, 기본 deny, approval binding/재사용 테스트 | TTL/disabled/policy revision 변경 추가 coverage 필요 |
| ART-01 | 100 MiB 실제 HTTP disconnect/resume와 Agent binary write | scope/hash, 최종 SHA-256 일치 |
| ART-02 | quota 예약/ENOSPC 주입/incomplete cleanup/GC-reader 경합 | 물리 disk-full 시험은 미수행 |
| PTY-01 | 지속 REPL/resize/독립 cursor/overflow/Unicode/WS credit·gap/재시작 | Windows 10 검증. reference OS/soak 검증 남음 |
| STATE-01 | Gateway/Agent 복구, boot 만료, cancel intent, tombstone/late resolution/출력 복구 | 강제 crash 조합과 참조 OS 검증 확대 필요 |
| DESK-02 | pipe/lease/capture/Provider 경계, UIA ref 11개, input/window hook 2개, Guardian ledger/identity/정리 4개, startup task 경계 3개, 자체 GUI 최신 고유 10개 | 실제 입력 해제·공개 HTTP drag·로그온 연결·task 정리. 최신 Job kill 재검 skip, SCM/다른 SID·준비 전 orphan recovery·RDP/UAC/참조 OS 남음 |
| RE-01 | plugin 계약/수명 27개, typed application fixture, GDB native 2개+parser/hash unit 8개, Ghidra native 2개+runtime/containment unit 6개 | Windows 10 실제 static/debugger fixture와 plugin crash/core 생존 확인. 지원 OS·attach/detach·storage/event·External MCP·나머지 Phase 9 gate 미완료 |
| UI-01 | 실제 Chromium 로그인/승인/취소/UNKNOWN/offline/terminal safe text | gap/expired/pagination/preview/WS 검증. 다른 browser·accessibility 확대 필요 |

## 알려진 제한

현재 single owner, single Gateway worker다. SQLite schema 1은 알려지지 않은 schema의
시작을 거부하며 아직 Alembic migration은 없다(ADR-0001). registry에는 shell과
filesystem/process/terminal/browser/desktop와 fixed RE operation을 추가했다. 설치 backend가 준비돼야
해당 RE operation을 실행한다. 그 외 미구현 operation은 CAPABILITY_UNAVAILABLE다. foreground Agent 실행은
Windows Service/Broker 검증을 대신하지 않는다.

출력 inline UTF-8 text 한도는 64 KiB, 셸 원본 수집은 stdout+stderr 합계 64 MiB다.
채널별 framed binary의 물리 spool 한도는 작업당 72 MiB다.
binary filesystem Artifact는 1 GiB 상한으로 스트리밍한다.
spool 초과는 RESOURCE_EXHAUSTED와 부분 결과로 표시하고 프로세스 트리를 정리한다.
Artifact 기본 HTTPS 경로는 SHA-256와
Device/owner scope를 확인하며 resume/GC/100 MiB 전송은 [추가 검증](phase-3-5-artifact-result.md)을 통과했다.
업로드가 실패한 spool은 Agent data와 별도 첨부 catalog에 보존해 자동 재전송한다.
Agent spool 예약은 10 GiB/64건, 유효 transfer는 Device당 2개다.
원래 실행 결과는 유지하고 `operation outputs`로 회수된 Artifact를 조회한다.

Browser의 실제 Chromium 검증 범위와 후속 계약은 [Phase 7 기록](phase-7-browser-result.md)에 있다.
외부 OAuth 인증 기반과 실제 Keycloak 후속 결과는 [OAuth 결과](phase-1-2-oauth-result.md)에 있다.
다음 우선 구현은 실제 AI client 원격 연결과 PC 등록·폴더 권한·백그라운드 Agent 흐름이다.
Phase 9의 External MCP mapping과 durable event/storage 계약, Linux Ghidra containment는 후속 유지한다.
실제 GDB의 attach/detach·TTL/revoke/cancel 확대와 charset/다른 architecture 호환 gate도 남아 있다.
Phase 8 Guardian 준비 전 orphan recovery/장애 경계도 남아 있다.
SCM/서로 다른 계정·자동 시작 설치 검증은 Phase 11의 준비된 설치 산출물과 함께 수행한다.
Phase 5 잔여 관리 gate, Phase 6 상세 진단과 Phase 7 실행 health/참조 OS gate도 남아 있다.
실제 host와 참조 OS 검증도 계속 남아 있다.

Phase 8 기반 경계와 미구현 범위는 [ADR-0010](adr/ADR-0010-local-session-broker-and-input-fences.md)에 기록한다.
Broker는 명시적 Agent 세션 설정으로 활성화한다. foreground 자신의 계정/세션만 launch한다.
화면/취소/실제 검증 범위는 [Phase 8 결과](phase-8-broker-result.md)에 기록한다.

비관리자 Service/로그온 Broker의 등록 계약과 실제 검증 범위는 [ADR-0011](adr/ADR-0011-nonadmin-agent-and-user-logon-broker.md)에 있다.
UIA/reference·입력 hook·세션 mutex와 실제 자체 창 검증은 [ADR-0012](adr/ADR-0012-ui-automation-and-input-watch.md)에 있다.
독립 입력 해제 Guardian의 실제 검증과 남은 계약은 [ADR-0013](adr/ADR-0013-independent-input-release-guardian.md)에 있다.
Phase 9 기반 계약과 증거는 [ADR-0014](adr/ADR-0014-allowlisted-plugin-supervisor.md)와
[Phase 9 결과](phase-9-plugin-result.md)에 있다.
Agent application과 typed Handle 경계의 후속은 [ADR-0015](adr/ADR-0015-re-application-handles-and-policy.md)에 있다.
실제 GDB/MI launch/소유 target 수명과 검증은 [ADR-0016](adr/ADR-0016-native-gdb-mi-launch-and-owned-target.md)에 있다.
실제 Ghidra 분석과 프로젝트/containment 검증은 [ADR-0017](adr/ADR-0017-native-ghidra-headless-analysis.md)에 있다.

## 2026-10-06 · 등록 정보 수정과 복구

Client 0.1.6은 PC 설정의 등록 정보 수정과 설정 오류 화면의 등록 정보 복구를 추가했다.
Gateway 주소, 폴더·추가 허용 폴더·profile·CA를 수정하며 Device ID/credential과
data_dir/journal은 유지한다. 실행 중 변경, stale revision, 유효하지 않은 경로와 백업 실패는 저장을 거부한다.
현재 OS 계정으로 보호된 credential을 해독할 수 없는 경우 원본을 유지한다.

핵심 설정 Python 17개, mypy 132개 source가 통과했다. 실제 Electron/private CA HTTPS/WSS에서
CA 파일 유실 → GUI 복구 → 같은 Device/read_only 재연결 → 원격 파일 읽기, 실행 중 저장
거부 → 명시적 중지/저장, Gateway 주소 변경, full exit와 기존 credential 손상 보존을 확인했다.
별도의 141 새 버전 실행으로 간주하지 않는다. 근거 로그는 dist/client-0.1.6-e2e.log다.

전체 최초 gate는 318 passed/18 skipped/1 failed였다. 실패한 Windows Guardian readiness
시험은 별도 실행에서 3 passed를 확인했다(dist/guardian-startup-0.1.6.xml).
최종 전체 gate 재실행은 **322 passed/18 skipped**, format/lint/mypy 132개 source를
통과했다. Node 10개, 실제 packaged 0.1.6 실행과 NSIS/ZIP 생성도 확인했다.
ZIP CRC와 Agent/실행 파일/ASAR **4,790개** hash를 확인했다. Windows 개발 빌드는
unsigned이며 실제 141의 0.1.6 실행, clean VM/rollback/전체 복원/AI host는 미검증이다.
[설정 수정 근거](adr/ADR-0029-local-settings-repair.md).

## 2026-10-06 · Client 0.1.7 등록 정보 편집 직접 진입

0.1.7은 저장 설정 검증이 실패했을 때 보이던 **등록 정보 복구 / 상태 확인 / 완전 종료**
3버튼 중간 화면을 제거했다. 오류 상태에서도 **등록 정보 편집** 폼을 즉시 표시하고,
정상 PC 설정의 버튼과 편집기 제목도 동일한 이름으로 통일했다. 오류 상태에서 편집기를
취소하여 예전 중간 화면으로 돌아가는 경로도 제거했다.

Client build와 Node 10개, 핵심 설정 Python 17개, 실제 Electron/private CA HTTPS/WSS의
설정 복구·Gateway 편집·동일 Device 재연결 E2E, packaged 0.1.7 smoke를 통과했다.
포터블 ZIP은 CRC와 Agent/실행 파일/ASAR 4,790개 hash를 확인했다. Python 소스는
0.1.6 이후 변경하지 않아 전체 Python suite는 0.1.7에서 다시 실행하지 않았다.
0.1.6-final은 보존하고 새 배포는 `dist/client-desktop-0.1.7-final`에 분리했다.

[Windows 전체 인수 gate](windows-release-gates.md)에 원문 E14–E16의 남은 증거를 기록했다.


2026-10-06 사용자 시작 후 실제 141 Client 0.1.7의 EXE/ASAR를 현재 배포 ZIP hash와 비교했다.
같은 Device의 epoch 2에서 file/shell/Artifact/ConPTY/Job cancel 기본 시험과 managed process
identity/wait/terminate, 임시 browser form/PNG, 100 MiB upload interruption/resume 및 원격
binary write/hash/read/download resume를 통과했다. 마지막에는 자기 status query 외 active
작업이 없었고 Agent 연결을 유지했다. [두 PC 결과](two-pc-141-result.md)를 참고한다.
이 업데이트는 desktop 비활성과 실제 AI host 미검증 상태를 변경하지 않는다.

2026-10-06 Codex 연동 후속에서는 실제 Gateway 재시작 뒤 141의 epoch 3 재연결과 같은
mutation의 비재실행(counter=1)을 확인했다. Windows CurrentUser 시험 CA 등록과 native
Codex CLI OAuth/PKCE/consent/keyring 저장도 통과했다. LAN Agent/API와 동일 process의
loopback HTTPS/OAuth MCP listener를 분리하는 `--local-mcp-port`를 추가했다. 관련 unit
36개, connection import/remote TLS 10개와 132개 source의 mypy/ruff 검사를 통과했다.
전체 Python 회귀는 332 passed / 18 skipped, 별도로 추가한 dual TLS socket/OAuth MCP→
fixture Agent 파일 읽기 integration은 1 passed다. 실사용 Gateway의 새 listener 적용은 자동 승인 검토가
재시작 명령을 거부해 사용자 실행용 script를 준비했다. 현재 Codex 앱에서 실제 141
MCP tool call은 여전히 미검증이다. 상세 증거와 한계는 [두 PC 결과](two-pc-141-result.md)에 있다.

2026-10-06 15:21 KST에 사용자가 적용 script를 실행한 뒤 실제 Gateway의 local MCP와
LAN socket을 확인했다. 141은 epoch 4 / ONLINE이다. native Codex refresh에서 발견한
시험 Keycloak의 offline_access optional scope/role 누락을 수정하고 PKCE 로그인, native
app-server 84개 tool 초기화와 60초 token 만료 후 재연결을 확인했다. 현재 대화 catalog
갱신은 앱 재시작이 필요하며 실제 141 tool call/HOST-01 gate는 미완료 상태다.

2026-10-06 앱 완전 재시작 후 현재 대화에 RACP 84개 도구가 반영됐다. 실제 first-class
MCP 호출로 141 Device 선택, 기존 한글 파일 읽기, 새 시험 폴더/파일 저장·읽기/hash,
원격 hostname/cwd 확인, 같은 shell key의 operation ID 유지 및 실제 counter=1을 확인했다.
Job RUNNING polling/cancel/CANCELLED/cleanup complete/PID 소멸, ConPTY 입력/read/close,
별도 headless browser의 한글 type/click와 MCP inline PNG 미리보기도 통과했다. 시험 소유
browser/terminal은 닫았고 마지막 Agent 연결은 RUNNING/epoch 4이며 자기 status query 외
진행 중 operation이 없었다. 증거는 `dist/codex-chat-141-20261006.json` 및 PNG/최종 status다.
이 결과로 현재 Codex 앱의 실제 PC 연결은 검증됐다. Windows desktop는 여전히 미설정이며
ChatGPT/전체 Windows 배포/SCM/복원/soak 등 별도 gate는 유지한다.
