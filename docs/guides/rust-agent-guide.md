# Rust Agent 실행 가이드

> **Document ID**: `DOC-GUIDE-RUST-AGENT`\
> **Status**: Active · **Target Version**: v0.1.21\
> **Last Updated**: 2026-10-10 · **Classification**: User Guide

## 1. 실행 파일

Windows x64 개발용 ZIP을 풀면 `racp-agent.exe`, `chromium/`, `build-manifest.json`이 들어 있다. Python·Node 설치 없이 Agent를 실행한다. Chromium 디렉터리는 실행 파일 옆에 유지한다. Node는 빌드 중 고정된 Chromium을 내려받을 때만 사용하며 패키지에 포함하지 않는다.

Rust Agent는 등록·설정, HTTPS/WSS, 작업 저널/Artifact, 100개 등록 RPC의 파일·셸·프로세스·메모리·터미널·CDP 브라우저·OS 관측·클립보드·Windows desktop Broker/Guardian·GDB/Ghidra 및 native/proxy carrier를 구현한다. 데스크톱 UI와 패키징은 Tauri/Rust를 사용한다. 선택적 분석 도구는 로컬 설치를 명시적으로 승인해야 사용한다. 최신 빌드 근거는 [구현 현황](../quality/implementation-status.md)에 기록한다.

## 2. 명시적인 상태 디렉터리

모든 실행 명령에는 절대 경로인 `--state-dir`를 지정한다. 기존 클라이언트의 `agent/credential.bin`을 사용하는 경우 해당 `agent` 디렉터리를 지정한다. 새로운 실기 테스트에는 별도 디렉터리를 사용하고, 동일한 상태 디렉터리에서 두 Agent 프로세스를 동시에 실행하지 않는다.

```powershell
$State = 'C:\RACP\rust-agent-lab'
New-Item -ItemType Directory -Force $State | Out-Null
.\racp-agent.exe --help
.\racp-agent.exe info --state-dir $State
```

현재 프로세스 사용자에게 속한 DPAPI credential을 읽는다. 다른 Windows 계정의 credential을 복사해 사용하지 않는다.

## 3. 등록과 설정

`bridge`는 기존 클라이언트와 동일한 JSON 요청을 표준 입력으로 받는다. 등록 토큰은 명령 인수에 넣지 않는다. 콘솔 입력을 UTF-8로 설정한 다음 명령을 실행하고, 한 줄의 등록 JSON을 입력한다. 기존 클라이언트에서 등록한 상태를 사용하는 경우 등록을 반복할 필요가 없다.

```powershell
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
.\racp-agent.exe bridge --state-dir $State
```

입력 형식은 `{"action":"enroll",...}` 또는 `{"action":"enroll_connection",...}`이며, 세부 필드는 [클라이언트 bridge 계약](../../crates/racp-contract/schemas/bridge.json)을 따른다. 토큰을 셸 히스토리에 저장하지 않도록 JSON은 실행 후 표준 입력에 직접 입력한다.

설정과 현재 revision을 확인한다.

```powershell
.\racp-agent.exe settings --state-dir $State
```

기본 정책은 `read_only`다. 쓰기·프로세스 실행·브라우저 입력 등은 필요한 정책을 설정한 뒤 사용한다. 설정 변경은 `bridge`의 `update_settings`와 표시된 revision을 사용한다.

## 4. 연결과 종료

```powershell
.\racp-agent.exe run --state-dir $State
```

등록된 Gateway에 연결한다. 전경 실행은 Ctrl+C로 종료한다. 백그라운드 실행은 다음 명령을 사용한다.

```powershell
.\racp-agent.exe start --state-dir $State
.\racp-agent.exe status --state-dir $State
.\racp-agent.exe activity --state-dir $State
.\racp-agent.exe stop --state-dir $State
```

조회·제어 명령은 `ok`와 `result` 또는 오류 `code`를 포함하는 JSON을 출력한다. `stop` 응답의 `cleanup_status`로 자원 정리 결과를 확인한다. 요청 실패는 종료 코드 4를 반환한다.

브라우저 origin 제한과 외부 CDP 연결은 전경 실행에 명시한다.

```powershell
.\racp-agent.exe run --state-dir $State --browser-allow-origin 'https://example.com'
```

외부 CDP를 사용하려면 `--enable-cdp`를 추가한다. 백그라운드 `start`에는 이 옵션을 전달하지 않는다.

`desktop_enabled=true`인 설정에서는 현재 사용자 세션의 Rust Broker가 별도 process/Job으로 시작된다. Gateway에서 `desktop.sessions`로 세션 ID를 확인하고 `desktop.monitors`, `desktop.windows`, `desktop.foreground`, `desktop.screenshot`을 호출한다. 잠금·비활성 세션·다른 사용자 로그인·secure desktop에서는 캡처를 거부한다. 캡처와 선택적 preview는 PNG Artifact로 전달된다. 입력/UIA는 허용된 로컬 권한과 유효한 lease/observation, 독립 Guardian 상태를 확인한 뒤 실행한다. 서비스는 허용된 사용자 로그온 Broker의 Named Pipe 등록을 지원한다. 사용자 토큰이나 비밀번호로 다른 세션의 프로세스를 시작하지 않는다.

## 5. 빌드와 검증 보류

Windows x64, PowerShell 7, Rust 1.90.0, Node 22.23.0에서 다음을 실행한다.

```powershell
pwsh -File scripts/build_rust_agent.ps1
```

출력은 `dist/rust-agent/0.1.21/win-x64/<build-id>/`다. 기존 산출물을 덮어쓰지 않는다. ZIP SHA-256과 파일별 크기·해시, source commit은 동봉된 기록으로 확인한다. 개발 빌드는 서명되지 않았다.

사용자 요청에 따라 현재 migration 브랜치의 자동 테스트는 보류한다. `rust-agent` CI는 production release Agent와 Chromium 패키지만 빌드한다. 이후 `workflow_dispatch`의 `run_tests=true`로 기존 acceptance를 재개할 수 있다. 빌드 성공은 기능 동등성·실제 데스크톱 입력 검증을 의미하지 않는다.

## 6. 선택적 네이티브 분석 도구

GDB/Ghidra/JDK/CDB는 패키지에 포함하지 않는다. 다음 명령은 명시된 로컬 파일을 검증하고 provenance, SHA-256 및 승인 manifest를 등록한다. --directory는 새 절대 경로여야 한다.

```powershell
.\racp-agent.exe provision-gdb --state-dir $State --directory 'C:\RACP\tools\gdb-registration' --gdb 'C:\Tools\gdb\gdb.exe' --backend-version '17.1'
.\racp-agent.exe provision-ghidra --state-dir $State --directory 'C:\RACP\tools\ghidra-registration' --ghidra 'C:\Tools\ghidra' --java 'C:\Tools\jdk\bin\java.exe'
.\racp-agent.exe provision-native --state-dir $State --directory 'C:\RACP\tools\cdb-runtime' --cdb-directory 'C:\Program Files (x86)\Windows Kits\10\Debuggers\x64'
```

등록 후 Agent를 재시작한다. GDB 버전과 실행 파일, Ghidra/JDK inventory, CDB DLL/실행 파일의 해시·PE 아키텍처·파일 버전을 확인한다. 실행 기능은 로컬 권한에서 별도로 허용해야 한다.

## 7. 서비스와 로그인 세션

SCM 명령은 racp-agent.exe service --state-dir <absolute-path>다. 서비스 이름은 RACPAgent이며 Session 0과 활성화된 서비스 SID, 비관리자 실행 계정을 확인한 뒤 credential을 연다. LocalSystem/관리자 실행은 거절한다. DPAPI 등록은 해당 서비스 계정의 소유여야 한다.

관리자는 명시적인 SID 목록과 endpoint 경로를 구성한다.

```powershell
.\racp-agent.exe configure-service-login --state-dir $State --device-id '<registered-device-id>' --agent-sid '<service-account-SID>' --service-sid '<RACPAgent-service-SID>' --login-user '<interactive-user-SID>' --endpoint 'C:\RACP\desktop-login-endpoint.json'
```

endpoint 상위 폴더는 지정 사용자가 통과할 수 있어야 한다. 서비스는 허용된 OS 사용자/세션만 등록하고, Broker는 GUI 초기화 전 서비스 소유 Job에 들어간다. 지정 사용자의 로그온 세션에서 racp-agent.exe broker-login --login-endpoint <endpoint-path>로 연결한다. Guardian은 Task Scheduler를 통해 Job 밖에서 실행되며 실제 PID/birth와 release-only 상태를 확인한다.

## 8. 관련 문서

- [Rust 전환 설계](../spec/client-rust-migration.md)
- [구현 계획](../spec/client-rust-implementation-plan.md)
- [데스크톱 클라이언트 가이드](desktop-client-guide.md)
