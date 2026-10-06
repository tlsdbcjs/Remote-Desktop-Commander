# ADR-0020: 만료된 임시 읽기 결과의 재연결

2026-10-04 KST · 채택 · RPC/STATE 수명 경계

key 없는 동기 읽기의 결과는 보존 기간 뒤 operations에서 삭제할 수 있다.
Gateway가 먼저 삭제하고 Agent에 아직 결과가 남아 있으면, 이전 구현은 재연결의 result replay를
OPERATION_NOT_FOUND로 거부하여 PC 연결을 반복해서 끊었다. Console PC 등록 인수시험에서 재현했다.

삭제 transaction에서 필수 감사 기록 `transient_outcome_retired`를 먼저 남긴다.
operation_id/Device/request_id/trace_id가 정확히 일치하는 감사 기록을 partial index로 조회한다.
reconciliation 중이고 boot/epoch가 일치한 결과만 확인된 퇴역 읽기로 취급해 버린다.
operation/result/Artifact/Handle을 재생성하거나 요청을 다시 실행하지 않는다.

알 수 없는 ID, 다른 Device/request/trace, 오래된 epoch는 계속 거부한다. 일반 result 경로에도
이 예외를 적용하지 않는다. mutation tombstone과 retained read/Job의 기존 수명 계약을 유지한다.
서버 감사 기록은 실제 완료된 non-mutation/non-retained row의 삭제에서만 생성한다.
감사 쓰기 실패 시 삭제 transaction도 rollback한다.

실제 Agent 재시작 후 읽기 회복, 기존 조회의 404 유지와 위조 correlation/epoch 거부를
`tests/integration/test_retention_resolution.py`로 검증한다. 참조 OS/soak와 나머지 상태 복구 gate는 유지한다.
