# ADR-0024: 사용자가 선택한 Windows 로그인 후 Agent 연결

2026-10-05 · 채택 · CORE-LIFE-05 부분 구현

등록한 Windows packaged client에 로그인 후 Agent 연결 checkbox를 제공한다.
기본은 꺼짐이며 사용자가 켰을 때 현재 Windows 사용자의 startup 항목에
고정된 client executable와 `racp-background-agent` 인자만 등록한다.
토큰·Gateway·파일 경로를 startup argv에 넣지 않는다. current-user 항목이며 서비스/관리자 권한은 없다.

launcher는 보호된 기존 설정을 읽고 같은 background Agent start 경로를 호출한다.
RUNNING을 확인한 뒤 창을 열지 않고 UI controller를 종료한다. Agent는 기존 lifetime lock과
PID/creation-time·보호된 local control·cleanup 계약을 유지한다. 설정/시작 오류는 일반 UI로 복귀한다.
Agent stop은 자동 시작 checkbox를 바꾸지 않으며 다음 로그인에 다시 연결한다는 문구를 표시한다.
UI checkbox를 끄면 자기 고정 startup 항목만 제거한다. OS가 시작 앱을 비활성화하면 표시한다.

Electron의 getLoginItemSettings를 동일 executable/args로 조회하고 자기 name/path/user scope를 확인한다.
실제 Windows에서 `--` switch는 launchItems.args에 포함되지 않는 것을 확인했으므로
고정 positional 인자를 사용한다. getLoginItemSettings는 args 지정 여부에 따라 openAtLogin이
달라져 expected-args 상태와 inventory를 각각 조회한다.

Node unit 6개, 실제 Electron 등록/파일·명령/background와 saved Agent 재시작 E2E,
고유 fixture HKCU 값의 실제 등록/해제·args 검증을 통과했다.
native fixture는 product startup 항목을 사용하지 않고 자기 고유 Run/StartupApproved 값을 정리한다.
실제 OS logoff/reboot/logon과 installer upgrade·제거 연동은 후속 gate다.
portable 폴더를 옮기면 등록한 executable path가 달라지므로 자동 시작을 다시 설정해야 한다.

공식 API: [Electron login item settings](https://www.electronjs.org/docs/latest/api/app#appsetloginitemsettingssettings-macos-windows).
