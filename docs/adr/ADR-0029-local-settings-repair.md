# ADR-0029 · 보호된 PC 등록의 설정 수정과 복구

2026-10-06 · Windows Client 0.1.6

0.1.5는 등록 후 설정을 조회할 수 있지만 수정할 수 없었다. 저장된 폴더나 외부 CA가
사라지면 설정 전체 검증이 실패하여 등록 상태 확인 화면에 머물렀다.

PC 설정의 등록 정보 수정과 오류 화면의 등록 정보 복구는 동일한 편집기를 사용한다.
Gateway 주소, 기본/추가 허용 폴더, 실행 profile, CA 파일을 변경한다. Device ID, Device
credential, journal data_dir는 유지한다. Gateway 주소/포트 변경은 동일한 Gateway 서버의
접속점 변경을 위한 것이며 다른 Gateway 서버로의 이동과 등록 해제는 이 변경의 범위에
포함하지 않는다.

보호된 credential을 현재 OS 계정으로 해독하고 구조와 identity를 검증한 뒤에만 편집
정보를 반환한다. 이 preview는 외부 폴더/CA의 존재를 요구하지 않는다. 새 등록 token이나
Device credential을 renderer에 반환하지 않는다. 손상된 credential이나 다른 사용자로
해독 불가능한 파일은 복구 편집으로 덮어쓰지 않는다.

저장은 Gateway HTTPS origin과 로컬 경로/TLS/profile/workspace 검증을 수행한다. Agent 시작과 설정 변경은
credential별 settings OS lock으로 읽기와 lifetime lock 취득 순서를 직렬화한다. 저장은
foreground/background/service Agent와 동일한 Device lifetime lock을 취득해야 하므로
실행 중 변경을 거부한다. 자동 중지나 재시작은 하지 않는다. 사용자가 Agent 중지를 누른
뒤 다시 저장하고, 다음 시작부터 변경을 적용한다.

편집 시 받은 보호 파일 SHA-256 revision이 달라지면 다시 열도록 안내한다. 기존 credential
document를 같은 OS 보호 방식으로 settings-backups/<UUID>.bin에 먼저 저장하고, 원본은
SecretStore의 임시 파일/fsync/atomic replace로 변경한다. 백업 실패 또는 유효하지 않은
입력은 원본을 유지한다. lock 파일을 삭제하여 실행 상태를 우회하지 않는다.

검증은 실제 Windows 보호 저장소와 Agent lifetime lock, 사라진 기본/추가 폴더 및 CA,
Gateway 주소 변경과 동일 Device/credential/journal 보존, stale revision 및 백업 실패를 다룬다. 실제 Electron
HTTPS/WSS 시험은 CA가 사라진 상태에서 GUI 복구, read_only 저장, 동일 Device 재연결과
원격 읽기, 실행 중 저장 거부를 다룬다. 파일 선택 dialog 결과는 시험 fixture로 제공하며
실제 사용자의 mouse/dialog 조작이나 141 PC의 새 버전 시험으로 주장하지 않는다.

0.1.7 후속 UI에서는 저장 설정 검증 실패 시 별도 **등록 정보 복구 / 상태 확인 / 완전 종료**
중간 화면을 표시하지 않고 이 편집기를 즉시 연다. 정상 PC 설정의 진입 버튼과 편집기 제목도
**등록 정보 편집**으로 통일한다. credential 보존·백업·lifetime lock 규칙은 그대로 유지한다.
