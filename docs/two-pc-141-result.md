# Windows 두 PC 핵심 인수시험

2026-10-05 KST · 실제 두 PC 파일/실행/터미널 PASS · 전체 목표는 진행 중

사용자가 192.168.29.141에서 Windows desktop client 0.1.0을 설치하고 직접 등록·시작했다.
192.168.29.140의 private CA HTTPS/WSS Gateway가 해당 Device의 ONLINE을 확인했다.
RDP/WMI/Windows 로그인 비밀번호 없이 outbound Agent와 RACP 프로토콜을 사용했다.

| 항목 | 실제 증거 |
|---|---|
| Device | dev_9adcb9a37d424f089fe5f153534a6f9f |
| 호스트 PC | GhostShell-Develop / 192.168.29.140 |
| 원격 PC | DESKTOP-06NU139 / 사용자 지정 peer 192.168.29.141 |
| 실행 계정 / profile | GhostShell / standard |
| 원격 cwd / 승인 폴더 | E:\01_RE-Lab\01_Project |
| 시험 전용 하위 폴더 | racp-acceptance-cfe2401065274370aea2b7cdaa831d7e |
| 연결 | 실제 ONLINE, connection epoch 1, file/shell/ConPTY healthy |

`scripts/two_pc_acceptance.py`는 명시한 Device와 표준 profile로 자기 시험 폴더만 생성한다.
승인 필요 요청은 자기 요청이 발급한 approval_id를 owner API에서 승인하고 같은 key로 재요청한다.
일반 파일이나 실행 중인 다른 프로그램은 수정·종료하지 않는다.

실제 확인한 항목:

- 시험 폴더와 한글 UTF-8 파일 생성, 내용 읽기, SHA-256 일치, 파일 복사.
- 141의 PowerShell 명령 실행과 원격 hostname/cwd 확인. 호스트와 다른 hostname이다.
- 같은 key의 실행 요청 재전송, 결과 일치와 counter.txt 값 1로 side effect 한 번 확인.
- 실제 ConPTY 지속 PowerShell terminal 열기/입력/read cursor와 marker 출력 확인.
- 자기 terminal만 close하고 cleanup_status=complete 확인.
- 후속 시험에서 binary file read의 Artifact를 인증된 HTTPS로 다운로드하고 UTF-8 bytes/SHA-256 일치.
- 자기 PowerShell 장기 Job의 PID 게시 뒤 cancel, CANCELLED/cleanup complete와 실제 PID 종료 확인.

원본 결과는 `dist/two-pc-141-acceptance.json`에 operation/approval ID와 결과를 기록했다.
owner/Device token은 기록하지 않았다. 파일은 원격 시험 폴더에 남겨 사용자가 확인할 수 있다.
후속 Artifact/Job 결과는 `dist/two-pc-141-extended.json`에 기록했다. 해당 시험 폴더는
`racp-acceptance-57761ef26fb541eb8f7b9fb6a3145c6a`다. 작은 파일의 실제 Artifact 전송이며
원격 100 MiB disconnect/resume 시험을 대신하지 않는다.
첫 시험은 읽기 결과의 text 필드를 content로 검사한 시험 코드 오류로 중단했다.
필드를 실제 계약에 맞추고 다른 고유 시험 폴더에서 전체 위 흐름을 통과했다.

이 결과는 같은 PC의 합성 Device 시험이 아닌 실제 두 Windows PC의 핵심 흐름이다.
실제 AI host의 OAuth MCP 등록/tool call, 대용량 Artifact/연결 단절·PC reboot,
installer upgrade/rollback/제거/자동 로그온 인수시험과 전체 Phase 0–12 gate는 별도로 남는다.


## 2026-10-06 · 실제 Client 0.1.7 재시험

사용자가 141의 Agent를 시작한 뒤 같은 Device ID의 ONLINE/epoch 2를 확인했다.
원격 실행 Client의 ProductVersion은 0.1.7.0이고 EXE와 app.asar의 SHA-256이 현재
0.1.7 portable ZIP의 해당 파일과 일치했다. 프로토콜 hello의 agent_version은 Python
package의 고정 0.1.0이므로 이를 GUI 배포 버전 확인의 근거로 사용하지 않았다.

기본 인수시험은 원격 hostname DESKTOP-06NU139, cwd E:\01_RE-Lab\01_Project에서
한글 파일 생성/읽기/복사/hash, 인증된 binary Artifact 다운로드, 같은 mutation key의
counter=1, 소유 Job cancel/CANCELLED/cleanup complete/PID 소멸, ConPTY 입력/read/close를
통과했다. 시험 폴더는 racp-acceptance-d319ae1787ba4847b5ce802bb8ff8e26이며 작은 검증 파일은 남겨 두었다.

확장 시험은 소유 process.spawn/inspect/tree, wait timeout 후 생존, 잘못된 create_time으로
종료 거부, 올바른 identity의 종료/PID 소멸을 확인했다. 별도 임시 headless browser의
about:blank에 시험 DOM만 생성하여 한글 type/click 결과와 PNG Artifact의 SHA를 확인하고
browser.close/cleanup complete로 정리했다. 외부 사이트와 기존 사용자의 browser profile을
사용하지 않았다. PNG는 원격 browser 시험 페이지이며 Windows desktop screenshot이 아니다.

100 MiB 시험은 controller→Gateway의 실제 HTTPS upload를 4 MiB commit 뒤 다음 chunk
중간에서 끊었다. 미완료 chunk가 commit되지 않음을 확인하고 같은 transfer를 SDK로
resume했다. Artifact로 141의 전용 폴더에 binary write하고 원격 SHA-256을 비교했다.
141의 binary read 결과를 controller에서 8 MiB 다운로드 후 끊고 Range/If-Range의 HTTP
206으로 재개하여 전체 크기/해시를 확인했다. 원격 큰 파일은 expected_revision을 지정해
삭제했다. source/다운로드의 controller 임시 파일도 정리했다. Gateway의 시험 Artifact는
정상 retention 대상이다. 원격 Agent의 WSS를 끊거나 PC를 reboot한 시험은 아니다.

마지막 private local control status는 RUNNING/connected=true였다. 조회 자체의 shell
operation 1개를 제외한 다른 active operation은 없었다. 140:8765의 실제 established
TCP 상대 주소 192.168.29.141도 확인했다. Agent를 종료하거나 PC의 network를 변경하지 않았다.
보조 status query의 첫 실행은 shell 환경에서 APPDATA가 제거된 계약을 놓쳐 실패했다.
Windows Known Folder API로 경로를 얻도록 시험 probe를 수정한 뒤 위 상태 확인을 통과했다.
제품 실행 환경 allowlist를 완화하지 않았다.

증거:

- dist/two-pc-141-20261006-core.json
- dist/two-pc-141-20261006-extended.json 및 PNG (process/browser)
- dist/two-pc-141-20261006-large.json 및 PNG (100 MiB 포함)
- dist/two-pc-141-20261006-final-status.json

이 최초 두 PC 시험 당시 Windows desktop capability는 DESKTOP_NOT_CONFIGURED로 비활성이었다.
이 구간의 결과는 Owner API를 사용한 실제 두 PC 시험이며 이후 AI host OAuth MCP/desktop 검증은
아래 2026-10-06 후속 기록에서 별도로 다룬다. 전체 원문 제품 gate와 reboot/upgrade/rollback/복원/soak는 계속 남는다.

## 2026-10-06 · 재연결 및 Codex OAuth 준비

실제 LAN Gateway를 OAuth 설정으로 재시작한 뒤 141 Agent가 같은 boot ID로 epoch 2에서
3으로 재연결했다. 기존 shell mutation을 같은 key/request로 제출했을 때 같은 operation ID와
보존된 결과를 반환했고 원격 counter는 1이었다. 증거는
`dist/two-pc-141-20261006-reconnect.json`이다.

사용자가 승인하고 Windows 최종 확인 창을 직접 수락한 시험 CA는 CurrentUser/Root에
등록됐다. Thumbprint는 7F5C39FCCE56C4EB2DF761C1D7DD35EAFAEC2D28이고 만료는
2026-10-12 02:19:21 KST다. 시스템 전체 저장소에는 추가하지 않았다.

Codex 0.158.0의 native OAuth login은 별도 임시 Gateway의 local HTTPS MCP 접점에서
PKCE/consent와 OS keyring 저장까지 통과했다. 같은 process에서 LAN API와 local MCP를
제공하는 기능을 추가했으며 인증 없는 MCP 401, local 관리 API 403, LAN MCP 403도
실제 TLS listener에서 확인했다. 증거 `dist/codex-local-mcp-20261006.json`은 임시 Gateway
시험이며 141의 실제 Codex 앱 tool call 성공을 의미하지 않는다. 임시 listener/process는
소멸을 확인했다. 종료 직후 SQLite shm 삭제 경합으로 임시 폴더 일부가 남았다.
잔여 폴더 삭제 명령도 자동 승인 검토가 거부하여 보존했다.

local ingress/origin/peer/socket와 기존 OAuth/network 관련 unit 36개, connection import 및
remote TLS 10개가 통과했다. 기존 141 Gateway에 새 listener를 적용하는 재시작은 자동
승인 검토에서 `blocked by policy`로 거부됐다. 적용용
`.racp/two-pc-lab/Restart-Codex-Mcp.ps1`을 준비했고 실제 앱 MCP tool call gate는 미완료다.

전체 회귀는 `uv run --frozen pytest -q`에서 332 passed / 18 skipped,
468.94초로 완료했다. 전체 검사 수집 이후 추가한
`tests/integration/test_local_mcp_tls.py`는 별도 실행에서 1 passed / 3.61초다.
두 실제 TLS socket의 같은 control plane에 WSS fixture Agent를 연결하고 local OAuth
MCP로 Device 조회와 한글 파일 읽기를 확인했다. 유효한 서명을 가진 잘못된 audience
token은 401로 거부했다. 이 fixture는 실제 141 또는 Codex 앱 시험을 대체하지 않는다.
fixture의 첫 실행은 Windows write_text의 CRLF 변환 때문에 기대 LF와 달라 실패했다.
입력 fixture를 UTF-8 bytes로 생성하도록 수정했으며 제품의 파일 읽기 결과를 변환하지 않았다.

## 2026-10-06 15:21 KST 이후 · 실제 Gateway 적용과 native Codex 초기화

사용자가 `Restart-Codex-Mcp.ps1`을 직접 실행했다. 실제 Gateway PID 29004가 LAN
192.168.29.140:8765와 local 127.0.0.1:18765를 함께 listen했고 141은 같은 boot ID로
epoch 4 / ONLINE 상태에 재연결했다. protected-resource metadata의 resource와 issuer는
각각 local MCP와 owned loopback Keycloak 주소다. 익명 MCP 및 owner bearer의 MCP 접근은
401, local owner API와 LAN MCP는 403이었다. 증거는
`dist/codex-141-gateway-20261006.json`이다.

Codex native app-server의 metadata 초기화 시험에서 오래된 access token의 refresh가
`invalid_scope: racp.execute racp.read offline_access`로 실패했다. 최소 Keycloak fixture가
`offline_access`를 client의 optional scope에 연결하지 않은 원인이었다. owned test realm에
scope, fixture user의 해당 role과 scope mapping을 추가하고 다시 PKCE/consent 로그인했다.
실제 Windows 계정 비밀번호와 다른 사용자 계정은 사용하지 않았다. 시험 provider의
access token lifetime을 60초로 설정해 갱신을 검증했다. 신규 생성 helper의 기본은 900초다.

별도 `codex app-server`에서 initialize → initialized → mcpServerStatus/list를 호출했을 때
oAuth / initialized=true / tools=84를 확인했다. 이 checker는 thread 또는 model turn을
시작하지 않는다. 첫 초기화 결과 파일 생성 후 69.4초가 지난 뒤 같은 checker를 재실행해
동일 결과를 확인했다. 원래 60초 access token으로는 불가능하므로 native token refresh
경로를 포함한 재연결이 통과했다. 실패 및 전후 증거는
`dist/codex-mcp-native-startup-20261006-before-fix.json`,
`dist/codex-mcp-native-startup-20261006-initial.json`,
`dist/codex-mcp-native-startup-20261006.json`이다. stdout에는 token을 기록하지 않는다.

현재 대화의 MCP catalog에는 여전히 RACP가 없고 resources/list도 unknown server를
반환했다. 사용자의 MCP 비활성화/활성화 뒤에도 같은 상태였으므로 앱 완전 재시작을
요청했다. 별도 native 초기화는 실제 현재 대화의 141 tool call/HOST-01 PASS로 확대하지 않는다.

## 2026-10-06 · 현재 Codex 대화의 실제 141 MCP 시험

사용자가 앱을 완전히 재시작한 뒤 현재 대화의 tool catalog에 RACP 84개가 나타났다.
`mcp__racp__device_list`로 ONLINE/epoch 4의 정확한 141 Device를 선택하고 모든 file/shell
작업에서 default workspace E:\01_RE-Lab\01_Project를 지정했다. HTTP SDK로 원격 호출을
대체하지 않고 현재 대화의 first-class RACP MCP 도구를 사용했다. standard profile이 요구한
승인은 이 시험에서 직접 생성한 approval ID만 기존 owner API로 승인했다.

기존 own fixture의 한글 파일을 읽고 `racp-codex-1791268496476` 폴더에 새 한글 파일을
저장했다. 읽은 content와 SHA-256이 controller 기대값과 같았다. 원격 PowerShell의 hostname은
DESKTOP-06NU139, cwd는 허용 폴더였다. 동일 shell key 두 호출의 operation ID가 같았고
실제 원격 counter 파일도 1이었다. Job은 RUNNING 조회 → CANCEL_REQUESTED → CANCELLED와
cleanup complete를 확인했다. 소유 PID의 process.inspect는 예상한 PROCESS_NOT_FOUND로
프로세스 소멸을 증명했다. 처음 probe는 alive=false 응답을 기대하여 assert가 실패했지만
제품 응답은 올바른 명시적 부재 error였다. 이를 예상 negative result로 기록했다.

별도 headless browser의 own about:blank 시험 DOM에서 한글 type/click 결과와 MCP 응답의
inline PNG를 실제 대화에서 받았다. 최초 DOM fixture의 inline onclick 따옴표가 잘못돼
click 결과 기대값이 실패했다. 해당 browser를 cleanup complete로 닫고 handler를 DOM property로
배정한 새 fixture로 재검증했다. 정상 결과 후 두 번째 browser도 cleanup complete로 닫았다.
이 PNG는 원격 browser page이며 Windows desktop capture가 아니다. 보관 PNG의 binary Artifact
다운로드는 별도로 owner API/SDK의 크기/hash 검증을 사용했으며 OAuth MCP image 미리보기와
인증 경로를 구분한다. 사용자의 기존 browser/profile/site에는 접근하지 않았다.

ConPTY를 열어 분리된 marker 문자열을 실행·read한 뒤 cleanup complete로 닫았다. 마지막
자기 status query에서 Agent RUNNING/connected=true/epoch 4를 확인했고 다른 active operation은
없었다. token lifetime 60초를 넘어 실제 대화의 MCP 호출이 계속 성공해 앱의 자동 갱신 경로도
포함됐다. 작은 시험 파일과 counter/PID 파일은 own fixture 폴더에 남겼다.

증거는 `dist/codex-chat-141-20261006.json`, `.png`, `-final-status.json`이다.
현재 Codex 앱의 실제 PC 도구 연결은 통과했다. ChatGPT, 운영 provider/public 인증서와 native
Windows desktop/session 입력·capture, 전체 배포 gate는 이 결과로 통과 처리하지 않는다.

## 2026-10-06 · Windows Client 0.1.8 후속 재접속

사용자가 141에서 새 Agent를 실행한 뒤 현재 대화의 `device_list`에서 정확한 Device가
ONLINE/epoch 5로 나타났고, process capability가 `process.memory_regions`와
`process.memory_read`를 포함했다. Gateway 교체 전에 남아 있던 `desktop.sessions` 조회 1건은
15:48 KST부터 `ACCEPTED`에 머물렀으며 operation API로 확인한 결과 실제 dispatch 전 상태였다.
이를 `CANCELLED`/`execution_state=not_started`로 정리한 뒤 준비된
`.racp/two-pc-lab/Refresh-Gateway-0.1.8.ps1`을 실행했다.

새 Gateway는 192.168.29.140:8765와 127.0.0.1:18765를 다시 listen했고, 141은 같은
`agent_boot_id`로 epoch 6에 자동 재접속했다. 별도 native Codex app-server 시작 검사는 OAuth
RACP 86개 도구를 초기화했으며 `process_memory_regions`, `process_memory_read`가 실제 tool catalog에
포함됐다. 현재 대화에서도 재접속 뒤 새 `process.list` operation이 SUCCEEDED해 일반 MCP 실행 경로가
계속 동작함을 확인했다.

그 뒤 사용자가 141의 화면 허용 설정을 저장하고 Agent를 다시 시작했다. Device는 새 boot ID
`boot_0ffb8e896928407ba8fce44818b9c684`, epoch 7로 ONLINE/healthy이며 desktop capability도
enabled/healthy다. session 1 Broker와 UI Automation/input Guardian이 준비됐고 DISPLAY1
3840x2160, scale 1.5에서 sessions/monitors/foreground/windows와 실제 전체 화면 screenshot이
SUCCEEDED했다. full Artifact는 `art_921dbefdb4bd46c187e1a947140d2e47`, preview는
`art_c126c00174354043bec3c8181fa4c81e`이다.

입력 검증용 임시 WinForms 창의 생성·UIA inspect까지는 성공했으나 click/type를 묶어 실행하려던
Codex Native host 요청이 자동 안전 검토에서 실행 전에 차단됐다. 임시 프로세스 PID 6048은
`op_82174d844b91499ab552bc2f9df28592`로 cleanup complete 종료했고, 후속 `desktop.windows`
`op_5d136b17dede472697ad2f5f73dfac77`에서 시험 창이 없는 것을 확인했다. 따라서 desktop capture는
PASS지만 실제 desktop input은 아직 PASS가 아니다. 이어서 RACP 개별 `process.spawn`으로 새 격리
시험 창을 만들려 한 재시도도 OpenAI가 요청의 보안 상태를 결정하지 못해 도구 실행 전에 차단됐다.
원격 Agent에는 dispatch되지 않았고 우회 실행하지 않았다. 실제 141 원격 process memory-read 인수시험도
실행 전 차단되어 미검증 상태를 유지한다. 비밀값을 제외한 상태 증거는
`dist/two-pc-141-20261006-0.1.8-status.json`이다.
