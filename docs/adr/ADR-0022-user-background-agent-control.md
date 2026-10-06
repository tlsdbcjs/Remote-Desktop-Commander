# ADR-0022: 사용자 Agent의 백그라운드 수명 제어

2026-10-05 KST · 개발 구현 · CORE-LIFE-05 후속 일부

`racp-agentctl start/status/stop`은 등록한 OS 계정으로 Agent를 실행한다. 새 Device credential을
만들거나 권한을 확대하지 않으며 기존 보호된 설정을 사용한다. foreground/background/SCM host는
credential 위치의 Device 잠금과 data directory의 OS 파일 잠금을 journal 열기/복구 전에 얻는다.
종료 또는 crash 때 OS가 잠금을 해제하며 lock 파일을 unlink하지 않는다.
Agent Python class를 직접 사용하는 개발 fixture는 entry point 잠금 밖에 있으며 별도 Device/data로 격리한다.

Windows는 [DETACHED_PROCESS](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)로
호출자의 console을 상속하지 않는다. 외부 Job 제한을 벗어나는 breakaway는 제공하지 않는다.
POSIX는 새 session을 요청한다. 실제 검증은 Windows 10의 controller process 종료 후 유지이며
창/로그오프/reboot/SCM 수명과 Ubuntu 실행은 별도 gate다.

제어 채널은 127.0.0.1 임시 TCP port의 한 줄 strict JSON이며 status/stop만 받는다.
credential과 별개인 random bearer, 요청 nonce, instance ID와 실제 PID/생성 시각을 확인한다.
port/bearer는 DPAPI 또는 POSIX 0600 SecretStore로 보호하며 출력/명령 인자에 넣지 않는다.
요청 4 KiB, 응답 16 KiB, 동시 연결 8개와 I/O 2초 제한을 적용한다. HTTP/WS protocol이 아니다.
OS 계정 내 권한 격리는 별도로 제공하지 않는다.

start는 실행 중인 같은 인스턴스를 반환하며 인자가 다르면 먼저 종료해야 한다.
Windows venv launcher와 실제 Python PID가 다를 수 있어 OS가 확인한 PID/생성 시각의
자식 관계로 startup을 식별한다. 확인하지 못하면 timeout이며 자동 재시작하지 않는다.

stop은 먼저 상태 응답의 인스턴스를 확인한 뒤 정상 취소/정리를 요청한다. PID 파일만 보고
강제 종료하지 않는다. 반복 stop은 진행 중 cleanup을 다시 취소하지 않는다.
종료 관측과 해당 인스턴스의 보호된 cleanup receipt가 있어야 complete를 보고한다.
확인되지 않은 종료는 unknown/timeout이며 실행 결과와 OS 상태를 다시 조사한다.

로그는 background/agent.log, 1 MiB와 이전 3개로 제한한다. 상태는 process RUNNING과 실제
Gateway connected/epoch를 구분하며 Device/boot/실행 계정/profile/허용 폴더/진행 작업 수를 표시한다.
command 내용·credential·bearer는 표시하지 않는다.

installer/자동 시작, 비관리자 전용 서비스 계정/다른 사용자 Broker, 수동 upgrade/rollback/backup,
장기 soak와 전체 Phase 11–12 및 실제 AI host/두 PC gate는 유지한다.
