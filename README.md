# Remote AI Control Platform (RACP)

Python Gateway와 outbound Agent가 원격 작업을 실행하고 CLI/MCP가 동일한 실행 기록을
조회한다. **개발 중인 0.1.0이며 MVP/v1 완료 릴리스가 아니다.** 구현 기준은
[개발정의서 v1.1](docs/RACP-개발정의서-v1.1.md), 진행 현황은
[작업 현황](docs/implementation-status.md)에 기록한다.

제품의 핵심은 AI가 원격 PC의 자료를 읽고 수정하고, 명령/프로그램과 지속 터미널을 사용해
작업을 이어가는 것이다. 현재 우선순위와 실제 인수 흐름은 [원격 PC 핵심 계획](docs/remote-pc-core-plan.md)에 있다.

현재 실행 가능한 기능은 Device enrollment/owner 인증, heartbeat/reconnect/revoke,
정책/일회 승인, durable execution journal, 멱등 셸 실행, timeout/cancel, 최소 Job,
큰 출력 Artifact, 파일 조회/쓰기/복사/이동/삭제/해시/검색, 프로세스 조회/spawn/종료/대기,
CLI와 MCP Streamable HTTP다. 초기 profile은 read_only다.

격리 Chromium Browser Provider의 navigation/snapshot/폼 입력/screenshot/지속 Handle도
실행할 수 있다. Artifact 기반 Browser upload/download도 지원한다. loopback CDP 연결은 Agent의 명시적 opt-in을 요구한다.

## 개발 환경

Python 3.12.11과 uv 0.12.11을 사용한다. 종속성 patch는 `uv.lock`에 고정되어 있다.

```powershell
uv sync --all-packages --frozen
uv run python scripts/check.py
uv run python scripts/build.py
```

빌드는 `dist/`에 각 workspace package의 wheel과 SHA-256 manifest를 만든다.
서비스 설치 패키지와 signed release는 아직 제공하지 않는다.

## 로컬 실행

새 PC 등록은 Console의 장비 → 새 PC 연결에서 일회용 토큰을 발급한 뒤,
그 PC에서 `racp-connect --gateway https://... --workspace ...`로 진행할 수 있다.
기본 읽기 전용 설정과 Device credential을 함께 보호해서 저장하며 이후 `racp-agent`로 재시작한다.
개발본 준비, profile/CA와 복구 절차는 [PC 연결 안내](docs/pc-connect-guide.md)에 있다.
이 경로는 foreground Agent이며 signed installer/자동 시작은 아직 제공하지 않는다.
등록 후 `racp-agentctl start/status/stop`으로 [사용자 Agent의 백그라운드 수명](docs/background-agent-guide.md)을
관리할 수 있다. 현재 OS 계정으로 실행하며 서비스 설치/자동 시작 인수시험은 별도다.

Node/Python을 별도로 설치하지 않는 [데스크톱 클라이언트](docs/desktop-client-guide.md)도 제공한다.
Windows 개발용 설치 EXE/ZIP과 실제 등록·원격 작업·background 유지 시험을 확인했다.
Windows Client 0.1.8은 화면·마우스·키보드 허용 설정과 일반 프로세스 메모리 관측을 추가했다.
[사용·검증 범위](docs/remote-pc-windows-0.1.8.md)를 참고한다. 141의 새 memory capability와 Gateway
재접속, 실제 Windows desktop session/window/monitor와 화면 캡처까지 확인했다. 실제 마우스·키보드
입력과 remote memory-read 인수시험은 남아 있다.
macOS DMG/ZIP·Linux AppImage/deb target과 native CI를 구성했으며 실제 OS 실행/서명은 미검증이다.

프로젝트 폴더에서 owner를 한 번 초기화한다. Gateway 실행 전 수행하며 재초기화는
거부된다. owner/Device secret은 Windows DPAPI 또는 POSIX 0600 파일에 저장된다.

```powershell
uv run racp gateway init
uv run racp-gateway --enable-trusted-personal
```

별도 터미널에서 테스트 Device를 등록한다. 토큰을 history나 로그에 넣지 않고 같은
OS 사용자의 보호된 파일로 전달하는 로컬 예시다. 원격 Agent enrollment는 숨김 prompt
또는 `--token-stdin`을 사용할 수 있다.

```powershell
uv run racp device create-enrollment my-local-device
uv run racp agent enroll --token-file .racp/enrollment.bin
uv run racp-agent --workspace E:\MyWorkspace --profile trusted_personal
```

조회한 stable Device ID를 다음 명령에 사용한다. workspace는 기존 디렉터리여야 한다.
실행은 Agent OS 계정의 권한을 사용하므로 trusted_personal은 신뢰한 개인 Device에서
명시적으로 선택한다.

```powershell
uv run racp --json device list
uv run racp shell dev_REPLACE --key unique-command-001 --profile trusted_personal -- python --version
uv run racp doctor
```

승인 흐름은 `--profile standard`로 요청한 뒤 응답의 approval_id를 사용한다.
owner가 승인한 후 **동일 key/동일 payload**에 `--approval-id`를 추가하여 재요청한다.
approval은 동일 operation_id에 한 번만 소비된다. MCP tool에는 승인 권한이 없다.

```powershell
uv run racp approval get apr_REPLACE
uv run racp approval approve apr_REPLACE
uv run racp operation get op_REPLACE
uv run racp operation cancel op_REPLACE
```

`--job` 실행은 HTTP 202로 별도의 operation_id/job_id와 접수 상태를 반환한다.
결과는 Job ID로 조회한다. 기본 budget은 1시간, 상한은 24시간이며 queue 시간을 포함한다.
sync 상한은 120초, MCP sync 상한은 20초다. 취소 접수와 종료 확인을 구분한다.
원격 프로그램의 nonzero exit code는 JSON result에 보존한다. CLI exit code는 0 성공,
1 실행 실패, 2 입력 오류, 3 인증/정책 거부, 4 통신 불확정, 5 승인 필요다.

```powershell
uv run racp job get job_REPLACE
uv run racp job list --device-id dev_REPLACE
uv run racp job cancel job_REPLACE
```

WAITING에는 외부 event 사유가 있으며 승인 대기와 구분한다. UNKNOWN은 자동 재실행하지
않고 상태를 조사한다. [Job 검증 기록](docs/phase-5-job-result.md).

결과는 최소 24시간 보존한다. 만료된 mutation key는 기존 ID와 410을 반환하며 재실행하지
않는다. UNKNOWN 이후 늦은 결과는 별도 resolution에 남는다. 출력 업로드 실패 후에도
원래 실행 결과를 보존하고 자동 복구된 첨부를 별도로 조회한다.

```powershell
uv run racp operation resolutions op_REPLACE
uv run racp operation outputs op_REPLACE
```

셸 원본 stdout+stderr 수집 한도는 64 MiB다. 상한 초과는 작업 종료와 부분 결과를 반환한다.
inline UTF-8 text는 합계 64 KiB, 큰 출력은 Artifact로 제공한다.
[보존·출력 복구 검증](docs/phase-5-retention-output-result.md).

## 파일·프로세스

파일 경로는 선택한 Agent workspace 기준이다. 기본 폴더는 `default`이며
`--allow-workspace docs=D:\Documents`로 승인한 추가 폴더를 AI/CLI의 `workspace_id`/
`--workspace-id docs` 또는 Console에서 선택한다. [여러 폴더 안내](docs/named-workspaces-guide.md).
Windows junction/symlink·UNC·ADS는 기본 거부한다.
replace에는 overwrite를 명시하고, append에는 관측한 byte offset이 필요하다. 외부 writer와
원자적인 compare-and-swap은 보장하지 않으며 SHA-256/revision 조건과 RACP writer 직렬화를 사용한다.

```powershell
uv run racp fs list dev_REPLACE .
uv run racp fs read dev_REPLACE notes.txt
uv run racp fs write dev_REPLACE notes.txt --content-file local-notes.txt --key unique-write-001 --profile trusted_personal
uv run racp fs hash dev_REPLACE notes.txt
uv run racp fs read dev_REPLACE sample.bin --binary
uv run racp artifact download art_REPLACE local-sample.bin
uv run racp process list dev_REPLACE
uv run racp process spawn dev_REPLACE --key unique-spawn-001 --profile trusted_personal -- python worker.py
```

binary read는 Artifact ID를 반환하고 download는 SHA-256/size를 검증한 뒤 로컬 파일을 게시한다.
binary write는 먼저 업로드한 Artifact를 사용한다. 전송 중단 시 동일 transfer ID로 재개한다.

```powershell
uv run racp artifact upload dev_REPLACE local-input.bin
uv run racp artifact upload dev_REPLACE local-input.bin --resume-transfer-id trf_REPLACE
uv run racp fs write dev_REPLACE received.bin --artifact-id art_REPLACE --key unique-binary-001 --profile trusted_personal
```

입력은 Agent에서 전체 해시를 검증한 뒤 파일에 반영한다. create/replace/append의 조건은
텍스트 쓰기와 동일하다. Artifact 전송 및 오류 주입 결과는
[검증 기록](docs/phase-3-5-artifact-result.md)에 있다.
process spawn은 출력 discard 모드이며 지속 터미널은 별도 terminal provider를 사용한다.
terminate/wait/tree에는 관측한 pid, create_time, agent_boot_id를 사용한다. wait timeout은
프로세스를 kill하지 않는다. 강제 종료는 `--force`를 명시하며 managed spawn의 자식 tree도 정리한다.

Windows graceful 종료는 이용 가능한 GUI의 WM_CLOSE 또는 console group signal을 시도한다.
가능한 경로가 없으면 OPERATION_NOT_SUPPORTED를 반환하며 자동 force로 전환하지 않는다.

## 지속 터미널

```powershell
uv run racp terminal open dev_REPLACE --shell powershell --key unique-terminal-001 --profile trusted_personal
uv run racp terminal write dev_REPLACE term_REPLACE --data "Get-Location`r" --key unique-input-001 --profile trusted_personal
uv run racp terminal read dev_REPLACE term_REPLACE --cursor 0 --wait-ms 1000
uv run racp terminal resize dev_REPLACE term_REPLACE --cols 100 --rows 30 --key unique-resize-001 --profile trusted_personal
uv run racp terminal close dev_REPLACE term_REPLACE --key unique-close-001 --profile trusted_personal
```

다음 읽기에는 응답의 next_cursor를 사용한다. CURSOR_EXPIRED는 earliest_cursor와 lost_bytes를
반환하므로 새 위치를 명시적으로 선택한다. poll은 idle TTL을 늘리지 않으며 keepalive를 사용할
수 있다. Windows ConPTY는 stdout/stderr를 합친 terminal stream이다. long poll과
WS stream을 지원한다. WS stream은 독립 cursor와 소비 ACK를 사용하며 CLI로 이어서 볼 수 있다.
Console prototype은 아래에서 실행한다. [터미널 검증](docs/phase-4-terminal-result.md),
[WS 검증](docs/phase-4-stream-result.md).

```powershell
uv run racp terminal stream dev_REPLACE term_REPLACE --cursor 0
```

stream은 한 줄당 JSON event를 출력한다. stdout flush 후 ACK하며 최대 256 KiB만 미확인
상태로 보낸다. Ctrl+C로 구독을 종료하면 terminal은 유지된다. 재접속에는 마지막 처리한
next_cursor를 사용한다. gap 오류가 있으면 earliest_cursor에서 다시 볼지 명시적으로 선택한다.

## Browser

Python Playwright 1.63.0의 Chromium을 설치한다. workspace cache를 사용할 경우 Agent
실행 시 같은 PLAYWRIGHT_BROWSERS_PATH를 유지한다. 개인 Chrome profile은 사용하지 않는다.

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$pwd/.tools/playwright"
uv run python -m playwright install chromium
uv run racp browser open dev_REPLACE --key unique-browser-001 --profile trusted_personal
uv run racp browser navigate dev_REPLACE --browser-id browser_REPLACE --page-id page_REPLACE --url http://127.0.0.1:8000 --key unique-navigation-001 --profile trusted_personal
uv run racp browser snapshot dev_REPLACE --browser-id browser_REPLACE --page-id page_REPLACE
```

click/type/key/evaluate에는 직전 snapshot의 observation_id/navigation_revision을 사용한다.
selector는 role/name 또는 test_id JSON이고 element ref도 사용할 수 있다. navigation 또는
새 snapshot 후 이전 관측은 거부한다. CLI 키 입력은 --press-key, 멱등 키는 --key다.
evaluate는 mutation 권한을 요구하며 무한 루프/취소 시 해당 browser 전체를 종료한다.
읽기 조회는 1시간 idle TTL을 늘리지 않는다. 필요하면 keepalive를 명시적으로 호출한다.

Agent의 --browser-allow-origin을 반복 지정하면 page HTTP(S)/WS를 exact origin으로
제한한다. 미지정 시 실행 계정이 접근 가능한 HTTP(S) 내부망을 포함한다.
download는 관측에 묶인 element를 실제 클릭하여 Artifact를 게시한다. upload는
--artifact-id와 안전한 --filename으로 준비한 파일을 input에 설정한다. 서버 제출 결과는
다음 snapshot에서 확인한다. 승인되지 않은 자동 다운로드는 취소한다. 지연 파일 읽기를
위한 staging은 browser 수명 동안 유지하며 최대 16개다. profile/temp 파일은 Agent data
내부의 private 경로에서 관리하고 종료/재시작 시 소유권을 확인하여 정리한다.
frame 목록은 browser frames로 조회하고 snapshot/입력에 --frame-id를 지정한다.
frame 이동·제거 또는 다른 frame의 관측 참조는 거부한다.

CDP를 사용할 Agent는 --enable-browser-cdp로 시작한다. browser cdp_targets의
--endpoint-url로 loopback target ID를 조회하고 browser attach로 연결한다. 기본은
새 격리 context이며, existing 모드는 --page-target-ids JSON 배열로 page를 직접 선택한다.
--allow-page-termination은 선택 page를 작업 정리 때 닫도록 허용하며 기본은 detach다.
기존 context의 download는 지원하지 않고 evaluate/upload에는 종료 권한이 필요하다.
외부 browser 프로세스와 선택하지 않은 page는 정리 대상이 아니다.
Browser 상태 이벤트는 metadata만 ACK/replay/gap 경로로 전달한다. runtime 파일이 없으면
browser.open을 광고하지 않으며 Device의 다른 기능은 유지한다. 파일 존재 health와 실제
launch 성공은 별도로 검증한다. [Browser 검증 기록](docs/phase-7-browser-result.md).

## MCP와 네트워크

로컬 endpoint는 `http://127.0.0.1:8765/mcp/`다. loopback peer/origin에서 owner bearer를 사용하며
원격 MCP는 외부 OAuth provider의 검증된 토큰을 요구한다. Device credential은 MCP에 사용할 수 없다.
HTTP/WSS 내부 메시지는 MCP와 독립적인 RACP Protocol 1이다.
기본 실행은 loopback이며, 명시적 HTTPS listener로 원격 Agent의 outbound WSS와 remote MCP를 제공한다.
TLS/Host/Origin/CA 설정과 실행 명령은 [원격 TLS 결과](docs/phase-1-remote-tls-result.md)에 있다.
원격 MCP의 issuer/JWKS/subject/client/scope 설정과 host 연결 절차는
[OAuth 설정](docs/remote-mcp-oauth-setup.md)에 있다. OAuth 미설정 원격 MCP는 503으로 차단한다.
실제 Keycloak의 로그인·동의·PKCE 인증과 MCP 파일 저장/명령 실행을 로컬에서 검증했다.
ChatGPT OAuth와 실제 host 연결·공개 배포는 후속 구현 범위다.
Agent는 loopback 밖의 평문 URL, 토큰 query, TLS 검증 우회를 허용하지 않는다.

MCP SDK client에서 실제 command까지의 테스트는 통과했지만 Codex/ChatGPT host 연결은
아직 UNVERIFIED다. Windows 11/Ubuntu 참조 runner와 GUI/RE 환경도 별도 검증이 필요하다.

## Console prototype

Node 22.23.0과 pnpm 11.19.0을 사용한다. Windows에서는 workspace 안에 portable Node를
설치하고 같은 runtime으로 frozen dependency와 production build를 만들 수 있다.

```powershell
uv run python scripts/bootstrap_node.py
$node22 = (Resolve-Path .tools/node-v22.23.0-win-x64/node.exe).Path
$env:PATH = "$(Split-Path $node22);$env:PATH"
& $node22 .tools/node-v22.23.0-win-x64/node_modules/corepack/dist/pnpm.js install --frozen-lockfile
uv run python scripts/console_build.py --node $node22
uv run racp console setup
```

Gateway 실행 후 [Console](http://127.0.0.1:8765/console/)에서 CLI가 발급한 일회성
setup secret을 입력한다. 유효 기간은 5분이고 한 번만 교환된다. owner token을 브라우저에
입력하거나 localStorage에 저장하지 않는다. Console 세션은 유휴 30분·절대 12시간이다.
SSE 단절 중에는 마지막 관측 상태를 표시하며, 승인/취소 후 종료 상태를 별도로 조회한다.

```powershell
uv run python scripts/console_e2e.py --node $node22
```

이 명령은 임시 workspace, 실제 별도 Agent와 headless Chromium으로 인수시험을 실행한다.
Chromium 설치가 필요한 경우 `pnpm --dir apps/console exec playwright install chromium`을
먼저 실행한다. `PLAYWRIGHT_BROWSERS_PATH`를 workspace `.tools/playwright`로 설정한다.
목록은 owner별 keyset pagination/filter를 제공한다. 파일은 원본 다운로드와 제한된
텍스트/PNG/JPEG preview를 제공하고, terminal은 cookie 인증 WS viewer로 관측한다.
상세 doctor/session 관리 일부는 diagnostic JSON이다.
[Console 검증 기록](docs/phase-6-console-result.md).

## Windows Broker 개발 상태

별도 `racp-session-broker` entry point와 Named Pipe 인증·입력 lease/관측 경계 기반이 있다.
Agent에 --desktop-session-id를 명시해 해당 foreground 계정/세션의 Broker를 활성화한다.
desktop sessions/windows/monitors/screenshot/lease_acquire/lease_renew/lease_release/activate/
move/click/type/key/scroll을 CLI/HTTP/MCP 경로로 제공한다. 입력은 lease와 직전 관측을 요구한다.
drag는 입력 해제 Guardian이 준비된 세션에서 제공한다.
inspect/invoke/set_value는 UI Automation 관측/ref와 Invoke/Value pattern을 사용한다.
사용자 입력·foreground 변화 hook과 OS 세션별 mutex로 입력 lease를 중단한다.
원본·preview는 private Artifact이며 MCP는 제한된 PNG image content를 반환한다.
실제 자체 창에서 HTTP drag와 Broker Job 종료·Agent crash·RPC/operation 취소·Device revoke 뒤
Ctrl/button 해제, release gate와 임시 task/config 정리를 검증했다. SCM/다른 계정과 나머지
GUI/DPI 인수시험은 남아 있다.
[Broker 설계와 범위](docs/adr/ADR-0010-local-session-broker-and-input-fences.md).
[UI Automation/입력 hook 계약과 검증](docs/adr/ADR-0012-ui-automation-and-input-watch.md).
[Broker Job 종료 후 입력 해제 Guardian](docs/adr/ADR-0013-independent-input-release-guardian.md)은
foreground와 로그온 등록 경로에 연결했다. 같은 계정의 실제 로그온 등록/재시작을 검증했으며,
SCM/다른 계정·자동 시작 설치와 전체 인수 gate는 남아 있다.

로그온 사용자 Broker는 Agent의 --desktop-login-user-sid로 허용한 SID가
racp-login-broker --endpoint-file <Agent data/brokers/login-endpoint.json>으로 등록한다.
SCM에서는 실제 token에 포함된 --service-sid를 지정해야 한다. 기본 Agent를 LocalSystem으로
바꾸거나 WTS user token을 받지 않는다. racp-agent-service는 준비된 service config를 사용하는
SCM host이며 실제 설치/계정/자동 시작 provisioning은 후속 범위다.
[등록 설계와 검증 범위](docs/adr/ADR-0011-nonadmin-agent-and-user-logon-broker.md).

## RE plugin 개발 상태

Agent package의 `plugins`에 로컬 allowlist manifest와 stdio subprocess supervisor 기반을 추가했다.
승인 SHA-256/permission, JSON-line 한도, health, generation fencing, restart/backoff와 소유 process 정리를
검증했다. `--plugin-config <절대 local 경로>`의 manifest hash/permission을 통과한 backend가 준비되면
Agent/CLI/MCP의 고정 RE operation으로 노출한다. query/command는 action별 tagged payload이며,
CLI에는 `--payload-json`, MCP에는 typed `payload`를 사용한다. synthetic domain contract 외에 실제 GDB/MI
launch/breakpoint/stop/register/memory/backtrace/close와 plugin crash 뒤 target 정리/Agent 생존을 검증했다.
현재 GDB adapter는 main symbol을 요구하며 attach와 External MCP mapping은 후속 구현이다.
실제 Ghidra 12.1.4의 정적 분석, 함수/문자열/xref/disassemble/decompile와 Unicode rename/comment 저장도
자체 fixture로 검증했다. Windows Job 기반이며 Linux containment와 database 재사용/보존 관리 gate는 남아 있다.
Ghidra 설정과 검증은 [ADR-0017](docs/adr/ADR-0017-native-ghidra-headless-analysis.md)에 기록한다.

설치된 GDB의 절대 경로와 정확한 version으로 새 private approval directory를 만든다.
manifest와 backend executable SHA-256을 고정하고 config/provenance를 생성한다. 이후 Agent에 생성한
`plugin-config.json`을 지정한다. GDB executable은 별도로 설치하며 개발 wheel에 포함하지 않는다.

```powershell
uv run python -m racp_agent.plugins.gdb_installation --directory E:\RACP\gdb-approval --gdb C:\Tools\gdb\bin\gdb.exe --backend-version 17.1
uv run racp-agent --workspace E:\MyWorkspace --plugin-config E:\RACP\gdb-approval\plugin-config.json
```

현재 검증한 GDB build는 UTF-8 path/argv를 지원하지 않아 비ASCII 입력을 거부한다.
이 build에서는 target snapshot이 위치할 Agent data 경로도 ASCII로 지정해야 한다.
`debugger.info`의 `utf8_arguments_supported`로 build 지원 여부를 조회할 수 있다.
지원 환경과 남은 인수 항목은 [호환성 표](docs/compatibility.md)에 유지한다.
[계약과 신뢰 경계](docs/adr/ADR-0014-allowlisted-plugin-supervisor.md),
[현재 검증과 남은 요구사항](docs/phase-9-plugin-result.md).
