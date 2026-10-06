# ADR-0019: 한 명령 PC 등록과 보호된 실행 설정

2026-10-04 KST · 채택 · CORE-ONBOARD-03 기반

`racp-connect`가 Gateway/허용 폴더/상태 경로/TLS를 먼저 검증하고 숨김 입력 또는 stdin으로
일회용 토큰을 받는다. 토큰을 명령행·환경 변수·설정에 저장하지 않는다. 초기 profile은 read_only다.
등록 응답은 4 KiB로 제한하고 redirect와 ambient HTTP proxy를 사용하지 않으며 TLS 검증을 유지한다.

Gateway/Device ID/workspace/data/profile/CA를 포함한 versioned AgentSettings와 Device credential을
하나의 SecretStore에 원자적으로 저장한다. Windows DPAPI 또는 POSIX 0600이며 새 등록은 기존 파일을
덮어쓰지 않는다. 동시에 새 파일을 게시해도 하나만 성공한다. 설정 identity와 credential identity가
다르면 시작을 거부한다. 원격 PC의 owner token은 필요하지 않다.

기본 위치는 Windows LOCALAPPDATA/RACP/agent 또는 POSIX XDG_STATE_HOME/racp/agent다.
`racp-agent`는 저장한 설정으로 시작한다. 기존 --workspace/--credentials 실행형도 지원한다.
명시한 실행 flag는 해당 실행의 override다. profile을 더 높게 선택하는 것은 PC 로컬 사용자의 행위이며
Gateway profile/승인 정책을 대신하지 않는다. Broker/CDP/plugin은 기존 별도 opt-in을 유지한다.

토큰 소비 전에 경로/CA/파일 크기와 로컬 저장 경로의 쓰기를 검사하고 setup marker로 겹친 등록을
막는다. 등록 후 저장 실패는 EXECUTION_UNKNOWN과 Device ID를 반환하며 자동 재등록하지 않는다.
중단된 marker는 확인 없이 지우지 않는다. 정상 credential이 있으면 설정으로 Agent를 시작할 수 있다.
credential이 없으면 owner가 등록된 Device를 확인하고 새 enrollment로 복구한다.

Console은 owner cookie/CSRF로 토큰을 발급한다. token은 component memory에만 보관하며
화면 닫기/만료/로그아웃 때 지운다. 명령 문자열에는 token을 넣지 않는다. loopback 주소에서 다른 PC의
연결을 안내하지 않고, 해당 경우 실제 Gateway HTTPS 주소를 사용하도록 표시한다.
Device 상세에 허용 폴더와 실제 로컬 profile을 표시한다.

이 결정은 개발 Agent의 foreground 등록/실행 경로다. signed installer, service identity/SCM,
자동 시작/업데이트, clean PC 설치, 실제 두 PC와 AI host 인수 gate는 계속 남는다.
