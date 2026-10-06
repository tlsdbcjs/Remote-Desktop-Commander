# Phase 7 Browser 기반 구현 결과

2026-10-02 KST · Windows 10 Pro x64 · Python Playwright 1.63.0
Chromium revision 1243 / 153.0.8010.12 · 부분 구현

browser.open/pages/new_page/navigate/snapshot/screenshot/click/type/key/evaluate/
close_page/close/keepalive/download/upload/frames/attach/cdp_targets를 실제 Provider로 연결했다. HTTP·인증된 CLI·MCP는 같은
정책·journal·Job·Handle 경로를 사용한다. 설계는
[ADR-0009](adr/ADR-0009-contained-browser-and-observations.md)에 있다.

## 실제 검증

`tests/integration/test_browser.py`의 실제 Chromium 시험 7개와 contract 시험 2개를 통과했다.

- 실제 wire로 CREATING Handle 관측, 초기 응답 전 후속 요청 거부.
- 2개 context·3개 page의 명시적 대상과 localStorage 격리/공유 범위.
- open key 재사용 시 같은 browser 반환, 추가 실행 없음.
- 테스트 폼 snapshot → ref type → 구조 selector click → 결과 DOM 확인.
- 새 snapshot/navigation 이후 stale ref 거부, 다른 context page 거부, 다중 selector 거부.
- viewport screenshot Artifact의 PNG signature/전체 SHA-256 검증.
- 실제 Gateway 재시작 후 browser/page Handle 복구, Agent boot 변경 후 이전 Handle 거부.
- 무한 evaluate 500ms budget 초과 후 실제 worker/driver/Chromium 자식 종료와 새 context 생성.
- Agent 종료가 Browser cleanup과 겹쳐도 watchdog 종료와 자식 정리가 끝남.
- Job 취소 후 CANCELLED와 자식 종료, lease 만료 후 EXPIRED/종료.
- read_only evaluate 거부, standard 승인 요구, 64 KiB 초과 evaluate 결과 거부.
- 매우 큰 Unicode semantic DOM의 truncated snapshot과 inline 크기 상한.
- 기본 dialog dismiss event, network allowlist 및 외부 redirect 거부.
- CLI open/type/key/close와 실제 인증 MCP navigate/snapshot 연결.

TTL/lease는 monotonic 경계 주입이며 실제 1시간/60초 대기 시험으로 표시하지 않는다.
무한 evaluate의 500ms 실행 제한과 OS 자식 종료는 실제 수행했다. 각 시험의 workspace와
browser context는 임시 fixture이며 개인 profile을 사용하지 않는다.

전체 Python 품질 gate와 최신 source 수는 [구현 상태](implementation-status.md)에 기록한다.
Ruff format/lint와 Windows/Linux 타입 검사를 수행했다. Linux 타입 target은 실제
Ubuntu 실행 검증을 대신하지 않는다. 결과는 `dist/test-results.xml`에 있다. Console Chromium E2E 10개와 production
build도 통과했고, worker를 포함한 개발 wheel 8개와 SHA-256 manifest를 확인했다.

## 파일 전송과 수명 관리의 추가 검증

`test_browser_files.py`의 실제 Chromium 시험 5개와 `test_browser_workspace.py` 2개를 통과했다.

- HttpOnly cookie가 필요한 4 MiB 초과 파일의 native download와 SHA-256/Artifact 원본 일치.
- page-owned Blob 다운로드, same key replay 후 서버 download counter=1.
- 추가 Browser API 호출 없이 명령 대기 중 unsolicited download 취소.
- Artifact 파일을 한글 basename으로 설정한 후 페이지 fetch의 실제 수신 byte/해시 확인.
- 지연 File 읽기까지 private staging 유지, context 종료 후 profile/download/upload 디렉터리 제거.
- 작은 max_bytes 초과 시 FAILED/RESOURCE_EXHAUSTED, 원격 side effect는 unknown으로 표시,
  소유 browser 종료/임시 파일 정리 및 부분 Artifact 미게시.
- Artifact 게시 실패 후 Agent 재시작으로 첨부 회수, 원래 결과 불변과 재클릭 counter=1.
- Windows controller를 실제 kill한 뒤 Job 소유 자식 전부 종료와 새 실행의 잔여 directory GC.
- protected DACL, PID/creation-time ownership, live/unverified 파일 보존,
  nested junction의 외부 파일 보존.

이 gate는 전체 디스크에 OS hard quota를 설정한 시험이 아니다. 50ms monitor는 순간 초과가
가능하며 물리 disk-full/OS별 부하와 장기 TTL 대기는 아직 미검증이다.

## Frame와 외부 CDP의 추가 검증

`test_browser_frames.py` 2개, `test_browser_cdp.py` 6개와 추가 endpoint contract를 통과했다.

- same-origin/cross-origin frame의 명시적 ID/관측/ref와 폼 입력/결과 DOM, key focus 격리.
- frame navigation/detach 뒤 stale 거부, 다른 page의 frame/cursor 거부와 frame pagination.
- default CDP 비활성화/operation 미advertise, remote host/query/credential URL 거부.
- 실제 별도 persistent Chromium의 loopback CDP에 연결, 기존 profile을 자동 선택하지 않음.
- 새 isolated context의 무한 evaluate timeout 정리와 external browser/다른 page 보존.
- 명시적으로 선택한 borrowed page의 입력과 detach, RACP 새 tab만 정리.
- allow_page_termination을 준 page의 무한 evaluate 종료, 선택하지 않은 page 보존.
- controller 실제 kill 후 persistent remote cleanup 범위만 회수하고 backing directory 제거.
- CDP isolated context의 cookie download와 private Artifact 원본 byte 일치.

시험의 external browser도 임시 profile이며 실제 개인 Chrome/Edge를 조작하지 않았다.
borrowed 기존 context의 download는 지원하지 않는다. evaluate/upload는 종료 권한이 필요하고,
새 isolated context에서는 이 기능들을 사용할 수 있다.

## 비동기 이벤트와 health 추가 검증

`test_browser_events.py` 실제 연결 시험 2개와 `test_browser_event_contract.py` 2개를 통과했다.

- Browser poll 없이 timer dialog/navigation/popup close 상태를 Gateway Handle/audit로 관측.
- URL·DOM·dialog 본문을 audit/event로 전송하지 않음.
- Gateway restart 뒤 ACK replay의 durable sequence 중복 제거.
- owner/boot/provider scope 검증, Handle·cursor·audit transaction 실패 시 전체 rollback.
- bounded outbox overflow와 owner SSE full_refresh, 최신 health 상태 유지.
- Chromium cache가 없는 상태에서 browser.open 비광고/Doctor degraded, Device는 ONLINE이며
  filesystem 작업은 계속 실행됨.

health probe는 pinned driver/Chromium 파일과 platform 지원 검사다. 정상 실행/DLL 호환성을
미리 검증한 것으로 표시하지 않으며 실제 실행은 browser.open에서 검증한다. Agent event
outbox는 RAM이고 Agent crash 뒤 이전 boot의 이벤트를 영속 재생하지 않는다.

## 남은 범위

runtime launch/clean-install health 확대, OS hard disk quota/성능·soak,
강제 Agent crash/OS별 containment 확대,
참조 Windows 11/Ubuntu와 실제 host 검증이 남았다.
Phase 7 전체 gate 또는 v1 전체 완료를 선언하지 않는다. Phase 8–12도 계속 남아 있다.
