# 사용자 Agent 백그라운드 실행

2026-10-05 KST · 개발 실행 · 자동 시작/installer gate 미완료

[PC 등록](pc-connect-guide.md)을 마친 OS 계정에서 실행한다.
Agent가 이미 foreground에서 실행 중이면 먼저 그 실행을 정상 종료한다.

```powershell
uv run racp-agentctl start
uv run racp-agentctl status
uv run racp-agentctl stop
```

start 명령이 끝나도 Agent는 별도 process에서 실행하며 저장한 Gateway/CA/허용 폴더/profile을
사용한다. RUNNING은 로컬 background process이고 실제 Gateway 연결은 `connected: true`와
connection epoch로 확인한다. AI에서는 Device ONLINE/capability도 확인한다.
start 직후 연결 중일 수 있으므로 connected를 확인한 뒤 작업을 보낸다.

별도 credential 위치를 사용했다면 각 명령에 지정한다.

```powershell
uv run racp-agentctl start --credentials E:\RACP\my-pc-state\credential.bin --profile standard
uv run racp-agentctl status --credentials E:\RACP\my-pc-state\credential.bin
uv run racp-agentctl stop --credentials E:\RACP\my-pc-state\credential.bin
```

이번 실행의 profile/추가 폴더/desktop session 등 Agent 인자를 지원하며 저장한 기본값은 바꾸지 않는다.
같은 인자의 start는 현재 process를 반환한다. 실행 인자를 바꾸려면 stop 후 새 인자로 시작한다.
foreground/background/SCM host가 같은 등록 위치의 Device 또는 실행 DB를 중복해서 열면
실행 전에 거부한다. credential 복사로 새 인스턴스를 만들지 않고 별도 Device로 등록한다.

stop은 진행 실행을 취소하고 terminal/process/browser/Broker/plugin을 정리한다.
`cleanup_status: complete`는 해당 인스턴스의 실제 정리 완료 receipt를 확인한 결과다.
unknown/timeout이면 상태와 작업을 조사한다. PID만 보고 강제 kill하는 fallback은 없다.
강제 종료 뒤 새 Agent를 시작하면 기존 mutation은 UNKNOWN으로 복구할 수 있으며 자동 재실행하지 않는다.

등록 상태 폴더의 `background/agent.log`에 비밀 값 없는 진단 로그를 남긴다.
현재 1 MiB와 이전 3개로 제한한다. control.bin/shutdown.bin은 해당 OS 계정의 보호된
내부 상태이며 다른 PC/계정으로 복사하지 않는다.

로그오프/reboot 뒤 자동 실행, 서비스 설치/전용 계정, 업데이트/rollback/backup과 8시간 soak는
미완료다. 외부 launcher가 Job 수명으로 자식을 강제 종료하는 환경에서는 그 제한을 우회하지 않는다.
