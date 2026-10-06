# Phase 8 Broker/desktop 부분 구현 결과

2026-10-04 KST · Windows 10 Pro x64 · Python 3.12 · Pillow 12.3.0 · comtypes 1.4.17

desktop canonical registry, 입력 모델, 정책·journal, Agent Provider, CLI와 MCP를 연결했다.
Agent는 `--desktop-session-id`로 명시한 foreground 자신의 Windows 세션의 Broker를 시작한다.
설계와 남은 계약은 [ADR-0010](adr/ADR-0010-local-session-broker-and-input-fences.md)에 있다.
Windows Service의 다른 사용자 세션 launch와 전체 Phase 8 gate 완료는 아니다.
전체 Python gate는 219 passed/12 skipped, Windows/Linux 타입 target 111개 source,
8개 wheel과 Console production build를 통과했다. Linux 타입 검사는 실제 Ubuntu 실행이 아니다.
별도 실제 자체 GUI opt-in은 앞서 Guardian/취소/공개 HTTP drag를 포함해 11개를 통과했다.
Guardian 준비 실패 정리 보강 뒤 최신 회귀는 9 passed/2 skipped, 해당 2개 재검은 1 passed/1 skipped다.
이번 회귀의 고유 10개를 확인했고 Job termination 1개는 OS가 자체 창을 활성화하지 않아 skip이다.
report는 `dist/desktop-native-gui-results.xml`과 `dist/desktop-native-gui-retry-results.xml`이다.

## 실행과 전송

- Broker는 local-only Named Pipe ACL, peer PID/birth/SID/session, mutual nonce를 검증한다.
- foreground Broker는 base Python gate 뒤 Windows Job에서 실행되며 Agent crash/lease 종료 시 정리한다.
- session별 RPC 직렬화, timeout/cancel cleanup, bounded crash backoff와 별도 health 관측을 연결했다.
- Broker/자식 PID는 일반 process.terminate에서 보호한다.
- 원본 visible rectangle은 top-down native DIB/PNG, preview는 같은 pixel의 LANCZOS PNG다.
- 원본 16 Mi pixel/32 MiB, preview 긴 변 1600px/2 MiB, capture buffer 4개/64 MiB/30초 고정 TTL이다.
- 경로 공유 없이 owner/device/operation에 묶인 32 KiB chunk를 받고 Agent가 size/offset/EOF/hash를 검증한다.
- 원본과 preview를 별도 Artifact/output으로 게시하고 첨부 복구는 기존 durable output spool을 사용한다.
- MCP는 operation/owner에 묶인 제한된 PNG image content를 반환하고 GC reader pin과 hash를 검증한다.
- Console은 desktop scope의 세션·로그온 계정·현재 입력 가능 여부를 표시한다.

## 실제 검증과 fixture 경계

`tests/integration/test_broker_pipe.py`의 실제 Named Pipe/독립 Broker 3개,
`tests/unit/test_desktop_fences.py`의 경계 주입 8개,
`tests/unit/test_desktop_capture.py`의 buffer·scope·TTL·quota·변환 2개,
`tests/integration/test_desktop_provider.py` 5개를 검증했다.

- 실제 supervisor/Job/wire의 세션 발견, read_only 입력 거부, 잘못된 세션 거부와 protected PID 확인.
- 현재 WTS inactive 세션의 SESSION_UNAVAILABLE 거부, Device ONLINE과 filesystem 실행 유지.
- native GDI memory DC에서 생성한 알려진 RGB의 top-down orientation/PNG CRC/pixel/preview 검사.
- GDI capture 100회 뒤 object 수가 증가하지 않음.
- 별도 synthetic Broker fixture의 32 KiB 초과 원본·preview를 실제 pipe/HTTP Artifact로 전송해 hash 확인.
- 같은 screenshot key를 재사용할 때 같은 operation/result와 추가 캡처 없음.
- 실제 MCP client가 desktop_screenshot의 image content를 받음.
- synthetic input handler를 멈춘 뒤 cancel/deadline이 Broker/redirector 프로세스 종료를 확인하고
  CANCELLED/TIMED_OUT을 기록함. 정리 완료와 입력 side effect unknown을 따로 표시함.
- Console 실제 Chromium E2E 11개에 세션·로그온 계정 scope 표시를 포함함.

synthetic fixture는 생성한 byte와 GUI 동작 모형을 사용하며 사용자의 실제 화면을 캡처하거나
키/mouse 입력을 보내지 않는다. GDI memory DC 검증도 실제 visible-screen 캡처 인수시험을
대신하지 않는다. TTL/lease 경계는 monotonic 주입이고 15초/30초의 장기 wall-clock gate가 아니다.

1920×1080 고엔트로피 synthetic byte의 PNG+preview 인코딩은 약 3.3초에서 약 460ms로 개선했다.
이는 GUI·IPC·Artifact 저장을 포함한 end-to-end 800ms 성능 gate가 아니다.

## 남은 인수 범위

실제 SCM/다중 계정·로그온 자동 시작·logoff/RDP 변화, Guardian 자체 failure/준비 전 orphan recovery,
잠금과 UAC secure desktop의 실제 상태 전환, 실제 물리 사용자 입력 경쟁,
Notepad/Calculator click/type/screenshot, 100/125/150% DPI·음수 origin 2모니터,
실제 performance/soak와 Windows 11 참조 OS 검증이 남아 있다.
Phase 9–12와 기존 remote WSS/OAuth/실제 host/참조 OS gates도 계속 사용자 목표에 포함한다.


## 비관리자 Service/로그온 등록 후속 구현

WTS user token에 의존하는 launch 대신 사용자 로그온 launcher와 OS identity/nonce 기반 등록을
연결했다. `racp-login-broker` worker를 Agent Job에 편입하고 실제 peer kernel handle을 pin한다.
launcher는 Job 밖에서 새 worker를 시작하여 Agent 정리 뒤 새 PID/pairing으로 복구한다.
WTSINFOEX 잠금 상태와 로그온 사용자 변화 관측, 비관리자 SCM host/config/STOP 코드를 추가했다.
계약과 제한은 [ADR-0011](adr/ADR-0011-nonadmin-agent-and-user-logon-broker.md)에 있다.

`test_broker_login.py`의 실제 같은 계정·별도 프로세스/pipe/Job/Agent wire/재시작 3개와 등록 거부 1개,
`test_broker_logon_state.py`, `test_agent_service_host.py` 경계 주입 4개를 통과했다.
로그온 worker가 Guardian을 실행하고 Agent가 고정된 parent/controller/Job 범위를 확인하여 발견한다.
실제 등록 시험에서 Guardian 연결·정리와 임시 config 디렉터리 제거도 확인했다.
실제 SCM·다른 OS 계정과 자동 시작 설치는 수행하지 않았다. WTS 조회에서는 active/unlocked를
관측했다. 후속 실제 GUI 시험은 아래의 자체 임시 창에 한정한다.

## UI Automation와 입력 hook 후속 검증

`desktop.inspect/invoke/set_value`와 원래 COM ref를 보관하는 5초 관측 경계를 추가했다.
독립적인 low-level input/WinEvent pump, 외부 주입·foreground/창 재사용 감지와 OS 세션 mutex를 연결했다.
구체적 계약과 한계는 [ADR-0012](adr/ADR-0012-ui-automation-and-input-watch.md)에 있다.

일반 gate에는 입력 도중 변경/foreground 왕복 2개, input/창 generation 2개,
UIA PID 재사용·geometry·disabled/password·detached·다른 scope/integrity/TTL와 chord 중복 11개를 추가했다.
전체 재검사에서 Browser upload close profile 잔존 시험 1회가 실패했으며, 단독 재검사와 다음 전체
재검사는 통과했다. 해당 close 결과의 cleanup_status를 먼저 확인하도록 시험 진단을 보강했다.
이 관측을 전체 성능/soak gate 통과로 간주하지 않는다.

`RACP_TEST_GUI=1 uv run pytest -q tests/integration/test_desktop_native_gui.py`는 자체 임시 Win32 창만
입력 대상으로 사용한다. 초기 UIA/input 시험 4개와 아래 Guardian 후속 시험을 합친 최신 결과는
앞서 **11 passed**였다. 최신 회귀의 통과/skip과 report는 위에 기록한다.

- 실제 한글/emoji 입력, Ctrl+A/extended Delete, 정상 drag 종료 후 버튼/key 해제.
- 자체 창 visible rectangle 원본/preview 캡처와 실제 physical bounds 일치.
- 현재 실제 display는 3840×2160 단일 monitor, 원점 (0,0), scale 1.5다. 창은 720×450 physical pixel이었다.
- 자체 marker 입력을 사용자 입력에서 제외하고, 다른 marker의 Shift down/up 주입은 lease를 해제하여 후속 입력을 거부.
- 실제 UIA Value/Invoke의 자체 Edit/Button 내용·동작 확인, ref 재사용과 password mutation 거부.
- 별도 실제 Broker/Agent/HTTP와 MCP SDK client의 UIA tree/ref 회수 및 read_only Invoke 정책 거부.
- 실제 창 destroy hook과 별도 프로세스 간 session mutex 경쟁/해제 뒤 재획득.

초기 OS foreground 전환 거부는 skip으로 기록했고, 이후 합법적인 자체 창 show/activation 경로에서
시험을 실행했다. Mouse dwExtraInfo 상위 비트 불일치를 실제 hook counter로 발견해 양수 31-bit marker로
수정했다. zero-distance mouse 이동은 hook event가 생기지 않아 외부 주입 시험은 Shift pair로 검증한다.
UIA focus의 자체 provider 재진입 시험에서 native RPC 진단이 발생한 경로는 제거했다.
자동 foreground 우회나 unlock/elevation은 구현하지 않았다.

이 자체 창 결과는 Notepad/Calculator, 100/125% DPI, 음수 원점 2 monitor, 실제 물리 사용자 입력,
UAC/locked/RDP와 Windows 11/Ubuntu gate를 대체하지 않는다.
Drag 내부 정상 종료/예외 해제와 강제 종료 후속 검증은 아래에 기록한다.
Guardian이 준비된 세션에서 공개 Provider의 drag를 제공하며 해당 세션에서 실제 입력을 검증한다.

## 독립 입력 해제 Guardian 후속

foreground와 로그온 Broker의 입력은 별도 Guardian을 연결하고 own input receipt와 release gate를 사용한다.
임시 current-user task fallback과 Broker의 실제 PID/birth/Job 독립성을 확인했다.
같은 실제 자체 창에서 Ctrl/left button을 누른 상태의 Broker Job termination, 3초 hard hold timeout,
RPC 취소에서 실제 입력 해제, Guardian native exit code 0, 새 session lease를 확인했다.
실제 별도 Agent process crash에서도 입력 해제·exit code 0·임시 task/config 제거를 확인했다.
실제 Agent/Gateway HTTP operation 취소와 Device revoke 뒤에는 입력 해제·release gate·Broker/Guardian
정리 완료를 확인했다. operation 취소 결과는 CANCELLED와 cleanup_status=complete였다.
GUI opt-in의 앞선 결과는 **11 passed**였으며 ledger/identity/정리 ownership 경계 4개도 추가했다.
입력을 보내지 않는 실제 Agent crash/task 정리 시험은 일반 전체 검사에도 포함한다.

공개 capability는 Guardian이 준비된 세션에 한해 drag를 광고한다. 자체 창의 직전 physical 관측을
사용한 HTTP 80ms drag가 SUCCEEDED/os_dispatch_only를 반환하고 left button과 release gate를
정리한 것을 확인했다. 앱 내용의 의미상 변경을 보장한다고 표시하지 않는다.
Guardian 자체 finally가 task/config를 제거하여 Agent crash 뒤에도 정리된다. 정확한 private config의
ownership을 대조하고 link/다른 root를 거부하며, 단일 task/file과 빈 디렉터리만 제거한다.
같은 계정의 실제 logon 등록 경로도 이 Guardian을 연결한다.
구체적인 계약과 SCM/다른 계정·준비 전 orphan recovery 잔여 범위는
[ADR-0013](adr/ADR-0013-independent-input-release-guardian.md)에 있다.

Guardian 준비 receipt 유실/health 거부 때 task가 실제로 중지된 뒤 config를 제거하도록 보강했다.
native process handle 종료와 파일 제거의 실제 시험 2개, 중지 확인 실패 시 private config 보존 경계
1개를 통과했다. 새 Broker에 붙이거나 input을 arm하기 전의 task에만 강제 중지를 적용한다.
등록 직후 Agent crash로 실행되지 않은 task의 orphan recovery까지 완료한 결과는 아니다.
