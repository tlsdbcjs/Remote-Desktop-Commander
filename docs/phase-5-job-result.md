# Phase 5 Job / queue / budget 구현 결과

일자: 2026-10-02 KST · 환경: Windows 10 Pro x64 / Python 3.12.11

별도 durable Job ID와 owner/device/state/revision, HTTP 202 접수, Job get/list/cancel,
CLI와 MCP를 구현했다. 완료 결과는 Job 조회로 제공하고 idempotent 접수 재요청은 같은
Job ID를 반환한다. Operation 조회·취소는 별도로 유지한다. process.wait의 WAITING은
process_exit 사유를 기록하고 Gateway 재시작 후 복구한다.

Job 기본 1시간/상한 24시간, sync 상한 120초, MCP sync 상한 20초를 적용했다.
Device별 실행 16개·대기 64개와 durable FIFO dispatch를 제공한다. budget은 enqueue부터
계산하고 실제 WS 전송 직전에 남은 시간을 갱신한다. 전송 큐에서 만료한 요청은 보내지
않는다. deadline monitor가 다른 전송 대기에 막히지 않도록 bounded delivery task를 쓴다.
Gateway와 Agent deadline 취소가 cleanup과 겹쳐 결과를 잃는 실연결 실패를 재현했고,
cleanup 보호와 deadline/owner cancel 구분으로 수정했다.

실제 Gateway/Agent HTTP·WS 연결에서 확인한 항목:

- 202/QUEUED/별도 Job ID, default 1시간 값, 최대 24시간 값과 초과 거부.
- 완료 결과 조회, 같은 key/같은 Job 재접수, 늦은 cancel의 완료 결과 보존.
- 실행 16개를 barrier로 유지하고 대기 64개 이후 RESOURCE_EXHAUSTED 확인.
- 대기 중 cancel/timeout은 provider를 호출하거나 대상 파일을 생성하지 않음.
- barrier 해제 이후 저장된 dispatch 순서의 FIFO와 각각 한 번 실행.
- 실행 중 WAITING과 deadline을 Gateway 재시작 이후 복구.
- 실행 16개+대기 Job의 Gateway 재시작에서 active mutation 재전송 0,
  기존 active 완료 이후 queued mutation만 한 번 dispatch.
- MCP 접수/polling과 과도한 sync budget의 실행 전 거부.
- 실제 CLI Job 접수/get/list/cancel과 owner secret 비출력.
- cleanup 중 owner cancel intent와 CANCEL_REQUESTED를 Gateway 재시작 이후 유지.
- 첫 cancel 전송 시각을 유지해 Gateway 재시작이 cleanup grace를 늘리지 않음.
- queued Job의 binary 입력 Artifact를 GC에서 보호하고 Job 취소 후 정리.
- transport backlog에서 만료한 request가 쓰이지 않고 heartbeat는 계속 전송됨.

기존 process tree timeout/cancel/revoke/lease 테스트도 유지한다. barrier 시험은 실제
전송과 admission을 검증하는 결정적 장애 주입이며, 80개 실제 장시간 프로세스 부하
시험을 수행한 것으로 표시하지 않는다. 1시간/24시간은 설정·경계·저장 검증이고 실제
해당 시간 전체 실행/8시간 soak를 대신하지 않는다.

이 Job 구현 시점 전체 검사: **69 passed, 1 skipped**. Ruff format/lint, Windows/Linux 타입 target의
53개 source file을 통과했다. 8개 개발 wheel과 SHA-256 manifest를 재생성했다.
JUnit은 `dist/test-results.xml`, manifest는 `dist/build-manifest.json`에 있다.

계약과 clock 근거는 [ADR-0006](adr/ADR-0006-job-admission-and-budget.md)에 있다.
24시간 결과 보존/tombstone, UNKNOWN 이후 resolution, output/spool cap과 복구,
audit retention의 후속 구현은 [추가 기록](phase-5-retention-output-result.md)에 있다.
참조 OS/실제 host/packaging/hardening 검증은 남아 있다. Phase 5 또는
MVP 전체 완료로 표시하지 않는다.
