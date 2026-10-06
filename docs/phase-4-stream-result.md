# Phase 4 WS stream 구현 결과

일자: 2026-10-02 KST · 환경: Windows 10 Pro x64 / Python 3.12.11

owner WebSocket → Gateway relay → Agent PTY ring의 stream_open/data/ack/end를 구현했다.
구독은 owner/device/handle/boot/epoch에 묶이며 Device token, query token, 다른 Origin을
거부한다. chunk는 원본 64 KiB, 미확인 구간은 최대 256 KiB/4 frame이다. 소비 완료한
chunk 경계의 누적 ACK만 처리하고 오래된 ACK는 추가 side effect 없이 무시한다.

느린 소비자는 Agent 전송을 멈추지만 PTY ring 수집은 계속된다. 사라진 cursor는
stream_gap과 CURSOR_EXPIRED 종료를 제공하고 명시적 새 cursor로 재접속한다.
owner disconnect는 구독을 정리하며 terminal Handle은 유지한다. Gateway restart 후
기존 Handle에 소비 cursor부터 재접속할 수 있다. Python SDK는 다음 item 요청에 ACK하고
CLI terminal stream은 NDJSON을 flush한 뒤 ACK한다.

core WS는 control/data 큐를 분리하고 단일 writer가 control을 우선 전송한다. bounded
relay와 구독 상한을 적용하고 EOF의 마지막 ACK를 기다린 뒤 소켓을 닫는다. 실제 CLI
시험에서 마지막 ACK와 EOF 소켓 종료의 경합을 발견하고 수정했다.

실제 Gateway·Agent·PTY 연결에서 확인한 항목:

- owner/Device 인증 구분, 잘못된 Origin 거부.
- 독립 소비자 두 개의 Unicode 출력과 동일 byte cursor 관측.
- Gateway 재시작 후 cursor 재접속에서 이전 출력 중복 없이 REPL 상태 유지.
- 미확인 데이터 1 KiB 제한 시험, ACK 중단 중 heartbeat와 다른 RPC/Job 취소 처리.
- ring overflow gap과 명시적 새 cursor 선택.
- 미래 ACK가 해당 owner stream만 닫고 Agent/terminal을 유지.
- SDK EOF/종료 코드, CLI NDJSON/최종 cursor/secret 비출력.
- opened 응답 전에 20회 연결을 종료해도 구독 슬롯을 회수.

unit 시험은 backlog 중 control 우선·단일 writer, 전송 실패의 pending caller 해제,
다른 연결의 stream fencing, 미전달/중간/다른 stream ACK 거부, byte credit와 누적 ACK를
검증했다. 계약은 [ADR-0005](adr/ADR-0005-terminal-stream-credit.md)에 있다.

최신 전체 검사: **60 passed, 1 skipped**. Ruff format/lint와 strict mypy(Windows/Linux
타입 target의 49개 source file)를 통과했다. 8개 개발 wheel과 SHA-256 manifest를
재생성했다. JUnit은 `dist/test-results.xml`, manifest는 `dist/build-manifest.json`이다.

참조 Windows 11/Ubuntu runner, Console cookie/CSRF/SSE, 실제 MCP host와 8시간 soak는
미검증/후속 범위다. full Job domain/timeout/retention도 Phase 5에서 이어서 구현한다.
로컬 stream 검증을 MVP 또는 전체 v1 완료로 표시하지 않는다.
