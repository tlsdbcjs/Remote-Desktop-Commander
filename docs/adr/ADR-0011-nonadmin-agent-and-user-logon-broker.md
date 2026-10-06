# ADR-0011 — 비관리자 Agent와 사용자 로그온 Broker

상태: 개발 구현 · 2026-10-04 · 실제 SCM/서로 다른 계정 gate 미검증

개발정의서 E9의 Agent Service 기본 identity는 비관리자 계정/서비스 identity다.
[WTSQueryUserToken](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/nf-wtsapi32-wtsqueryusertoken)은
LocalSystem과 TCB privilege를 요구하므로 이 API를 기본 Agent의 사용자 프로세스 시작 경로로
사용하지 않는다. GUI는 [interactive service 분리 원칙](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)에
따라 지정 사용자의 로그온 launcher에서 시작하고, local-only pipe로 Agent에 등록한다.
Agent는 사용자 token이나 password를 전달받거나 복제하지 않는다.

## 등록과 OS identity

Agent의 `--desktop-login-user-sid`는 설치/로컬 설정에서 GUI를 위임할 사용자 SID를 지정한다.
SCM Session 0에서는 `--service-sid`가 필수이며 실제 Agent token의 enabled service group과
일치해야 한다. LocalSystem과 관리자 service identity는 거부한다. foreground 개발 Agent는
자기 identity SID를 사용할 수 있다. 등록 endpoint의 ACL은 이 Agent principal과 지정 사용자
SID만 허용하고 REJECT_REMOTE_CLIENTS/FIRST_PIPE_INSTANCE를 적용한다.

로그온 worker는 실제 PID/생성 시각/TokenUser/TokenSessionId로 검사한다. 등록 요청의 Device와
session은 실제 peer와 일치해야 하며 임의 token 필드는 strict schema로 거부한다. 사용자
worker는 GUI backend를 만들기 전에 Agent 소유 named Job에 편입한다. 응답에 새 pairing secret과
nonce를 보내고, 소비 receipt의 HMAC를 확인한 뒤 supervisor에 Job/peer handle 소유권을 넘긴다.
기존 live Broker와 session 충돌은 기존 Broker를 덮어쓰지 않는다.

서로 다른 계정의 peer identity 검증을 위해 process/token에 필요한 최소 ACL을 설정한다.
Agent는 지정 GUI 사용자에게 process query/synchronize와 TOKEN_QUERY만 허용한다. worker는
Agent principal에 process query/synchronize/set-quota/terminate와 TOKEN_QUERY를 허용한다.
TOKEN_DUPLICATE/IMPERSONATE나 process VM_READ는 부여하지 않는다. 기존 owner/system DACL을
보존하고 lifetime 종료 때 원래 descriptor를 복원한다.

public `login-endpoint.json`에는 Device와 Agent/service SID만 있다. credential, owner token,
pairing secret은 없으며 지정 사용자에게 read만 부여한다. 기존 Agent credential 경로의
권한을 확대하지 않는다. 실제 설치 계정·credential ACL/DPAPI provisioning은 Phase 11 gate다.

## 사용자 launcher와 수명

`racp-login-broker --endpoint-file <절대 경로>`는 지정 로그온 사용자 context에서 실행한다.
launcher는 관리 대상 worker와 별도 프로세스라 Agent Job 정리 후에도 유지되어 다시 등록한다.
worker는 `--once` 경로로 handshake 뒤 기존 Broker backend를 실행한다. launcher의 환경은
허용한 OS 변수만 전달하고 자식 console은 숨긴다. 실패 backoff는 0.5초에서 최대 30초다.

등록된 worker도 [ADR-0013](ADR-0013-independent-input-release-guardian.md)의 입력 해제 Guardian을
해당 named Job 밖에서 실행한다. Agent는 private pipe로 Guardian을 발견하고 실제 worker/controller
identity와 Job 범위를 검증한다. 같은 계정의 실제 등록 시험에서 연결, 정리 완료와 임시 config
제거를 확인했다. SCM/다른 계정의 Scheduler 권한까지 검증한 결과는 아니다.

WTS의 사용자 SID/로그온 상태를 관측하며 알려진 logoff나 사용자 변경 때 worker/launcher를
종료한다. 조회 실패는 logoff의 증거로 간주하지 않는다. 연결이 끊긴 RDP 세션은 관측할 수
있으나 입력은 SESSION_UNAVAILABLE다. WTSINFOEX lock flag와 input desktop을 함께 검사하여
locked와 unlocked 상태의 secure desktop을 구분하고, 모르는 상태는 unknown으로 표시한다.
자동 unlock/UAC elevation은 없다. 실제 잠금/UAC/RDP 전환 시험은 아직 남았다.

## SCM host와 검증 범위

`racp-agent-service --config-file <절대 경로>`는 SCM dispatcher 전용 entry point다.
`agent-service-config-v1.schema.json`은 absolute workspace/data/credential 경로, 기대 Agent SID와
service SID, profile와 허용 GUI 사용자를 정의한다. identity를 먼저 검사한 뒤 credential을 연다.
STOP/SHUTDOWN handler는 stop pending을 보고하고 event loop의 Agent task를 취소한다.
Agent의 기존 정리 경로 완료를 기다린 뒤 service run을 끝낸다. installer나 service account를
이 entry point에서 생성하지 않는다.

실제 같은 OS 계정의 별도 프로세스에서 등록 pipe/Job 편입/Agent wire capability/정리와
launcher의 새 pairing·새 PID 복구를 검증했다. 서로 다른 계정, 실제 SCM service SID/권한,
계정 provisioning과 자동 로그온 시작을 검증한 것으로 표시하지 않는다. 제어 handler의
비동기 STOP 동작과 identity/config 거부는 경계 주입 시험이다.

OS 자동 시작 등록, 설치·upgrade·rollback·credential 준비와 실제 SCM test는 Phase 11에서
구체적인 설치 산출물을 만든 뒤 수행한다. Phase 8 UI Automation/input hook/실제 GUI/DPI,
참조 OS와 나머지 전체 개발 계획도 계속 사용자 목표에 포함한다.
