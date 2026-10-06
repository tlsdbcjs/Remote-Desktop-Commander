# ADR-0013 — Broker 강제 종료 후 입력 해제

상태: foreground·로그온 Broker 개발 구현/실제 검증 · 2026-10-04 KST

## 종료 경계

Broker의 finally만으로는 TerminateJobObject/Agent crash 후 입력을 해제할 수 없다.
별도 `input_guardian`은 Broker PID/birth/SID/session을 kernel handle에 고정하고,
해당 Broker marker의 injected key/button down만 RAM에 보관한다.
최대 16개이며 foreign key/문자/좌표 이력을 기록하지 않는다.
Unicode VK_PACKET의 scan unit, modifier의 실제 left/right VK와 extended flag를 유지한다.

Broker 종료, 3초 hard input budget 초과, 눌린 상태의 외부 입력/foreground 변화, hook 장애 시
고정된 Broker를 종료하고 own key/button up을 보낸다. 이름이나 재사용된 PID로 대상을 다시 찾지 않는다.
종료 뒤 150ms quiet 구간까지 hook을 유지하고 입력 release echo를 확인한다.
release가 거부되거나 확인되지 않으면 종료 코드 2와 unverified를 유지한다.
Agent는 Guardian의 실제 종료와 코드 0을 확인한 뒤 cleanup complete를 반환한다.

## 독립성/실행

Broker Job은 GUID의 Global named object로 만들고 Guardian은 이 Job에 속하지 않아야 한다.
Job ACL은 Agent 관리 권한과 SYSTEM, 해당 로그온 사용자의 QUERY에 한정한다.
직접 base Python bootstrap은 inherited Job이 있으면 거부한다.
현재 Codex 실행 호스트는 외부 Job을 사용하므로, 임시 current-user interactive task로 실행하는
fallback을 실제 검증했다. Task는 password/elevation/자동 시작 trigger 없이 windowless Python을
지정 session에서 실행한다. Task Scheduler 자체 Job을 포함한 모든 OS Job의 부재를 주장하지 않는다.
독립성은 Broker Job 밖의 별도 Task Scheduler lifetime이며, 공통 외부 kill-all/power-off나
Guardian 자체의 강제 종료까지 소프트웨어 입력 해제를 보장한다고 표시하지 않는다.

Agent/Broker와 Guardian의 pipe는 기존 local-only ACL/PID·SID·session/birth/nonce 계약을 재사용한다.
allowlist는 status/arm/idle/controller bind/close뿐이며 arbitrary input/shell/evaluate를 제공하지 않는다.
Actor 권한은 Parent Broker와 고정된 controller다. Guardian의 process/token query grant도 최소 권한이다.
owner/Device credential은 전달하지 않고 bootstrap secret은 stdin 또는 private ACL config에만 둔다.
base interpreter의 stdlib bootstrap을 사용해 venv redirector의 PID와 실제 Broker PID를 혼동하지 않는다.

foreground Agent는 실제 인증된 broker.status의 PID/birth를 확인한 뒤 Guardian을 연결한다.
guardian startup cancellation은 native spawn thread를 버리지 않고 완료/정리까지 기다린다.
Guardian이 준비되지 않으면 Win32 입력을 dispatch하지 않는다. 관측/UIA와 Device ONLINE은 유지한다.

로그온 worker도 등록 뒤 별도 thread에서 Guardian을 실행하고, 실제 Controller의
PID/birth/SID/session/service SID와 같은 Broker Job 이름을 고정한다. Agent는 private pipe의
guard_info를 검증하여 Guardian을 발견하며 owner API/status에는 pairing secret을 노출하지 않는다.
같은 사용자 계정의 실제 등록·재시작 경로에서 연결과 종료 정리를 확인했다.

임시 task/config는 Guardian 자체의 finally에서도 정리한다. Agent crash 뒤에도 같은 경로를 사용한다.
private config의 pair ID/secret/정리 경로를 먼저 대조하고 link와 다른 root를 거부한다.
정리는 자신이 생성한 task 이름, 단일 config 파일과 빈 디렉터리에 한정한다. 정상 Actor와 Guardian의
중복 정리는 idempotent이며 살아 있는 Guardian의 종료를 확인하기 전에는 task/config를 삭제하지 않는다.

준비 timeout/identity·health 거부 경로도 task definition만 먼저 삭제하지 않는다.
아직 input을 arm하거나 Broker에 연결하지 않은 자체 task를 Stop하고 current-user 실행 instance가
없고 READY/DISABLED인 것을 확인한 뒤 definition/config를 제거한다. 중지 확인에 실패하면 복구
config를 보존하고 EXECUTION_UNKNOWN/cleanup_status=unverified를 유지한다.
PermissionError를 OSError readiness retry에 섞지 않아 identity/독립성 거부는 즉시 정리한다.
실제 준비 receipt 유실/health 거부 뒤 native process 종료와 파일 제거, 중지 실패 시 보존의 3개 시험을 통과했다.

## 새 lease 경합

Broker mutex가 abandoned가 되는 것만으로 새 입력을 허용하면 이전 버튼 해제와 경쟁할 수 있다.
별도 session release gate를 input arm 전에 reset하고 idle 또는 독립 cleanup 검증 뒤에만 signal한다.
새 lease는 mutex 획득 전후에 이 gate를 검사한다. 해제 실패로 gate가 남아 있으면 fail closed다.
Guardian은 실제 key/button/wheel receipt도 반환하여 살아 있는 PID만을 health로 사용하지 않는다.

## 실제 검증과 남은 범위

앞선 Windows 10 / 같은 사용자 세션의 실제 임시 창에서 GUI opt-in **11개 통과**를 확인했다.
native fixture가 실제 Ctrl와 left button을 누른 뒤 Broker Job termination / hard hold timeout /
실제 Agent process crash / RPC 취소 / HTTP operation 취소 / Device revoke 각각에서 입력 해제와
release gate를 확인했다. Job termination/hold timeout/RPC 취소 뒤에는 새 lease도 획득했다.
Guardian native exit code 0은 직접 process handle로 또는 cleanup_status=complete의 경로에서 확인했다.
fixture의 hold 동작은 tests에만 있으며 production allowlist/명령에는 포함하지 않는다.
최신 회귀는 고유 10개를 확인했고 Job termination은 OS 자체 창 활성화 거부로 skip이다.
현재 report와 이전 검증 구분은 [구현 및 검증 현황](../quality/implementation-status.md)에 기록한다.
일반 검사에도 실제 Agent crash 뒤 Guardian의 독립 종료와 임시 task/config 제거 시험을 포함했다.
Ledger의 Unicode/extended/modifier/mouse/bounds, parent/controller/server identity,
정리 ownership 경계 4개를 검증한다. 로그온 등록/Agent wire/launcher 복구 실제 시험 3개와
등록 거부 경계 1개를 통과했으며 실제 등록 시험에서 Guardian 연결과 정리도 확인했다.

공개 capability의 drag는 Guardian이 준비된 세션이 있을 때만 광고하고 해당 세션의 입력을 검증한다.
자체 창 대상의 실제 Agent/Gateway HTTP 정상 drag, OS dispatch result와 left button 해제를 확인했다.

SCM/다른 계정/자동 시작 설치, 실행 준비 전 scheduler failure/orphan recovery,
hook/Guardian 자체 failure·잠금/UAC/RDP 상태 변화는 후속 검증/구현이다.
전체 Phase 8과 Phase 9–12, 기존 참조 OS/host/security/soak gates는 계속 남아 있다.

## 공식 기술 근거

- [Job child inheritance/breakaway/kill-on-close](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [Task current-user non-admin security context](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks)
- [Task registration](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertaskdefinition)
- [RunEx session selection](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/nf-taskschd-iregisteredtask-runex)
- [Injected/extended/transition keyboard flags](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-kbdllhookstruct)
- [SendInput insertion/UIPI limits](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
