# Phase 5 결과 보존 / 출력 복구 구현 결과

일자: 2026-10-02 KST · 환경: Windows 10 Pro x64 / Python 3.12.11

Gateway/Agent journal에 결과 만료, mutation tombstone, 늦은 결과 resolution을 구현했다.
실제 Gateway/Agent HTTP·WS와 CLI/MCP에서 출력 첨부 catalog, scoped transfer,
재시작 후 자동 재전송을 검증했다. 설계 근거는
[ADR-0007](adr/ADR-0007-retention-and-output-recovery.md)에 있다.

## 검증

- 24시간 직전/정확한 경계, 활성·pinned 결과 보존, 본문/원문 key 제거, 410과 기존 ID.
- Gateway 재시작과 Agent 만료 요청 처리에서 같은 mutation 재실행 0.
- Device 한도 경계에서 신규 mutation 거부, 기존 tombstone 유지, 일회성 read 만료.
- UNKNOWN 이후 늦은 결과 별도 기록, 원래 Operation/Job 상태 유지, 반복 수신 dedupe.
- resolution TTL, audit 30일 경계, forward UTC jump 거부, OS clock 변경 후 보존 연장.
- 실제 65 MiB stdout/stderr 발생 명령의 64 MiB 한도 적용, 원본 framed Artifact,
  부모·자식 프로세스 종료 확인, 부분 결과와 idempotent replay.
- 별도 framed spool 한도와 fsync/생성 ENOSPC 주입, 실행 전 거부 또는 수집 결과 보존.
- UTF-8 변환 후 inline 64 KiB 제한과 원본 bytes Artifact 유지.
- Device 전송 슬롯 2개, 동시 접수·재시작·만료·갱신 우회 거부, 다운로드 검증 ACK와 슬롯 반환.
- 할당되지 않은 owner 전송에 대한 Device 조회/갱신 거부.
- 업로드 quota 실패 후 Agent/Gateway 재시작, 자동 첨부 회수, 원래 실행과 결과 유지.
- 첫 4 MiB chunk commit 이후 ACK 실패 주입과 Agent 재시작에서 같은 transfer ID로 완료.
- 해시 계산 중 deadline 취소가 도착해도 완료 결과와 출력 회수 유지.
- Agent spool 예약 초과 시 새 프로세스가 생성되지 않음.
- 실제 인증된 CLI/MCP의 outputs/resolutions 조회와 credential 비출력.

시간과 quota 경계는 clock/설정 주입 시험이다. 실제 24시간/30일 대기, 100만 건 부하,
10 GiB 공간 점유 또는 물리 disk-full을 실행했다고 표시하지 않는다. 출력 cap 시험은
실제로 65 MiB를 생성했고, transfer resume은 실제 HTTP 전송에서 4 MiB commit을 확인했다.

## 남은 범위

참조 Windows 11/Ubuntu, 실제 Codex/ChatGPT host, 강제 crash 조합과 장기 soak는 미검증이다.
million-row GC/목록 부하, journal/export 관리 명령, 관측 지표·doctor 확대, release migration도
남아 있다. Phase 6–12는 별도 구현 대상이며 이 기록을 MVP/v1 전체 완료로 간주하지 않는다.

최신 전체 gate는 **90 passed, 1 skipped**, frozen sync, Ruff format/lint,
Windows/Linux 타입 target 56개 source file 통과다. 8개 개발 wheel과 SHA-256 manifest를
재생성했다. JUnit은 `dist/test-results.xml`, manifest는 `dist/build-manifest.json`에 있다.
[작업 현황](implementation-status.md)에 전체 단계의 남은 범위를 기록한다.
