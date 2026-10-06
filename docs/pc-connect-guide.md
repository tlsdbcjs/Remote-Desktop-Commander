# PC 등록과 연결

2026-10-04 KST · 개발 Agent용 · installer/자동 시작 gate는 미완료

Gateway를 인증된 HTTPS 주소로 준비하고 [OAuth 설정](remote-mcp-oauth-setup.md)을 완료한다.
이 문서는 실행 가능한 개발 Agent의 등록 절차다. Python 3.12/uv와 프로젝트 개발본이 필요하다.
signed installer와 clean PC 설치 인수시험은 아직 제공하지 않는다.

1. Gateway Console에서 owner로 로그인하고 장비 → 새 PC 연결에 이름을 입력한다.
2. 일회용 등록 토큰을 발급한다. 기본 10분, 한 번만 사용한다.
3. 연결할 PC의 개발본을 준비하고, 허용 폴더를 선택하여 실행한다.

```powershell
uv sync --package racp-agent --frozen
uv run --package racp-agent racp-connect --gateway https://gateway.example:8765 --workspace E:\MyDocuments
```

숨김 토큰 입력창에 Console의 값을 붙여 넣는다. 토큰을 명령 인자나 환경 변수에 넣지 않는다.
private CA라면 `--ca-file E:\RACP\ca.pem`을 추가한다. 공개 CA는 기본 trust를 사용한다.
인증서/hostname 검증을 우회하는 옵션은 없다. localhost 주소는 같은 PC에서만 사용한다.

등록 후 configured가 출력되고 Agent가 foreground에서 연결을 유지한다. 실제 WSS 인증과
상태 대조가 완료되면 agent_connected와 Device ID/실행 계정/허용 폴더/profile을 표시한다.
Console의 PC와 capability를 확인하고 AI에서 해당 Device를 명시적으로 선택한다.
기본 read_only는 자료 조회용이다. 초기 등록에서 `--profile standard` 또는 명시적인
`--profile trusted_personal`을 선택할 수 있다. Gateway와 Agent 양쪽 정책을 통과해야 실행한다.

등록 정보와 설정은 같은 보호된 credential.bin에 저장한다. Windows 기본 위치는
LOCALAPPDATA/RACP/agent, POSIX는 XDG_STATE_HOME/racp/agent 또는 ~/.local/state/racp/agent다.
다른 디렉터리에서 재시작해도 저장한 Gateway/폴더/CA를 사용한다.

```powershell
uv run --package racp-agent racp-agent
```

새 실행에서 profile을 명시적으로 바꾸려면 다음과 같이 실행한다. 저장한 기본값은 그대로 유지한다.

```powershell
uv run --package racp-agent racp-agent --profile standard
```

`--state-dir E:\RACP\my-pc-state`로 등록했다면 재시작 시
`racp-agent --credentials E:\RACP\my-pc-state\credential.bin`을 사용한다.
`--configure-only`는 등록/저장 후 실행하지 않는다. 기존 CLI enrollment와
`racp-agent --credentials ... --workspace ...` 실행도 지원한다.

이미 등록한 위치에 racp-connect를 다시 실행하면 덮어쓰기 전에 거부한다.
중단된 setup marker가 있고 credential이 없다면 owner가 Device 목록을 확인한 뒤 새 토큰으로 복구한다.
credential이 존재하면 먼저 racp-agent로 시작한다. 다른 PC/OS 계정으로 credential을 복사하지 않고 새로 등록한다.
등록 응답이나 저장을 잃은 경우 같은 토큰을 자동으로 재사용하지 않는다.

Browser runtime이 없으면 browser capability를 사용할 수 없지만 파일/셸 연결은 유지한다.
상세에 실제 허용 폴더/로컬 profile을 표시하고 사용 가능한 capability로 작업한다.
여러 폴더는 `--allow-workspace ID=PATH`로 등록하고 AI/Console에서 선택한다.
[여러 작업 폴더 안내](named-workspaces-guide.md)를 참조한다.
개발 Agent의 [백그라운드 시작·상태·종료](background-agent-guide.md)를 지원한다.
서비스 설치/자동 시작, 실제 AI host/두 PC workflow는 다음 구현·인수 범위다.
