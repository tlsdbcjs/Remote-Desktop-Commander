# CORE-LIFE-05: 사용자 Agent 백그라운드 수명 후속

2026-10-05 KST · 개발 구현/Windows 로컬 검증 · 전체 목표 진행 중

`racp-agentctl start/status/stop`을 추가했다. 저장한 등록 설정으로 별도 process를 시작하고
로컬 background 실행 상태와 실제 Gateway connected/epoch를 구분한다. 같은 인자로 다시
시작하면 기존 process를 반환하며 인자 변경과 foreground 중복 시작은 거부한다.
foreground/background/SCM entry point는 journal 복구 전에 Device/data OS 잠금을 얻는다.

실제 별도 background Agent에서 controller 종료 후 유지, Gateway 재시작 후 동일 boot의
재연결, 파일 저장, 실행 중 Job의 정상 취소·자식 정리·새 boot 뒤 CANCELLED reconciliation을
확인했다. Windows 강제 종료 뒤 Job Object의 자식 종료와 journal의 UNKNOWN 복구,
같은 key의 mutation 비재실행도 확인했다. counter 파일 값은 1을 유지했다.

잘못된 local bearer, PID 생성 시각 변조와 잘못된 제어 응답의 종료 요청을 거부한다.
등록 파일별 control 경로를 분리하고 Windows의 짧은 경로 alias도 같은 canonical lock을
사용한다. 정상 종료 complete는 프로세스 종료와 해당 instance의 보호된 cleanup receipt를
모두 확인한다. PID만 보고 강제로 종료하는 fallback은 없다.

증거는 `tests/unit/test_background_control.py`와 `tests/integration/test_background_agent.py`다.
현재 unit 6개와 실제 background integration 1개가 최종 전체 검사에 포함되어 통과했다.
등록/서비스/여러 폴더 회귀 묶음도 **6 passed**였다(`dist/background-regression-results.xml`).
최종 `scripts/check.py`: **290 passed, 17 skipped**, `dist/test-results.xml`.
frozen sync/Ruff format/lint와 Windows/Linux 타입 target의 **126개 source file**을 통과했다.
실제 Console Chromium **14 passed**이며 8개 unsigned 개발 wheel의 SHA-256/frozen lock/
126개 Python source 일치와 agentctl entry point를 확인했다.

검증은 Windows 10의 같은 물리 PC다. 사용자 background Agent이며 SCM 설치·서비스 계정,
로그오프/reboot 자동 시작·장기 soak·수동 upgrade/rollback/backup을 완료한 것으로 해석하지 않는다.
실제 AI host/두 PC와 Windows 11/Ubuntu runtime도 미검증 gate다.
[사용 안내](background-agent-guide.md), [설계 결정](adr/ADR-0022-user-background-agent-control.md).
