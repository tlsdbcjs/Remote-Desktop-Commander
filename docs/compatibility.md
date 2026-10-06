# 런타임/지원 검증표

2026-10-05까지 로컬 실제 환경이다. 설계의 Windows 11/Ubuntu 참조환경과 구분한다.

| 항목 | 고정/관측 값 | 상태 |
|---|---|---|
| Python | CPython 3.12.11 Windows x64 | 로컬 실행 검증 |
| uv | 0.12.11 | frozen sync/build 검증 |
| MCP Python SDK | 2.0.0 | 실제 Streamable HTTP SDK client 검증 |
| OAuth provider / JWT | Keycloak 26.8.0 image digest 고정 / PyJWT 2.15.1 | 실제 HTTPS 로그인·동의·PKCE S256 후 MCP 파일 저장/명령 실행·scope 거부 확인. predefined test client이며 실제 AI host/DCR/CIMD/공개 배포 UNVERIFIED |
| Windows | Windows 10 Pro x64 10.0.19045 | 로컬 shell/Job Object/DPAPI 검증 |
| Windows 11 x64 | 설계 참조환경 | UNVERIFIED |
| Ubuntu 24.04 x64 | 설계 참조환경, CI matrix 작성 | UNVERIFIED (CI를 실행한 것으로 간주하지 않음) |
| Node | Console 22.23.0 portable Windows x64 / 기존 global 24.12.0 | checksum 검증과 실제 Console build |
| pnpm | 11.19.0 | frozen install 검증, pnpm-lock.yaml 고정 |
| React / TypeScript / Vite | 19.3.0 / 5.9.3 / 8.3.2 | 실제 browser/build, OpenAPI generator peer와 호환되는 TS 5 사용 |
| Playwright/browser revision | 1.63.0 / Chromium 1243, 153.0.8010.12 | Windows 10 Console E2E와 Python Browser Provider 실제 실행. 다른 browser/OS UNVERIFIED |
| Pillow | 12.3.0 | native DIB PNG/preview와 MCP image 전송 개발 검증 |
| comtypes | 1.4.17 | 자체 실제 창 UIA tree/Value/Invoke, password/foreground 거부와 HTTP/MCP 검증 |
| jsonschema / referencing | 4.26.0 / 0.37.0 | allowlisted plugin input/output 검사와 remote schema retrieval 차단. types-jsonschema 4.26.0.20260518은 개발 의존성 |
| Windows Broker | foreground·로그온 등록 같은 계정/세션 | pipe/Job/로그온 등록·재시작/합성 전송·취소, 자체 GUI/150% DPI/외부 주입 거부/OS mutex, Guardian Job kill/hold timeout/Agent crash/RPC·HTTP cancel/revoke 뒤 입력 해제·임시 task/config 정리와 공개 HTTP drag 검증. SCM/다른 계정/자동 시작 설치/나머지 DPI·RDP·UAC UNVERIFIED |
| Codex/ChatGPT 실제 host | 미연결 | UNVERIFIED |
| Ghidra | 12.1.4 / Oracle JDK 22.0.2 x64 | Windows 10 실제 RACP HTTP 분석/query/Unicode mutation/close, plugin Job crash와 Java 종료/core 유지. Linux containment 미구현·capability 거부, database 재사용/retention·전체 release gate 남음 |
| IDA/x64dbg | 미연결 | UNVERIFIED |
| GNU GDB | 17.1 x86_64-w64-mingw32 | 실제 RACP HTTP launch/breakpoint/stop/register/memory/backtrace/close와 plugin Job crash/core 유지 검증. UTF-8 charset/expat XML 미지원 build, 비ASCII argv 거부. attach·참조 OS·전체 release gate 미검증 |
| Java | Oracle Java / javac 22.0.2 | Ghidra 12.1.4 실제 headless 분석 검증. 다른 JDK/OS 미검증 |
| RE fixture compiler | portable LLVM-MinGW 20260908 / LLVM 23.1.1 | 공식 asset SHA-256 확인, workspace만 설치. DWARF 4/O0 C fixture와 source/target/argv manifest 생성 |
| Plugin/RE application | 로컬 승인 manifest/stdio·Agent·fixed registry | Windows protocol 수명과 synthetic domain fixture의 HTTP/MCP/CLI/Handle/Artifact, 실제 GDB와 Ghidra RACP adapter 검증. External MCP/durable event/storage gate 남음 |
| 원격 TLS | 직접 HTTPS/WSS / 명시적 public origin / 추가 CA | 실제 Gateway CLI 별도 process의 MCP file/shell·Artifact·WSS terminal·재연결/인증 거부 검증. 로컬 실제 provider OAuth 후속 통과. 두 PC/실제 AI host·공개 OAuth·IPv6·proxy gate 남음 |
| PC 등록 | racp-connect / 보호된 AgentSettings / foreground resume | 새 실제 Agent process의 HTTPS 등록·WSS·MCP 자료 읽기/저장/명령·재시작과 Console 토큰 1회 사용/메모리 제거 검증. clean PC/installer/자동 시작·두 PC/참조 OS UNVERIFIED |
| 여러 작업 폴더 | default + 로컬 승인 추가 15개 / workspace_id | 실제 MCP/HTTP/CLI 폴더 간 workflow, Artifact, shell·process·terminal cwd, 저장된 설정 resume, 경계·dedupe·승인 검증. cross-volume/두 PC/참조 OS UNVERIFIED |
| 사용자 background | agentctl start/status/stop / OS lifetime lock / 보호된 local control | Windows 10 실제 controller 종료 뒤 유지, 재연결/정상 정리/중복 거부·short path alias, crash UNKNOWN/비재실행 확인. 자동 시작/SCM 설치/로그오프/reboot/Ubuntu/soak UNVERIFIED |
| Desktop client | Electron 44.5.1 / electron-builder 26.15.3 / OS별 Python·Chromium bundle | Windows 10 x64 실제 UI 등록/원격 파일·명령/background 유지·정상 종료와 packaged EXE 실행 확인. macOS DMG/ZIP·Linux AppImage/deb 및 native CI 구성, 실제 OS 실행/서명·clean install/업데이트·제거 UNVERIFIED |
| Windows client 0.1.2 | native Tray/ICO · 현재 journal 작업 · sanitized 최근 활동 | 실제 E2E 활동·장기 Job·Tray callback/close-hide/복원/완전 종료·PID 정리와 다음 연결 CANCELLED 확인. packaged Tray/ASAR icon/bridge/정상 종료 PASS. 실제 141 upgrade/tray mouse/logoff·reboot/제거 UNVERIFIED |
| 실제 두 Windows PC | 호스트 192.168.29.140 / 원격 192.168.29.141, desktop client 0.1.0 standard | 원격 DESKTOP-06NU139의 파일/명령/ConPTY·승인·same-key 실행 한 번과 terminal 정리 PASS. 실제 AI host/Artifact·Job·reboot/전체 OS gate 남음 |

Python 모든 transitive patch와 플랫폼 marker는 `uv.lock`이 authoritative하다.
Console dependency는 `pnpm-lock.yaml`이며 React Query 5.104.0/Zod 4.6.5,
openapi-typescript 7.13.0/Prettier 3.9.9를 고정했다. Node의 checksum source는
[Node 22.23.0 distribution](https://nodejs.org/dist/v22.23.0/SHASUMS256.txt)다.
MCP SDK API는 설치된 코드로 확인했다. 참고 공식 소스:
[SDK v2 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0),
[SDK 서버 API](https://py.sdk.modelcontextprotocol.io/api/mcp/server/mcpserver/server/).

Windows 서비스 계정 ACL·다른 사용자 Broker launch, packaging/clean install, 공개 TLS/OAuth 운영 배포,
실제 host preview는 아직 지원 성공으로 표시하지 않는다.
