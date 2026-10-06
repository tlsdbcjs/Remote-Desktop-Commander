# ADR-0007 — 실행 결과 보존과 출력 첨부 복구

상태: 구현 · 2026-10-02

## 결정

Operation terminal 상태와 결과는 불변이다. UNKNOWN 이후 도착한 다른 terminal 결과는
별도 resolution에 기록하고 canonical digest로 중복을 제거한다. Operation당 최대 16개이며
원래 UNKNOWN을 SUCCEEDED 등으로 덮지 않는다. owner 인증을 거친 HTTP/CLI/MCP 조회를 제공한다.

결과와 resolution 본문은 최소 24시간 보존한다. 활성 실행과 미전송 출력이 있는 Agent
기록은 compact 대상에서 제외한다. 운영 GC는 UTC cutoff와 저장된 monotonic 경과 시간을
함께 확인한다. OS clock identity가 바뀌거나 기존 schema에 경과 시간이 없으면 최소
24시간을 추가 보존한다. 테스트용 명시적 `now`는 UTC 경계 주입으로, 실제 24시간 대기를
대신했다고 표시하지 않는다. audit 기본 보관 기간은 30일이다.

본문 만료 후 mutation의 scope/key hash, payload digest, operation ID와 terminal metadata를
Device 수명 동안 남긴다. 동일 키는 410 OPERATION_EXPIRED와 기존 ID를 반환하고 재실행하지
않는다. 다른 payload는 IDEMPOTENCY_CONFLICT다. 아직 본문이 있는 mutation도 포함해
Device당 100만 건을 넘기지 않으며, export/새 Device 관리 전환 안내와 함께 신규 mutation을
거부한다. 명시적 키가 없는 일회성 read는 24시간 후 제거할 수 있다.

stdout+stderr 원본 총 수집 한도는 64 MiB다. inline UTF-8 text는 합계 64 KiB이고,
채널별 원본 bytes를 framed Artifact로 보존한다. raw bytes에 channel/length를 붙이는
물리 spool 한도는 작업당 72 MiB다. 상한·쓰기·fsync 실패는 RESOURCE_EXHAUSTED와 부분
결과를 반환한다. 저장을 중단한 뒤에도 파이프는 EOF까지 소비해 containment 종료 확인이
완료되도록 한다. provider 완료 후 출력 해시 계산/업로드가 취소되어도 완료 사실을 유지한다.

출력 첨부는 실행 결과와 별도의 durable catalog로 관리한다. 출력 ID, 크기, SHA-256,
media type을 Agent가 결과와 함께 선언하며 Gateway는 owner/Device/operation과 결합한다.
완료된 작업의 새 전송은 이 선언과 정확히 일치해야 한다. 업로드 실패는 원래 결과에
pending으로 기록한다. 자동 복구는 저장된 transfer ID/offset을 재사용하고 exponential
backoff(최대 60초), 한 번에 최대 16개 조회와 순차 업로드를 사용한다. 재접속과 재시작 후
완료 첨부를 별도 catalog에 기록하고 원래 결과를 변경하지 않는다. owner는
`operation outputs`로 회수된 Artifact를 조회한다.

Agent spool은 10 GiB와 pending+예약 64건으로 제한한다. 실행 전 최악 크기를 예약하고
미전송 파일은 조용히 제거하지 않는다. 가득 차면 신규 작업을 거부하되 terminal close와
process terminate는 계속 허용한다. 성공한 출력 파일 삭제 실패는 다음 collector에서 재시도한다.

동시 유효 Artifact transfer는 Device당 2개, 전체 64개다. 만료 후 갱신도 슬롯을 다시
확인한다. 다운로드는 SDK에서 전체 해시를 검증한 뒤 scoped completion ACK를 보내 슬롯을
반환한다. ACK 중복은 같은 결과를 반환하고 재개 시 갱신으로 슬롯을 다시 확보한다.
Device credential로 operation에 할당되지 않은 owner 전송을 조회하거나 갱신할 수 없다.

## 제한

현재 compact는 logical 삭제다. SQLite 파일/WAL/backup의 secure erase를 의미하지 않는다.
schema version 1에 additive DDL을 사용하며 release migration/backup 절차는 Phase 11에서
구현한다. spool 예약은 관리되는 파일의 크기를 제한하며 실제 디스크의 여유 공간을 보장하지
않는다. 물리 disk-full, 참조 OS, 실제 MCP host와 장기 soak는 별도 gate다. 출력 첨부 조회
추가와 schema 변경은 RACP 내부 protocol 1 개발 버전에 반영했다. 배포된 이전 Agent와의
호환 release를 검증한 것으로 표시하지 않는다.
