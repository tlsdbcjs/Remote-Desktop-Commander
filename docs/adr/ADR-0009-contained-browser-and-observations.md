# ADR-0009 — 격리 Browser worker와 관측 참조

상태: 부분 개발 구현 · 2026-10-02

Browser Provider는 Python Playwright 1.63.0을 사용한다. browser Handle 하나마다
독립 worker·Chromium process·ephemeral context를 만든다. 사용자 기존 profile/CDP에
attach하지 않는다. browser sandbox를 켜고 headless를 기본으로 한다. 사용자 요청에는
임의 launch argv, executable, profile 경로, host function을 노출하지 않는다.
Playwright interface는 [공식 BrowserType](https://playwright.dev/python/docs/api/class-browsertype),
[Locator](https://playwright.dev/python/docs/api/class-locator)에서 확인했다.

Windows venv redirector가 Job assignment 전에 자식을 만들 수 있으므로 base Python의
stdlib-only worker gate를 사용한다. 부모가 Job Object를 할당한 뒤 stdin config를 보내야
Playwright import/driver/browser 실행이 시작된다. KILL_ON_JOB_CLOSE로 Agent 사망 시에도
자식을 종료한다. POSIX는 새 process group을 사용하며 실제 Ubuntu 실행 검증은 남았다.
worker/browser에는 Agent credential 환경을 전달하지 않는다.

동일 browser의 모든 operation을 직렬화한다. 요청 budget은 lock 대기를 포함한다.
evaluate가 무한 루프에 빠지거나 작업이 취소되면 소유 worker와 browser context 전체를
종료한다. Windows에서는 Job의 ActiveProcesses와 미리 연 kernel process handle의
종료 신호를 확인한다. provider/Agent의 중복 cancel도 정리를 완료하기 전에 terminal
결과를 기록하게 만들지 않는다. Agent 종료는 stopping flag를 먼저 설정하여 cleanup 중 cancel을 처리한 watchdog도
종료한다. 확인 grace는 최대 5초이며 초과는 cleanup_status unknown이다.

context는 최대 4개, context당 page history는 8개, browser history는 12개다.
Handle idle TTL은 1시간이며 pages/snapshot/screenshot 조회는 연장하지 않는다.
mutation과 explicit keepalive만 갱신한다. lease 만료는 TTL보다 우선한다.
Gateway inventory는 활성 Handle을 먼저 담고 전체 128개로 제한한다.

모든 page operation에는 browser_id/page_id가 필요하다. snapshot은 URL/title/frames,
semantic ARIA tree, interactive elements, focus/viewport, observation_id/navigation_revision을
반환한다. UTF-8 데이터는 48 KiB 이내로 잘라 truncated를 표시한다. element ref는 관측 시
실제 DOM handle에 바인딩하며 제거된 element를 새 element로 자동 대체하지 않는다.
새 snapshot/main-frame navigation 후 이전 관측을 재사용하면 STALE_OBSERVATION이다.
구조 selector는 exact role/name 또는 test-id이며 다중 일치는 AMBIGUOUS_TARGET이다.

evaluate는 browser.evaluate 고권한 mutation이다. read_only는 거부하고 standard는
일회 승인을 요구한다. trusted_personal은 Gateway와 Agent의 명시적 opt-in을 따른다.
JavaScript 결과는 JSON 64 KiB 상한이다. 스크린샷은 viewport PNG, 32 MiB 상한이며 기존
출력 spool/Artifact 복구 경로를 사용한다. 페이지 데이터는 untrusted_page_data로 표시한다.

기본 page network는 credential 없는 HTTP(S)다. 실행 계정이 접근 가능한 내부망을 포함한다.
관리자는 Agent --browser-allow-origin으로 exact origin allowlist를 설정할 수 있다.
navigation·redirect/subresource·page WebSocket에 적용하며 service worker는 차단한다.
Gateway는 결과 URL을 fetch하지 않는다. Chromium 자체의 background 통신을 OS firewall로
제한하는 gate는 아직 없으며 이 설정을 완전한 OS network isolation으로 부르지 않는다.

dialog는 dismiss하며 내용 없이 type/state를 제한된 event 목록에 남긴다. worker는
bounded stdin reader와 Playwright event pump를 분리해 명령 대기 중에도 dialog,
route, download callback을 처리한다. context당 동시에 하나의 명시적 download만
허용하고 다른 page/추가 download는 cancel/delete한다.

## Artifact 파일과 임시 디렉터리

browser.download는 관측에 묶인 element를 실제 클릭하고 native Playwright Download를
사용한다. 기존 context의 cookie/Blob 동작을 유지하고 Gateway는 URL을 fetch하지 않는다.
임시 완료 파일을 최대 4 MiB chunk로 읽어 size/SHA-256/fsync를 확인하고 생성된 operation
spool 이름에 저장한다. server suggested filename은 데이터이며 로컬 경로로 사용하지 않는다.
게시 파일의 기본·상한은 1 GiB이고 작은 max_bytes를 요청할 수 있다. 원본 결과/출력 첨부는
기존 durable output spool을 사용하므로 Artifact 전송 실패·Agent 재시작 후 재클릭 없이 회수한다.

browser.upload는 정확히 operation에 할당된 owner Artifact만 materialize한다. Gateway의
ready/queue 재검증, Agent full hash/size 검증, worker의 독립 복사 검증을 거친다.
임의 로컬 경로를 받지 않고 Windows device name/ADS/경로 문자가 없는 basename을 요구한다.
Chromium File의 지연 읽기를 지원하기 위해 private staging 파일을 browser 수명 동안
보관한다. 최대 16개이며 context 종료/TTL/lease 때 삭제한다. upload는 파일 input 설정이며
서버 제출 여부는 이후 DOM에서 확인한다.

새 browser 파일은 Agent data/browsers/browser_UUID 내부에서만 만든다. Windows root는
현재 계정과 SYSTEM만의 protected inheritable DACL, POSIX는 0700이다. browser/driver TEMP,
downloads, uploads를 이 경로에 두며 default 전역 TEMP의 다른 Playwright 파일을 회수하지 않는다.
종료 확인 후 절대 경로와 root 바로 아래 UUID scope를 검사하고 제거한다. nested junction의
외부 내용은 따라가지 않는다. owner/worker PID와 creation time을 기록하고, 재시작 시 두
process가 모두 사라진 directory만 GC한다. live/unverified ownership은 보존한다.

Agent spool 예약은 download에 native+출력 최대 2 GiB, upload에 input+staging 약 2 GiB를
포함한다. browser staging 사용량도 spool admission에 반영한다. 독립 monitor가 50ms마다
native download와 합계 10 GiB 사용량을 확인하고 초과 시 소유 context를 종료한다.
이 monitor는 polling 사이 순간 초과를 허용하므로 OS의 hard disk quota라고 부르지 않는다.
Artifact 게시 size 한도는 검사 후 적용된다. 물리 disk-full/OS quota와 성능/soak gate는 남았다.

참조 OS와 실제 host는 후속 범위다. 지속 이벤트와 health의 현재 계약은 아래에 기록한다.

## Frame 관측과 CDP opt-in

snapshot은 stable frame_id/parent_frame_id와 page_url을 제공한다. frame_id를 생략하면
main frame이고, 지정하면 해당 frame의 semantic DOM과 element ref를 반환한다.
observation_id는 page/frame 조합에 묶이며 frame attach/navigation/detach 때 page의
navigation_revision을 증가시켜 이전 관측을 거부한다. frame 목록은 기본 16개/최대 64개
cursor page로 조회하고, 최대 active 128/history 512개로 제한한다. 키 입력의 focus가
다른 frame이면 FOCUS_MISMATCH다. 내부 Playwright API 사용은
[공식 Frame API](https://playwright.dev/python/docs/api/class-frame)를 확인했다.

CDP는 Agent --enable-browser-cdp로 opt-in해야 operation을 advertise한다. attach와
cdp_targets는 high permission mutation 경로/승인·journal을 사용한다. endpoint는 query,
credential 없는 literal loopback HTTP(S)/WS(S)이며 localhost는 127.0.0.1로 고정한다.
HTTP discovery는 redirect/env proxy 없이 64 KiB까지 읽고 WS의 host/port/scheme이 바뀌면
거부한다. Gateway는 endpoint를 fetch하지 않는다.

attach 기본은 외부 browser 안의 RACP 전용 isolated context다. browser 프로세스는
외부 소유로 표시하고, 생성한 context/page만 종료한다. 기존 profile을 원하면 context_mode
existing과 정확한 page_target_ids를 명시해야 한다. no_defaults=True로 기존 default
context의 viewport/media/focus/download 설정을 덮어쓰지 않는다. 관련 interface는
[공식 connect_over_cdp](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)다.

borrowed page의 기본 close/lease cleanup은 관리 연결을 detach하고 원래 page를 유지한다.
그 page의 evaluate/upload는 allow_page_termination=True일 때만 허용한다. 이는 작업
timeout/cancel/관리 종료 시 해당 선택 page를 닫는 권한이며 브라우저 프로세스와 다른 page를
닫는 권한을 주지 않는다. borrowed context에서 만든 새 tab은 RACP 소유이며 cleanup 대상이다.
기존 default context의 download 설정 변경을 피하기 위해 그 context의 download는
OPERATION_NOT_SUPPORTED다. CDP isolated context의 private Artifact download는 검증했다.
borrowed profile의 기존 service worker/network는 OS 수준 격리를 보장하지 않는다.

worker와 독립적인 CDP peer가 허가한 target/context만 dispose/close하고 확인한다.
외부 browser 전체 kill은 하지 않는다. context는 pinned Playwright의 disposeOnDetach를
사용하며, 기존 default context의 새 tab은 생성 intent의 nonce URL을 먼저 fsync하고
생성 후 target ID를 기록한다. remote cleanup 범위는 private ownership marker에 저장한다.
Agent 재시작 때 죽은 owner/worker의 remote scope를 확인한 뒤 backing 파일을 회수하며,
확인되지 않은 파일은 보존한다. Protocol interface는
[Chrome Target domain](https://chromedevtools.github.io/devtools-protocol/tot/Target/)을 확인했다.
실제 사용자의 Chrome/Edge patch, 모든 crash 경합과 참조 OS gate는 계속 남았다.

## 비동기 이벤트와 기능 상태

worker stdout의 별도 reader는 요청 응답과 navigation/frame/page/dialog/download 이벤트를
분리한다. URL·본문·dialog 문구를 이벤트로 보내지 않고 Handle 상태와 종류만 전달한다.
worker는 50ms 단위로 같은 page/종류의 이벤트를 합쳐 전달한다. Agent metadata outbox는
128건이며 ACK 전 재연결 때 같은 sequence를 다시 보낸다. overflow는 inventory를 포함한
gap 이벤트로 표시한다. 작업 실행 journal과 이벤트 전달은 별개이며 이벤트 재전송이
mutation을 재실행하지 않는다.

Gateway는 Device/Agent boot/provider sequence를 SQLite에 저장하고 Handle 변경·audit와
같은 transaction에서 commit한다. epoch/boot/owner scope를 검사하며 Gateway 재시작 후
중복 이벤트를 무시한다. gap audit는 owner SSE에서 full_refresh 신호를 발생시킨다.
Agent outbox 자체는 메모리 보관이며 Agent crash 뒤 이전 boot 이벤트를 영속 재생하지 않는다.
이 경우 기존 boot Handle 만료와 새 inventory 관측을 사용한다.

10초마다 pinned Playwright driver와 headless Chromium 파일·OS/architecture 지원 여부를
검사하고 변화한 capability를 전송한다. native runtime 누락 시 browser.open을 광고하지
않으며 다른 Device 기능은 ONLINE을 유지한다. 이 검사는 실행 파일 존재 검사다. DLL/OS
호환성이나 정상 launch를 선행 증명하지 않으며 실제 launch 검증은 browser.open에서 한다.
참조 OS/clean installation과 성능·soak는 계속 별도 gate다.
