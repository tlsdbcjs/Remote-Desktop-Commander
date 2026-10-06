# Phase 4 terminal / Handle 복구 구현 결과

일자: 2026-10-02 KST · 환경: Windows 10 Pro x64 / Python 3.12.11

terminal.open/write/read/resize/close/keepalive를 Agent, registry/policy, HTTP operation,
인증 CLI, MCP에 연결했다. Windows raw ConPTY 및 POSIX PTY backend, 4 MiB raw ring,
독립 byte cursor, UTF-8 경계·invalid replacement 보고, 64 KiB text/raw write, 부분 입력
accepted_bytes, 8시간 idle TTL을 구현했다. 입력과 resize는 TTL을 갱신하며 poll은
갱신하지 않는다. 기본 session 상한 8개와 bounded history를 적용했다.

Gateway는 Handle의 owner/device/boot/provider/revision과 마지막 관측 시각을 저장한다.
reconcile 및 heartbeat inventory로 현재 availability를 갱신하고 과거 revision을
거절한다. 연결 상태는 Handle lifecycle과 독립적이다.

실제 Windows Gateway+Agent 시험에서 다음을 확인했다.

- 지속 Python REPL에서 변수 저장 후 식 계산, 한글/emoji, resize, 독립 cursor.
- PowerShell → cd test-dir → Python REPL → 계산 및 cwd 유지.
- raw base64 입력과 bounded ring overflow의 CURSOR_EXPIRED/lost_bytes.
- write의 동일 idempotency key가 입력을 두 번 보내지 않음.
- Gateway 재시작 후 같은 live Handle과 REPL 변수를 복구.
- Agent 재시작 시 이전 Handle EXPIRED, 이전 open 요청 replay에서 재실행 없음.
- idle TTL 및 local execution lease 만료에서 owned terminal/process tree 정리.
- 반복 close의 동일 결과와 종료 이후 write 거부.
- 실제 CLI terminal open/read/close 및 binary Artifact upload/write, secret 비출력.
- 실제 MCP SDK terminal_open/read/close와 provider registry 연결.
- read budget 만료에서 세션 유지, EOF와 종료 코드 보존(Windows exit 259 포함).

Unicode chunk 경계 및 invalid bytes는 별도 ring unit test에서 확인했다. inventory unit
test는 owner mismatch, 늦은 revision, offline 표시, boot 변경 만료를 확인했다.
Windows redirected parent stdio를 자식이 사용하는 실패를 실제로 발견하고
STARTF_USESTDHANDLES+null handles로 수정했다. 계약과 근거는 [ADR-0004](adr/ADR-0004-persistent-terminal.md).

최신 전체 검사: **51 passed, 1 skipped**. Ruff format/lint, strict mypy(Windows/Linux
타입 target, 43개 source file) 통과. 8개 개발 wheel과 SHA-256 manifest를 재생성했다.
JUnit은 `dist/test-results.xml`, manifest는 `dist/build-manifest.json`에 있다.

이후 WS push/stream_ack 256 KiB backpressure를 구현했다. [추가 검증](phase-4-stream-result.md).
capability 변경 event, full Job domain/retention, Console 진단 화면이 남아 있다.
Ubuntu/Windows 11 reference runner, 실제 MCP host 및 8시간 soak는 UNVERIFIED다.
따라서 Phase 4 전체 gate나 MVP 완료로 표시하지 않는다.
