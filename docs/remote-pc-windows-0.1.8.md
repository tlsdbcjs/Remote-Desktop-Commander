# Windows 원격 PC 제어 — Client 0.1.8

2026-10-06 · 개발 빌드 · A는 작업 PC, B는 제어할 원격 PC

RACP의 목적은 A에서 B의 파일·프로그램·터미널·화면을 제어하는 것이다.
A의 AI 앱과 작업 도구는 그대로 사용하고, B의 Agent는 요청한 작업을 실행하거나
자료를 수집해 돌려준다. 특정 디버거나 분석 프로그램 선택은 일반 원격 제어의 전제 조건이 아니다.
전용 디버거의 실시간 attach 프로토콜을 제공했다는 의미도 아니다.

## B의 Windows 화면과 입력 허용

1. 이전 Client의 트레이 메뉴에서 **완전 종료**한다.
2. 새 포터블 ZIP을 별도 폴더에 풀고 `RACP Client.exe`를 실행한다.
   Node/npm/Python의 별도 설치는 필요 없다. 기존 Windows 계정에서 실행하면 저장된 등록을 사용한다.
3. **등록 정보 편집**에서 **이 PC의 Windows 화면 캡처·마우스·키보드 조작 허용**을 선택해 저장한다.
   실행 중에는 설정 수정이 거부되므로 먼저 Agent를 중지한다.
4. Agent를 시작한다. 현재 Windows 로그인 세션의 Broker를 시작하고 화면·입력 준비 상태를 표시한다.
5. A에서 Device를 선택하고 desktop session/window 조회 → 캡처 → 입력 순서로 작업한다.
   기존 실행 profile 및 승인 정책은 그대로 적용된다.

꺼진 화면 허용 설정은 기존 등록의 기본값이다. 설정 저장만으로 다른 사용자의 세션을 선택하지 않는다.
잠금 화면, Session 0, UAC 보안 데스크톱과 준비되지 않은 foreground에서의 입력은 별도 제약을 갖는다.
GUI 창 닫기는 백그라운드 실행을 유지하며, 트레이의 **완전 종료**로 Agent와 앱을 종료한다.

## 일반 프로세스 메모리 관측

새 MCP 도구는 `process_memory_regions`, `process_memory_read`다.
`process_list` 또는 `process_inspect`에서 받은 `pid`, `create_time`, `agent_boot_id`로
대상을 지정한다. 영역 조회는 `start_address`와 `limit`, 읽기는 `address`와 `size_bytes`를 받는다.
주소는 `0x...` 문자열이고 1회 읽기 한도는 16 MiB다.

4 KiB 이하는 hex와 SHA-256를 반환하고, 큰 결과는 인증된 Artifact로 내려받아 A에서 사용한다.
원격 바이너리 파일은 기존 허용 폴더의 파일/Artifact 전송으로 A에 회수할 수 있다.
프로세스 메모리 읽기는 파일 폴더 선택으로 격리되는 작업이 아니므로 `read_only`에서 거부하고,
`standard`에서는 owner 승인을 요구한다. OAuth에도 read와 execute 권한이 모두 필요하다.
Agent와 그 제어 프로세스는 대상에서 제외한다.

읽기는 Windows가 허용한 권한으로 수행하며 대상 메모리를 수정하거나 프로세스를 일시 중지하지 않는다.
실행 중 메모리는 수집 중 바뀔 수 있으므로 결과에 `atomic_snapshot: false`를 표시한다.
전체 프로세스 dump 또는 실시간 디버거 연결의 구현 완료를 뜻하지 않는다.
네이티브 접근과 구조는 Microsoft의 [ReadProcessMemory](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory),
[VirtualQueryEx](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualqueryex),
[MEMORY_BASIC_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-memory_basic_information)을 따른다.

## 검증 범위

- 실제 Windows의 자체 시험 프로세스에서 메모리 영역·읽기·큰 결과 hash·취소 후 부분 파일 정리를 확인했다.
- 분리된 시험 Gateway/Agent의 MCP 요청 → 128 KiB Artifact 회수·hash 일치를 확인했다.
- 실제 Windows 자체 시험 창에서 Unicode·단축키·드래그·캡처와 UIA 입력·Invoke를 확인했다.
- 실제 Electron과 사설 CA HTTPS/WSS에서 등록·저장 설정·화면 허용 저장·Agent 재연결·실행 중 편집 거부·완전 종료를 확인했다.

위 시험은 같은 물리 Windows PC의 자체 fixture다. 2026-10-06 17:43 KST에 141 Agent가
ONLINE/epoch 5로 재접속했고 `process.memory_regions`와 `process.memory_read` capability를 보고했다.
A의 lab Gateway는 17:44 KST에 새 registry로 교체했으며 141은 같은 boot ID로 epoch 6에
자동 재접속했다. 새 native Codex app-server 초기화에서는 OAuth RACP 86개 도구와 두 memory
도구가 모두 노출됐다. 현재 대화의 기존 MCP tool cache는 재시작 전 목록을 유지할 수 있으므로
새 도구가 보이지 않으면 RACP MCP 연결 또는 Codex 앱을 다시 로드한다.

이후 사용자가 141에서 화면 허용 설정을 저장하고 Agent를 다시 시작했다. 2026-10-06 18:08 KST
기준 Device는 새 boot ID `boot_0ffb8e896928407ba8fce44818b9c684`, epoch 7로 ONLINE/healthy이며
desktop capability도 enabled/healthy다. session 1의 Broker, UI Automation, input Guardian이 모두
준비됐고 3840x2160 / 150% DISPLAY1의 session/window/monitor/foreground 조회와 실제 전체 화면
PNG 캡처가 SUCCEEDED했다. 따라서 실제 Windows desktop capture는 PASS다.

입력 검증용 임시 WinForms 창은 생성·inspect까지 확인했지만, 실제 click/type를 한 번에 실행하려던
호스트 요청이 Codex Native 자동 안전 검토에서 실행 전에 차단됐다. 임시 PID 6048은 cleanup complete로
종료했고 후속 `desktop.windows`에서 해당 창이 사라진 것도 확인했다. 따라서 실제 마우스·키보드 입력은
아직 PASS로 기록하지 않는다. 이후 RACP 개별 도구만 사용해 새 격리 시험 창을 다시 만들려 한
`process.spawn` 요청도 OpenAI가 요청의 보안 상태를 결정하지 못해 도구 실행 전에 차단했다. 이 요청은
원격 Agent에 dispatch되지 않았으며 다른 경로로 우회하지 않았다. 실제 141 원격 memory-read 인수시험도
실행 전 차단되어 수행되지 않았다.
상태와 Artifact ID는 `dist/two-pc-141-20261006-0.1.8-status.json`에 기록한다.
기존 두 PC 파일·실행·터미널·브라우저·Artifact 검증은 [실제 결과](two-pc-141-result.md)를 참고한다.
전체 배포·서비스·참조 OS·장기 실행 gate는 [release gate](windows-release-gates.md)에 남겨 둔다.

## 배포 파일과 최종 검사

포터블은 `dist/client-desktop-0.1.8-final/RACP Client-0.1.8-win.zip`, 설치 파일은
같은 폴더의 `RACP Client Setup 0.1.8.exe`다. 두 파일은 unsigned 개발 빌드다.
ZIP CRC와 Agent/EXE/ASAR의 4,789개 SHA-256를 검증했다.
패키지 EXE 0.1.8.0 실행, 별도 user-data 폴더 격리, 실제 내장 Python bridge 응답도 확인했다.
파일 크기·SHA-256·검증 및 pending 항목은 같은 폴더의 `build-manifest.json`에 있다.

최종 Client build, Node 10개, ruff와 mypy 133개 source를 통과했다.
Windows desktop 자체 창 시험은 2개 통과, 메모리 자체 프로세스 시험은 4개 통과다.
전체 Python 실행은 338 passed / 1 failed / 18 skipped였다.
실패한 커서 변조 시험은 무작위 커서가 이미 `x`로 시작할 때 시험 코드가 값을 바꾸지 않은 문제였다.
항상 다른 값을 만들도록 수정한 뒤 OAuth·메모리·설정·MCP Artifact·목록·브라우저 복구
관련 39개를 통과했다. 이 시험 코드 수정 후 전체 suite를 다시 실행한 결과로 표현하지 않는다.

현재 A의 lab Gateway 새 registry 적용용 사용자 실행 파일은
`.racp/two-pc-lab/Refresh-Gateway-0.1.8.ps1`이다. 확인한 PID/생성 시각/listener와
진행 중 작업 유무를 검사한 뒤 해당 lab Gateway만 교체한다. 이 문서 작성 시 실행하지 않았다.
2026-10-06 17:44 KST에는 오래된 미실행 `desktop.sessions` ACCEPTED 1건을 공개 operation API로
CANCELLED 처리한 뒤 이 스크립트가 성공했고, 8765/18765 listener와 141 epoch 6 재접속을 확인했다.
이전 `Restart-Codex-Mcp.ps1`은 이전 PID를 고정한 파일이므로 새 갱신에 사용하지 않는다.
Gateway 교체 후 메모리 도구가 보이지 않으면 Codex의 RACP MCP 연결을 갱신하거나 앱을 재시작한다.
