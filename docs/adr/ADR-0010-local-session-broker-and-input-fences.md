# ADR-0010 — 로컬 Session Broker와 입력 관측 경계

상태: Phase 8 부분 개발 구현 · 2026-10-03

Broker는 Agent wheel의 별도 `racp-session-broker` entry point다. 같은 Python 패키지를
사용해도 프로세스는 분리하며, GUI는 Broker 자신의 interactive session에서만 수행한다.
desktop canonical registry/CLI/HTTP/MCP와 같은 정책·journal 경로에 연결했다. Agent의
--desktop-session-id로 최대 4개 명시적 세션을 설정한다. 현재 foreground Agent는 자신의
OS 계정/세션만 시작하며 다른 사용자 세션의 Service/WTS token launch는 아직 미구현이다.
Windows Service와 자동 로그온/로그오프 수명 관리까지 완료된 것으로 표시하지 않는다.

## 로컬 연결과 identity

Named Pipe는 `\\.\pipe\LOCAL\racp-session-<pair_id>`이며 REJECT_REMOTE_CLIENTS,
FIRST_PIPE_INSTANCE와 단일 instance를 사용한다. DACL은 명시한 Broker 사용자 SID와
Agent SID만 허용한다. Windows Service pairing에는 개별 service SID를 지정할 수 있고
Agent token의 enabled service group 소속도 검사한다. All Services SID는 허용하지 않는다.
foreground 개발 pairing은 실제 Agent 계정 SID를 사용한다. 실제 SCM service SID gate는 남았다.

커널에서 pipe peer PID를 읽고 열린 process handle로 생성 시각·TokenUser·TokenSessionId를
검사한다. server는 OS pipe client를 짧게 impersonate하여 token이 process identity와 같은지
확인한 뒤 즉시 revert한다. 입력 작업은 impersonation 상태에서 실행하지 않으며 Agent가
보낸 임의 token으로 impersonation하지 않는다. mutual nonce HMAC는 pair/version/role에
결합하고 매 연결마다 새 challenge를 사용한다. owner/Device 토큰은 pairing에 들어가지 않는다.

pairing secret은 두 SID만의 protected DACL 디렉터리에 저장한다. 한 번의 연결에 한 요청을
보내며 자동 mutation 재전송을 하지 않는다. 메시지는 UTF-8 JSON 64 KiB와 depth 32 한도다.
overlapped I/O는 deadline과 동일 thread의 cancellation을 사용한다. 응답 소비 receipt까지
연결을 유지하여 DisconnectNamedPipe가 unread result를 버리지 않게 한다.
pipe peer가 종료하거나 PID 생성 시각이 달라지면 Broker를 종료한다.

기술 근거는 Microsoft의 [pipe 보안](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights),
[peer PID](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeclientprocessid),
[SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)이다.

## 입력 lease와 관측

GUI allowlist만 수락하며 shell/evaluate 또는 임의 command 실행은 없다. 모든 operation은
명시적인 session_id를 요구한다. exclusive lease는 owner/device/session에 묶이고 기본·상한
15초다. 관측은 5초 TTL, 최대 32건이고 layout revision, opaque window ID, PID/birth time,
창 bounds/DPI에 묶인다. foreground/user input tick/창/배치 변경 또는 만료 시 lease를 중단하고
새 관측을 요구한다. 다른 owner/Device는 해당 lease를 갱신하거나 입력할 수 없다.

Win32 backend는 per-monitor-v2 thread DPI awareness와 virtual physical pixel 좌표를 사용한다.
monitor마다 origin·width/height·scale_x/scale_y를 반환한다. 음수 좌표를 허용하고 monitor ID는
해당 layout revision 범위에서만 유효하다. 클릭 좌표가 다른 창에 가려져 있으면 거부한다.
elevated target은 integrity mismatch로 거부한다. Unicode/chord/mouse 입력은 단일 SendInput
batch로 dispatch하고 exception/종료 경로에서 자신이 누른 키/button만 해제한다.
다른 request까지 눌린 상태를 유지하는 API는 없다. 결과는 OS dispatch 확인이며 실제 앱의
내용 변경 성공을 뜻하지 않는다.

사용자 입력과 자체 주입은 [ADR-0012](ADR-0012-ui-automation-and-input-watch.md)의 hook으로 구분한다.
감지 한계 때문에 detection_complete는 false다. drag는 독립 입력 해제 Guardian이 준비된 세션에서만
제공한다. 정상 HTTP 실행과 Job kill/Agent crash/cancel/revoke 후 입력 해제는 ADR-0013에 기록한다.
clipboard를 사용하지 않는다. UI Automation 관측과 Invoke/Value pattern 경로를 추가했다.
WTSINFOEX 잠금/secure desktop 분류 코드는 ADR-0011에 있으며 실제 상태 전환 인수시험은 남아 있다.
현재 WTS inactive/disconnected 상태는 입력 전에 거부한다. Console에는 지정 세션의
로그온 계정과 현재 입력 가능 여부를 표시한다.

## 검증 범위

실제 Named Pipe 상호 인증/ACL/잘못된 PID/유휴 timeout/크기 한도와 별도 Broker 프로세스를
Windows 10에서 검증했다. lease·관측 변경/만료·잘못된 owner/device/session·예외 해제는
경계 주입 backend 테스트다. 실제 실행 세션은 WTS inactive로 보고되어 SESSION_UNAVAILABLE
거부를 확인했다. Notepad/Calculator 입력, 서비스 계정, 실제 다중 DPI/음수 origin,
잠금/UAC/RDP 변화는 검증 완료로 표시하지 않는다.


## 감독 프로세스와 화면 전송

foreground Broker는 base Python의 stdin gate를 Windows Job에 할당한 뒤 실행한다.
Broker/venv redirector가 assignment 전에 자식을 시작하지 않으며 KILL_ON_JOB_CLOSE를
사용한다. gate/자식의 console은 CREATE_NO_WINDOW로 숨긴다. 실행 환경은
허용한 OS 변수만 전달하고 owner/Device credential은 전달하지 않는다.
Broker RPC는 session별 직렬화하고 timeout/cancel은 native thread 응답을 포기한 채 결과를
확정하지 않는다. abort/Job 종료 후 accounting과 미리 연 자식 kernel handle의 종료 신호를
확인한다. 실패 restart는 0.5초부터 최대 30초 backoff다. Broker PID/자식은 일반 terminate에서
보호한다. 별도 health task가 현재 상태를 검사하고 heartbeat에서 scope 검증된 capability를
갱신한다. desktop 비활성화는 core Device ONLINE을 바꾸지 않는다.

캡처는 native top-down 32-bit DIB/BitBlt로 visible rectangle을 얻는다. 창 scope에도 가린 창의
픽셀이 들어갈 수 있음을 capture_scope로 표시한다. 원본은 16 Mi pixel/32 MiB PNG 상한이고
preview는 긴 변 1600px/2 MiB다. PNG 원본과 Pillow 12.3.0 LANCZOS preview는 같은 captured
pixel을 사용한다. metadata에는 crop origin과 x/y별 physical-to-preview 변환을 기록한다.
기술 근거는 [BitBlt](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-bitblt),
[CreateDIBSection](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-createdibsection),
[Pillow Image](https://pillow.readthedocs.io/en/stable/reference/Image.html)다.

Broker의 캡처 byte는 파일 경로가 아닌 RAM buffer에 보관한다. 4개/총 64 MiB, 고정 30초 TTL,
owner/device/operation scope를 적용하고 32 KiB base64 chunk로 전송한다. 읽기로 TTL을 연장하지
않는다. Agent는 offset/EOF/PNG magic/최종 SHA-256/fsync/atomic rename을 검증하고 원본·preview를
각각 기존 output spool과 Artifact로 게시한다. 게시 실패 시 같은 operation의 첨부 재전송
경로를 사용한다. MCP는 같은 operation·owner에 속한 2 MiB/1600px 이하 PNG만 image content로
읽으며 Artifact GC reader를 pin하고 byte/hash를 재검사한다. public URL을 만들지 않는다.

실제 활성 세션의 visible-screen 캡처/Notepad 입력과 reference OS, 전체 API 800ms 캡처 gate는
아직 검증하지 않았다. 1920×1080 고엔트로피 synthetic byte의 인코딩만 약 460ms로 측정했다.
이 측정은 GUI·IPC·Artifact 저장을 포함한 end-to-end 성능 결과가 아니다.
