# ADR-0025: Agent 현황·최근 활동과 트레이 수명

2026-10-05 · 채택 · 사용자 지시: 진행 현황과 트레이 완전 종료

Windows client 0.1.2는 현황/PC 설정 탭을 제공한다. 현황에는 연결 상태, 진행 중 작업 수,
PID, 최근 확인 시각, 실제 Agent journal의 현재 operation/state, 최근 40개 활동을 표시한다.
주기적 sample은 중복 실행을 합치고 상태 관측 실패 시 이전 ONLINE 표시를 제거한다.
상태와 activity를 한 번의 sample로 갱신하며 3초마다 새 관측을 시도한다.

활동은 private Python bridge의 고정 activity 명령으로 읽는다. log tail은 최대 32 KiB,
반환은 최근 40개다. 허용된 이벤트/registry operation/OperationState/UTC timestamp/공개 operation ID만
추출한다. raw line, argv, command, path, payload, stdout/stderr, credential은 반환하지 않는다.
잘린 JSON/예전 timestamp 없는 이벤트를 처리하고 원시 로그를 renderer로 넘기지 않는다.
background status의 현재 operation도 ID/name/state만 제공한다.

네이티브 Tray와 상태별 ICO를 사용한다. 창 닫기는 숨김이고 tray click/double-click은 복원한다.
메뉴에 현황 창/Agent 시작/Agent 종료/완전 종료를 제공한다. 진행 작업 또는 수명 요청은 황색,
연결은 녹색, 미연결은 회색 아이콘이다. login launcher도 UI/tray를 유지하므로 숨겨진 Agent를
사용자가 종료할 수 있다. 기존 0.1.1의 controller 종료 동작을 바꾼다.

UI 완전 종료, tray 완전 종료, 앱 quit는 같은 종료 경로를 사용한다. private stop 응답의 STOPPED와
정리 결과를 확인한 뒤에만 tray/app를 종료한다. unknown/통신 오류면 앱·tray를 유지하고 창에 오류를 표시한다.
일반 창 닫기는 관리 중 작업을 취소하지 않는다. 다음 Windows 로그인 자동 시작 checkbox는 별도 설정이다.

실제 Electron E2E는 장기 Job/현재 작업 표시/활동 기록, 실제 native Tray 생성·콜백,
창 닫기 후 Agent 유지와 원격 파일 작업, 완전 종료의 자기 Job PID 소멸을 확인한다.
서버에 전송되지 않은 취소 결과는 저장된 journal을 다음 연결에서 reconciliation해 CANCELLED로 확인한다.
소프트웨어 콜백 검증과 실제 사용자 마우스로 Windows tray를 클릭하는 인수시험은 구분한다.
unit은 종료 전 대기, cleanup unknown 거부·재시도와 stale ONLINE 제거를 검사한다.

공식 근거: [Electron Tray](https://www.electronjs.org/docs/latest/api/tray).
