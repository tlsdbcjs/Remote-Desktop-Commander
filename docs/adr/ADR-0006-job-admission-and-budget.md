# ADR-0006 — durable Job 접수와 실행 budget

상태: 구현 진행 · 2026-10-02

Job은 Operation과 별도 ID/owner/device/state/revision을 가진 projection이다. 실제 실행
사실은 Operation journal이 보존한다. QUEUED/RUNNING/WAITING/CANCEL_REQUESTED/
RECONCILING 및 terminal COMPLETED/FAILED/CANCELLED/TIMED_OUT/UNKNOWN을 제공한다.
process.wait는 외부 process 종료를 기다리는 WAITING이며 waiting_reason=process_exit를
기록한다. 승인 대기는 Job WAITING으로 표현하지 않고 기존 approval TTL을 사용한다.
전송 의도만으로 실행 사실을 추측하지 않으며 Agent ACK/progress 이전에는 QUEUED를 유지한다.

Job 접수는 HTTP 202와 operation_id/job_id/state/poll_after_ms를 반환한다. 완료된 Job의
idempotent replay도 같은 Job ID를 반환하고 stdout/result는 job_get으로 조회한다.
MCP 접수는 정상 structured result이며 tool 오류가 아니다. CLI와 MCP의 Job get/list/
cancel은 별도 Job ID를 사용한다. Operation get/cancel도 계속 지원한다.

Job 기본 budget은 1시간, 최대 24시간이다. sync 상한은 120초다. shell 기본 60초,
filesystem.stat 5초, 다른 현재 operation은 30초다. MCP sync는 최대 20초로 접수하고
이를 넘는 explicit timeout은 실행 전에 거부하여 Job 선택을 요구한다.

승인 이후 durable admission 시점부터 queue·dispatch·실행의 시간을 포함한다. Device별
전송 중 일반 요청 16개와 대기 64개를 적용한다. enqueue/approval 소비/Job 생성은 같은
transaction에서 commit한다. dispatch intent도 전송 전에 commit한다. 대기 순서는 UTC
표시 시각이 아닌 저장 row 순서다. 동시에 실행한 worker의 provider 진입 순서까지
직렬화하지 않는다. queue full은 RESOURCE_EXHAUSTED와 retry_after_ms를 반환한다.

budget은 monotonic deadline으로 저장한다. 같은 OS clock epoch의 Gateway restart는
deadline을 재사용하며 시간을 더하지 않는다. Linux kernel boot ID 또는 보수적인 OS
boot fingerprint가 바뀌면 과거 monotonic 값을 재사용하지 않는다. 큐에서 아직 전송하지
않은 실행은 budget invalidation으로 종료하며 전송한 실행은 cleanup/reconcile 대상이다.
boot fingerprint는 duration 측정에 사용하지 않는다.

Python 3.12의 system-wide monotonic clock을 사용한다. 참고:
[Python time.monotonic](https://docs.python.org/3.12/library/time.html#time.monotonic).
UTC는 로그/표시 용도이며 장비 간 wall clock 동기화로 실행 budget을 계산하지 않는다.

실제 transport writer가 보내기 직전에 remaining_timeout_ms를 계산한다. 전송 큐에서
만료한 request는 보내지 않고 not_started TIMEOUT을 반환한다. Gateway deadline monitor는
transport 전송 대기를 별도 bounded task로 처리하여 다른 deadline/cancel을 막지 않는다.
deadline cancel은 owner cancel과 구분한다. 종료 확인 grace는 최대 5초이며 확인이 없으면
UNKNOWN이다. 같은 clock epoch의 재시작에서도 첫 cancel 전송 시각을 유지한다.
원자적 작업 완료 뒤 cancel은 완료 결과를 유지한다. queued Job의 입력 Artifact도 pin한다.

Agent는 수신 budget으로 로컬 deadline을 잡고, restart 시 durable result 없는 실행을
UNKNOWN으로 판정한다. Gateway restart는 live Agent의 active execution/progress inventory를
검증하여 복구한다. 전송한 mutation을 자동 재전송하지 않는다. Agent inventory에 없는
전송 실행은 UNKNOWN으로 남긴다. owner cancel intent도 저장하여 재연결 이후 전달한다.

결과 24시간 보존/tombstone, 늦은 결과의 별도 resolution record, output/spool의 전체
resource limit과 audit retention은 이어서 구현한다. 이 ADR은 Phase 5/MVP 전체 완료
선언이 아니다.
