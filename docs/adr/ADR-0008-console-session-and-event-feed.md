# ADR-0008 — Console 인증과 realtime 관측

상태: 개발 구현 · 2026-10-02

Console은 React/TypeScript/Vite, TanStack Query, Zod를 사용한다. production build를
Gateway의 `/console/`에서 같은 origin으로 제공한다. Python workspace에서 Console을
제외하고 pnpm workspace/lockfile로 별도 관리한다. OpenAPI는 실제 FastAPI application과
transport model에서 생성하고 TypeScript client drift와 runtime Zod 검증을 함께 확인한다.

owner bearer를 가진 CLI가 5분짜리 one-use setup secret을 발급한다. Console은 이를
HttpOnly/SameSite=Strict cookie로 교환한다. secret은 교환 후 입력에서 지우며 browser
storage에 저장하지 않는다. cookie에는 owner token을 넣지 않고, DB에는 session digest만
저장한다. HTTPS cookie는 Secure이며 loopback 개발 HTTP만 예외다. CSRF는 session별 token과
동일 Origin을 모두 확인한다. Cookie session으로 MCP 인증을 대신할 수 없다.

세션은 monotonic 기준 유휴 30분·절대 12시간이다. GET/SSE의 background 조회는 유휴 기간을
연장하지 않는다. 사용자 입력 활동과 상태 변경만 갱신한다. clock identity 불일치는 세션을
무효화한다. 로그인/logout/설정 token 발급은 안전한 audit를 남긴다. 로그인과 인증 응답은
no-store, Console에는 CSP, nosniff, referrer policy를 적용한다.

SSE `/events`와 `/api/v1/events`는 durable sequence와 stream identity를 사용한다.
Last-Event-ID replay는 10분/1만 건 중 먼저 도달한 제한까지 제공한다. cursor가 범위를
벗어나면 refresh event를 보내 GET snapshot을 다시 조회하게 한다. 이벤트에는 resource
종류와 Device/Operation ID만 있고 command output, request payload, credential은 없다.
owner별 replay를 분리하고 rollback된 이벤트는 보이지 않는다. stream은 owner당 16개,
전체 32개로 제한하며 응답 header 전송 실패·정상 종료·단절에서 슬롯을 반환한다.

Console은 연결 단절 또는 브라우저 offline에서 ONLINE을 최신 사실처럼 표시하지 않는다.
EventSource 재접속 후 query cache를 무효화한다. Job의 취소 접수와 종료 완료, UNKNOWN의
상태 조사, 결과/승인 만료를 구분한다. 승인과 폐기/취소 확인에는 stable Device ID,
실행 계정·boot/session·대상을 표시한다. 승인을 실행할 때 원래 operation/key/digest와
동일한 요청을 기존 journal 경로로 보낸다. 새로운 operation을 임의 생성하지 않는다.

Terminal diagnostic viewer는 문자열을 React text로만 렌더링하며 ESC/C0를 표기 문자열로
바꾼다. clipboard/URL open이나 HTML 실행 기능을 연결하지 않는다. terminal UI는
Handle/cursor 기반 HTTP 읽기와 지속 WS 구독을 제공한다. cookie WS는 Origin을 확인하고
전송/ACK 및 유휴 1초 tick에서 세션을 다시 검사한다. logout은 구독을 종료한다.
React DOM 반영 후 누적 ACK하며, 최근 64 KiB 문자만 표시한다. gap/재구독은 사용자
cursor 선택을 요구하고 구독 중지는 PTY를 종료하지 않는다.

## Windows loop 결정

Windows CPython 3.12.11에서 SSE 연결 종료 시 `_call_connection_lost`의 socket shutdown
WinError 10054가 server detach를 막아 종료가 지연되는 실제 시험 실패를 재현했다.
Gateway는 네트워크 I/O 전용 Selector loop를 사용하고 Agent는 독립 process의 Proactor를
유지한다. CPython 내부 함수를 global patch하지 않는다. 관련 원본은
[CPython Proactor transport](https://github.com/python/cpython/blob/v3.12.11/Lib/asyncio/proactor_events.py)에 있다.
Selector의 Windows socket 수 제한은 고부하/reference OS gate에서 다시 확인해야 한다.

## 남은 gate

Windows 10 실제 Chromium에서 prototype을 검증했다. Windows 11/Ubuntu, Firefox/WebKit,
실제 TLS ingress/OAuth, 장시간 세션·SSE 부하 및 전체 accessibility audit는 미검증이다.
상세 doctor/session 관리 일부는 안전한 JSON diagnostic이다. wheel에 Console assets를 묶는 release packaging은 Phase 11 범위다.

## 목록과 안전한 preview

목록은 owner/filter/page size/revision을 HMAC cursor에 묶고 rowid 역순 keyset으로
조회한다. TTL은 5분이며 best-effort consistency와 최대 500건을 명시한다. 오래된
cursor/SSE gap은 첫 페이지를 다시 조회한다. audit도 owner별로 제한한다.

Artifact는 attachment 다운로드를 유지한다. 텍스트는 최대 64 KiB, 이미지는
PNG/JPEG 20 MiB까지 magic/전체 SHA-256 검증 후 Blob img에만 표시한다. HTML/SVG는
실행하지 않는다. SSE keepalive는 15초 watchdog을 갱신하며 데이터 조회를 유발하지 않는다.
