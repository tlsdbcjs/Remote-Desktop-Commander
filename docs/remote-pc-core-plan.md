# 원격 PC 핵심 사용 흐름과 구현 우선순위

2026-10-04 KST · 사용자 추가 지시 반영 · 전체 개발정의서 범위는 유지

제품의 핵심은 AI가 원격 PC에서 자료를 읽고 수정하고, 프로그램/명령을 실행하고,
여러 작업을 이어서 처리하는 것이다. Device는 사용자의 실제 PC이며 각 작업은 그 PC의
OS 계정/허용 폴더/승인 정책으로 실행한다. 리버싱은 이 기반 위의 확장 기능이다.
2026-10-06 사용자 정정에 따라 A의 작업 환경에서 B의 파일·프로세스·화면을 다루는
일반 원격 제어를 우선한다. 디버깅 예시는 특정 디버거 통합의 선행 요구로 해석하지 않는다.
[사용자가 제시한 Desktop Commander 흐름](https://desktopcommander.app/mcp/chatgpt/)처럼
파일과 지속 터미널, 실행 중 process와 결과를 채팅에서 사용할 수 있어야 한다.
링크의 제품 설명은 RACP 구현/실제 host 연결의 검증 증거를 대신하지 않는다.

## 핵심 인수 흐름

1. AI client가 인증된 Gateway MCP에 연결한다.
2. 등록된 원격 PC와 ONLINE/capability/허용 workspace를 확인하고 명시적으로 Device를 선택한다.
3. 해당 PC의 파일을 검색/읽기 → 수정/저장 → command/script 실행 → 결과 확인으로 이어서 작업한다.
4. 지속 terminal과 long-running Job을 사용하고, 출력 조회·취소·완료/UNKNOWN을 구분한다.
5. 큰 자료/결과는 인증된 Artifact 경로로 회수한다. GUI/browser가 필요한 작업은 해당 Device provider를 사용한다.
6. PC/Gateway 재시작·연결 단절 뒤 재접속하고, uncertain mutation은 자동 재실행하지 않는다.
7. PC 등록 해제와 권한 변경을 즉시 적용하고, 허용하지 않은 PC/폴더/작업은 거부한다.

## 다음 구현 순서

| 순위 / ID | 작업 | 완료 증거 |
|---|---|---|
| 1 / CORE-REMOTE-01 | 실제 HTTPS Gateway listener, outbound WSS Agent, Host/Origin·TLS 검증, CLI/Artifact/terminal의 동일 CA 설정 | 실제 TLS/MCP/file/shell/Artifact/terminal/reconnect 시험. 실제 서로 다른 두 PC 검증은 추가 gate |
| 2 / CORE-MCP-02 | ChatGPT/Codex 등 실제 AI client 연결, remote OAuth/metadata와 client 설정·연결 진단 | SDK client 외에 실제 host에서 Device 조회와 원격 파일 수정/실행 성공 |
| 3 / CORE-ONBOARD-03 | PC 등록·연결 확인·자격 증명 전달을 간소화하고, 사용 가능한 폴더/실행 권한을 명확히 표시 | 새 PC에서 설치/등록 → AI에 ONLINE 표시 → 첫 파일/명령 작업까지의 인수시험 |
| 4 / CORE-WORKSPACE-04 | 여러 허용 폴더/드라이브, workspace 선택·검색과 AI용 작업 환경 정보 | PC 밖/허용 폴더 밖 접근 거부와 허용 폴더 사이 실제 workflow |
| 5 / CORE-LIFE-05 | 백그라운드 Agent/사용자 세션·자동 시작·업데이트·doctor, 장기 작업과 연결 복구 사용성 | reboot/logon/업데이트/장애 뒤 원격 작업 복구, long-running 실제 인수시험 |
| 6 / CORE-E2E-06 | 실제 두 PC 및 실제 AI client에서 묶음 작업을 수행 | 파일 검색/편집/build 또는 자료 처리/Job/terminal/Artifact/취소·재연결 증거 |

기존 Phase 0–12와 보안/OS/패키징/soak gate를 삭제하거나 축소하지 않는다.
2026-10-05 사용자 추가 지시로 Windows·macOS·Linux 데스크톱 클라이언트를 배포 목표로 추가했다.
Node/npm/pnpm은 개발 도구이며 사용자 설치 패키지에는 필요한 런타임을 포함한다.
실제 native OS 빌드/실행과 installer lifecycle을 각각 검증한다(ADR-0023).
같은 날 사용자 최신 지시로 현재 배포·인수시험은 Windows를 우선하며 macOS/Linux는 후순위로 둔다.
Phase 9의 실제 GDB/Ghidra 기반은 보존하며 추가 adapter/확장 작업의 우선순위를 핵심 흐름 뒤로 둔다.

2026-10-06 후속의 구체적인 Windows 작업 순서·54개 시험·8개 display/session 조합·
실제 A→B 인수 시나리오·배포 종료 기준은
[작업 및 테스트 계획서 v1.0](RACP-Windows-작업및테스트계획서-v1.0.md)에 정리한다.

## 현재 구체적 진전

Gateway CLI에 --host/--public-origin/--tls-cert/--tls-key를 추가했다. non-loopback에는
TLS와 명시한 public origin이 필요하며 forwarded header로 TLS 신뢰를 만들지 않는다.
Agent/CLI/Artifact/terminal은 --ca-file 또는 enrollment에 저장한 CA 경로를 사용한다.
인증서/hostname 검증을 유지하며 verify=False 선택지는 제공하지 않는다.

실제 별도 Gateway CLI process와 Agent, 임시 private CA로 HTTPS MCP 파일 수정/읽기/명령 실행,
binary Artifact 왕복, WSS terminal과 Gateway 재시작 후 재연결을 검증했다.
미승인 CA, 다른 Host/Origin, owner API의 Device credential도 거부했다.
증거는 `tests/integration/test_remote_tls.py`와 `dist/remote-tls-results.xml`이다.
검증은 같은 물리 PC의 분리된 process이며 서로 다른 PC 또는 실제 ChatGPT host 연결의 PASS가 아니다.

filesystem은 PC가 직접 승인한 이름 있는 폴더를 지원하며 요청마다 workspace를 선택한다.
복사·이동은 목적지 workspace를 명시한다. [여러 폴더 안내](named-workspaces-guide.md).
remote MCP는 외부 provider OAuth를 사용하며, owner bearer는 loopback peer/origin으로 제한한다.
실제 Keycloak 로그인·동의·PKCE 후 MCP 파일 저장/명령 실행, 읽기 scope의 실행 거부를 검증했다.
[OAuth 설정과 실제 host 연결 절차](remote-mcp-oauth-setup.md)를 추가했다.
ChatGPT/Codex 실제 host 등록과 공개 배포,
PC 설치·자동 시작, 실제 두 PC 네트워크·참조 OS gate는 계속 남아 있다.

PC 등록에는 `racp-connect`와 Console 일회용 토큰 발급 화면을 추가했다.
저장한 Gateway/Device/폴더/profile/CA로 다른 작업 디렉터리에서도 Agent를 재시작한다.
실제 별도 Agent process의 HTTPS enrollment → outbound WSS → MCP 읽기 → 재시작 후 명시적
profile 선택 → 파일 저장/명령 실행을 확인했다. [PC 연결 안내](pc-connect-guide.md).
이 진전은 개발 환경의 등록/foreground 실행이며 clean PC 설치나 서비스 설치의 완료가 아니다.
만료된 임시 읽기 결과의 replay가 PC 재연결을 끊던 문제도 서버 감사 correlation 확인으로 수정했다.

여러 폴더 후속은 실제 MCP 저장/검색/복사·이동/실행/재시작과 CLI/Artifact/terminal·process cwd,
경계·승인·dedupe를 확인했다. 전체 Python **283 passed, 17 skipped**, Console **14 passed**.
[여러 폴더 결과](core-workspace-result.md). 다음 우선순위는 CORE-LIFE-05이며 실제 AI host와
두 PC, installer와 참조 OS gate는 미검증 상태를 유지한다.

2026-10-05 후속으로 사용자가 141 PC에 Windows client를 실행했고 실제 HTTPS/WSS ONLINE,
원격 파일/실행/ConPTY/Artifact·Job 취소를 확인했다. [두 PC 결과](two-pc-141-result.md).
Windows 0.1.1의 opt-in 로그인 후 Agent 연결도 구현해 native startup 등록/해제와 saved Agent
launcher 재시작을 확인했다. 실제 logoff/reboot·AI host와 전체 installer lifecycle gate는 남는다.

CORE-LIFE-05의 사용자 background 시작/상태/정상 종료를 구현했다. 실제 controller 종료 후
유지와 재연결, 중복 실행 방지, Windows crash UNKNOWN 복구/비재실행을 확인했다.
최신 전체 Python **290 passed, 17 skipped**, Console **14 passed**.
[background 결과](core-background-result.md). 자동 시작/서비스 설치/업데이트·backup·rollback,
장기 실행과 실제 AI host/두 PC gate를 계속 진행한다.

2026-10-05 Windows 후속: 0.1.2에 실제 Agent 현황/최근 활동과 트레이 완전 종료를 구현했다.
0.1.3에서는 current-user 설치·업그레이드·제거의 Agent 정리, 상태 hash 백업, 등록 정보 보존과
해당 설치 startup 제거를 검증했다. 긴 경로 bundle 파일의 제거도 실제 native 시험을 통과했다.
전체 Python **302 passed, 18 skipped**, Node **9 passed**, mypy **129 source files**.
[설치 수명 근거](adr/ADR-0026-windows-installer-maintenance.md). 실제 AI host/SCM/clean VM,
전체 migration·backup restore·rollback gate는 남는다. macOS/Linux는 사용자 지시로 후순위다.

같은 날 연결 방향을 재검토한 결과 Agent → Gateway의 outbound HTTPS/WSS를 유지한다.
0.1.4는 최초 등록 후 저장된 설정으로 현황·시작/중지/완전 종료를 사용한다.
토큰/인증서/네트워크 오류를 안전한 안내로 구분하고 등록 상태 확인 실패 시 재등록 form을 숨긴다.
실제 만료/소비 token 거부·새 등록·saved Agent 재실행과 자기 fixture credential 보존을 확인했다.
최신 전체 Python **307 passed, 18 skipped**, Node **10 passed**, mypy **129 source files**.
[등록·진단 근거](adr/ADR-0027-one-time-setup-and-safe-diagnostics.md).

0.1.5의 기본 최초 setup은 Gateway에서 받은 연결 파일 선택 → 로컬 폴더/profile 선택 → 등록이다.
주소·token·CA의 직접 입력을 기본 화면에서 제거하고 CA는 Agent state에 보존한다.
실제 private CA HTTPS/WSS의 등록·원격 작업·saved Agent 재실행과 파일 변조/만료 거부,
실제 Console 14개와 packaged preview를 확인했다. [연결 파일 근거](adr/ADR-0028-connection-file-onboarding.md).
새 옵션을 실제 140 Gateway에도 적용하고 readiness/Owner API 발급과 packaged preview를 확인했다.
141 업데이트·실제 AI host와 전체 개발정의서 gate는 미완료 상태를 유지한다.
최종 전체 Python **315 passed, 18 skipped**, Console **14 passed**, Node **10 passed**,
Windows/Linux mypy **131 source files**와 배포 ZIP 무결성을 확인했다.

2026-10-06 Client 0.1.6은 기존 PC 등록을 보존하는 로컬 설정 수정/복구를 구현했다.
핵심 설정 Python 17개와 실제 Electron HTTPS/WSS의 CA 유실 복구·Gateway 주소 변경·
동일 Device 재연결·원격 읽기·실행 중 변경 거부를 확인했다. [수정·복구 근거](adr/ADR-0029-local-settings-repair.md).
전체 gate 최초 실행의 Guardian readiness 실패는 별도 3개 시험에서 통과했으며,
최종 전체 Python **322 passed/18 skipped**, Node **10 passed**, mypy **132 source files**를
확인했다. Windows EXE/ZIP, packaged 실행과 ZIP CRC/4,790개 hash도 확인했다.
실제 141 새 버전, AI host 및 전체 배포/복원/부하 gate는 미완료다.
[Windows release gate](windows-release-gates.md)를 참고한다.

2026-10-06 Client 0.1.7은 설정 오류 시 **등록 정보 복구 / 상태 확인 / 완전 종료** 중간
화면을 제거하고 **등록 정보 편집** 폼으로 바로 진입한다. 정상 PC 설정에서도 같은 명칭을
사용한다. 0.1.6-final은 유지하고 0.1.7-final을 별도 배포한다.
