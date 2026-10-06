# Phase 6 Console 기반 구현 결과

2026-10-02 KST · Windows 10 Pro x64 · Python 3.12.11 · Node 22.23.0

대시보드, Device 목록/상세와 capabilities, Job 조회/취소, 승인 목록/Approve once/Deny와
동일 요청 실행, session 목록/terminal diagnostic read, Artifact/audit/doctor diagnostic,
설정/enrollment token 발급, owner별 keyset pagination/filter, 안전한 Artifact preview와
지속 WS terminal viewer를 구현했다. UI는 기존 인증된 application API를 사용하며
provider나 DB에 직접 접근하지 않는다. 설계는 [ADR-0008](adr/ADR-0008-console-session-and-event-feed.md)에 있다.

Console session은 one-use setup 교환, cookie/CSRF/Origin 검증, monotonic 30분/12시간 만료와
logout을 제공한다. SSE는 Last-Event-ID replay, gap refresh, owner 격리와 bounded 수량을
제공한다. Device heartbeat/상태·작업·승인·Artifact 이벤트로 GET snapshot을 갱신한다.

## 실제 검증

- Cookie 로그인/logout, secret storage 비사용, HttpOnly/SameSite, 다른 세션 CSRF 거부.
- Device credential의 owner/MCP 접근 거부, Origin/CSRF/query token 변조 거부.
- 유휴/절대/one-use 만료 경계의 monotonic clock 주입.
- SSE replay·Gateway 재시작·10분 cutoff와 count gap·owner 격리·rollback·슬롯 반환.
- 실제 별도 Agent process와 Chromium으로 승인된 동일 요청 실행, counter=1 확인.
- Job 취소 후 CANCELLED, Agent 강제 재시작 후 UNKNOWN과 재실행 제안 없음.
- Browser offline/online에서 stale 표시와 복구, Terminal escape 문자열의 URL 실행 없음.
- Keyset 페이지 사이 삽입·filter/cursor scope·만료·다른 owner 접근 거부.
- 실제 SSE gap 후 첫 페이지 재조회, silent stream 15초 stale 감지(가상 clock 주입).
- HTML 문자열은 텍스트 표시, 64 KiB 부분 preview, PNG magic/hash 검증과 원본 attachment.
- 승인 만료 후 실행 버튼 제거, 결과 410 후 재실행 없이 실제 side-effect 부재 확인.
- Cookie WS terminal update/명시적 cursor 재구독, logout 후 quiet stream 서버 슬롯 반환.
- Desktop/mobile 배치, page error 없음, Tab/Enter 로그인, Device online/offline realtime.

HTTPS Secure attribute는 ASGI HTTPS scope에서 검사했고 실제 TLS 배포를 검증한 것은 아니다.
30분/12시간/10분은 경계 주입이며 실제 해당 시간 전체 대기를 수행한 것으로 표시하지 않는다.
브라우저 시험은 isolated fixture이며 개인 Device credential이나 데이터를 사용하지 않는다.
fixture 관리 endpoint는 `scripts/console_e2e.py`에만 있고 production application에는 없다.

품질 산출물: `dist/test-results.xml`, `dist/console-test-results.xml`,
`dist/console-overview.png`, `dist/console-artifacts.png`, `dist/console-build-manifest.json`, `dist/build-manifest.json`.
production client build는 format/type/client drift를 확인하며 pnpm frozen lock install을 통과했다.
Phase 6 기록 시 전체 수치는 Python **101 passed, 1 skipped**, Chromium **10 passed**다.
Windows/Linux 타입 target 61개 source file과 개발 wheel 8개 빌드를 통과했다.
실제 Linux 실행 검증을 대신하지 않는다. [작업 현황](implementation-status.md)에 남은 범위를 기록한다.

## 남은 구현과 검증

상세 doctor/metrics와 session 관리 화면 일부는 JSON diagnostic이며 확대해야 한다.
다른 loading/error 상태와 전체 accessibility의 브라우저 인수 시험도 남아 있다. Node/Chromium 참조 patch는 고정했지만 참조 OS/다른 browser/실제 host/장기 soak는
미검증이며 milestone M2/M3 또는 v1 전체 완료를 선언하지 않는다. Phase 7–12는 별도 범위다.

2026-10-03 후속 검증: 실제 Chromium E2E 11개와 production build를 통과했다.
명시적으로 활성화한 foreground Broker의 세션·로그온 계정·입력 가능 상태 표시를 추가했다.
이 UI 검증은 실제 데스크톱 입력/다중 DPI 인수시험을 대신하지 않는다.

2026-10-04 OAuth 후속 회귀: Node 22 production build/format/type/client drift와 실제 Chromium
**11개 통과**를 확인했다. 병행 실행 중 두 재시작 시험이 10초 assertion 제한을 넘겨 실패했고,
fixture의 OS process/Broker 시작과 UI 이벤트 전달 시간을 분리했다. 테스트용 재시작 endpoint는
30초 안에 실제 새 epoch의 reconciliation을 확인한 뒤 응답하며 ONLINE/UNKNOWN을 직접 주입하지 않는다.
각 UI assertion은 이후 실제 SSE/조회 결과를 확인한다. 실패한 fixture Agent 로그를 별도 artifact로
보존하고 native 실행 시험을 겹치지 않게 검증했다. 제품의 임의 startup 성능 기준을 통과했다고 해석하지 않는다.

2026-10-05 PC 등록 후속: 실제 Chromium **13개 통과**를 확인했다.
새 PC의 이름/일회용 토큰 발급, memory-only 보관/만료 표시/로그아웃 제거,
실제 browser runtime이 없는 Agent 재시작 후 core 명령 실행을 추가했다.
허용 폴더/로컬 profile을 표시하고 선택한 PC 정보에 명확한 region을 부여했다.
결과 만료 후 transient read replay가 재연결을 끊던 제품 문제를 [ADR-0020](adr/ADR-0020-retired-transient-read-reconciliation.md)으로 수정했다.
`dist/console-connect.png`를 실제 렌더링으로 확인했다. 단일 물리 PC의 fixture이며 두 PC/실제 AI host/clean installer 검증을 대신하지 않는다.

2026-10-05 여러 작업 폴더 후속: 실제 Chromium 전체 **14개 통과**.
PC의 허용 폴더 목록과 명령 실행의 폴더 선택을 추가했고, 선택한 폴더에 실제 파일을
작성/조회하며 기본 폴더의 파일 부재도 확인했다. PC를 명시적으로 선택하므로 등록만 된
OFFLINE PC를 우연히 실행 대상으로 사용하는 시험을 피한다. Python fixture 명령은 UTF-8로
한글 cwd를 출력한다. 설치/지원/활성화/health 값에 맞게 capability 상태도 표시한다.
`dist/console-test-results.xml`, `dist/console-workspaces.png`가 최신 증거다.
