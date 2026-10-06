# Phase 3–5 Artifact / binary write 구현 결과

일자: 2026-10-02 KST · 환경: Windows 10 Pro x64 / Python 3.12.11

scoped upload/download transfer, 4 MiB chunk, 전체 크기 quota 예약, committed byte offset,
동일 chunk 재전송, SHA-256 검증 후 READY 게시, Range/ETag download, 10분 credential
만료 및 동일 scope 재인가, retention/GC를 구현했다. owner CLI는 Artifact를 업로드하고
filesystem.write의 artifact_id로 binary 입력을 제공한다. Agent는 입력 전체 해시를
검증한 후 create/replace/append writer에 연결한다.

실제 loopback Gateway와 Agent를 연결한 테스트에서 100 MiB 중간 HTTP 연결 단절 후
같은 transfer ID로 재개하고, 최종 Artifact 해시와 Agent 대상 파일 해시 일치를 확인했다.
Gateway를 종료·재시작해 committed offset 복구와 미커밋 tail 제거, 저장된 mutation 결과
재조회를 확인했다. 이는 OS 전원 손실/강제 crash의 전체 조합 검증을 대신하지 않는다.

ART-02 테스트는 quota 예약 초과, fsync의 ENOSPC 주입, incomplete cleanup,
실제 열린 다운로드 reader와 GC의 경합에서 READY 데이터 보호를 검증했다.
물리 볼륨을 채우는 시험은 수행하지 않았다. scope/hash 오류, credential 만료·재인가,
Range/If-Range/suffix/416, binary replace precondition 실패 시 원본 보존도 확인했다.
출력 Artifact 업로드가 거절되면 이미 완료된 provider 결과를 유지하고 pending 상태와
오류를 반환하며 spool을 보존한다. 당시 미구현이었던 자동 재전송과 첨부 조회는
[Phase 5 후속 기록](phase-5-retention-output-result.md)에서 구현·검증했다.

전체 regression: **39 passed, 1 skipped**. Windows/Linux mypy target에서 36개 source
file을 통과했고 8개 개발 wheel과 SHA-256 manifest를 재생성했다. Linux 타입 검사는
Ubuntu runtime 검증을 의미하지 않는다. JUnit과 build manifest는 dist에 저장한다.

계약: [ADR-0003](adr/ADR-0003-artifact-transfer-and-binary-write.md).
Phase 4 PTY 및 Phase 5 full Job/Handle inventory·Agent restart 만료·journal retention이
남아 있으므로 MVP 또는 전체 v1 완료로 표시하지 않는다.
