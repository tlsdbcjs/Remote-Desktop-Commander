# Windows Gateway 배포 및 운영

> **Document ID**: `DOC-GDE-GATEWAY-DEPLOYMENT`\
> **Status**: Active · **Target Version**: v0.1.19\
> **Last Updated**: 2026-10-09 · **Classification**: Deployment & Operations Guide

## 목차

- [1. 배포본과 실행](#1-배포본과-실행)
- [2. Console과 PC 연결](#2-console과-pc-연결)
- [3. Codex MCP 연결](#3-codex-mcp-연결)
- [4. 웹 관리·지원 bundle](#4-웹-관리지원-bundle)
- [5. 상태·백업·업데이트·복원](#5-상태백업업데이트복원)
- [6. 제거와 데이터 보존](#6-제거와-데이터-보존)
- [7. 운영 인수 체크리스트](#7-운영-인수-체크리스트)
- [8. 관련 문서](#8-관련-문서)

## 1. 배포본과 실행

Windows x64 Gateway는 **Setup**과 **Portable ZIP** 두 형태를 정의한다. 둘 다 Gateway 서버, Web Console, CPython 3.12.11 및 고정 dependency를 포함하므로 운영 호스트에 Python/Node 개발 환경이 필요하지 않다. Client 설치 EXE와는 별도 제품이다.

- Setup: `RACP-Gateway-<version>-win-x64-setup.exe`. 관리자 권한으로 `%ProgramFiles%\RACP\Gateway\<version>`에 설치하고 `RACP Gateway` Windows 서비스를 자동 시작으로 등록한다. 운영 상태는 `%ProgramData%\RACP\Gateway`에 둔다.
- Portable: `RACP-Gateway-<version>-win-x64.zip`. 서비스 등록 없이 고정 폴더에 해제하고 PowerShell 진입점으로 실행한다.

현재 소스의 Setup 정의는 NSIS `makensis`를 요구한다. v0.1.19 최종 검증에서는 electron-builder cache의 pinned NSIS **v3.04**를 archive/executable SHA-256까지 확인해 사용했고 compiler warning은 `/WX`로 오류 처리했다. compiler가 없거나 pin/hash가 다르면 Setup build는 중단한다. 정식 release는 별도 Authenticode 서명 gate를 통과해야 하며, 개발용 unsigned 산출물을 signed release로 취급하지 않는다.

2026-10-09 최종 선택 Portable은 `dist/gw-v019-final-native3/RACP-Gateway-0.1.19-win-x64.zip`, SHA-256 `5e9920f082ac9397117ee01544c344e84ca8ea8b76cea6cebb1064d7efd4d4f7`이다. ZIP CRC, manifest **4,391개** 파일 hash, bundled CPython import/version, fresh PowerShell launch, Console/TLS, 1회 등록·Console login, loopback MCP **86-tool** catalog smoke와 실제 121 Web Console 인수를 통과했다. 최종 Setup은 최신 frozen installer source를 pinned NSIS `/WX`로 다시 컴파일한 `dist/gw-v019-final-setup-20261009-1715/RACP-Gateway-0.1.19-win-x64-setup.exe`, SHA-256 `099145b5c6f2125c9447fa10dd3349225ac067fce842474378e030c2482922b8`이다. runtime/Python/Node가 없는 clean Windows 11 VM에서 실제 install, service identity/readiness, OS reboot 후 자동 기동, uninstall/ProgramData 보존, backup 확인 후 명시적 state 삭제까지 PASS했다. Setup source bundle의 **4,392 ZIP entry**는 선택 Portable과 byte-identical하므로 installer-only 수정이 실제 121에서 검증한 runtime payload를 바꾸지 않았다. 정확한 candidate/evidence는 [구현 현황 §26](../quality/implementation-status.md#gateway-management-final-0119)을 사용한다. 이 산출물은 **unsigned 개발 후보**이며 실제 interactive install cancel과 Authenticode 서명까지 통과했다는 뜻은 아니다.

두 선택 파일의 비게시 통합 복사본은 `dist/gw-v019-final-selected`에 있으며 `candidate-manifest.json`과 `SHA256SUMS.txt`로 exact hash를 확인한다. 이 디렉터리는 로컬 인수 편의를 위한 것이며 public release/upload를 뜻하지 않는다.

1. 쓰기 가능한 고정 폴더(예: `E:\RACP\Gateway`)에 ZIP을 압축 해제한다.
2. 호스트에서 `Start-Gateway.ps1`를 실행한다. 첫 실행은 로컬 IPv4를 선택하고 기본 HTTPS 포트 8765로 초기화한다. IPv4가 여러 개면 주소 입력을 요청한다.
3. 실행 창을 유지한다. 종료는 해당 창에서 **Ctrl+C**이다. 상태 조회는 `Status-Gateway.ps1`이다.

명시적 주소·포트가 필요한 경우:

```powershell
.\Start-Gateway.ps1 -BindAddress 192.168.29.141 -Port 8765
```

> [!IMPORTANT]
> 이미 같은 state root 또는 주소/포트에서 Gateway가 실행 중이면 두 번째 서버를 시작하지 않는다. Portable ZIP은 Windows Service를 등록하지 않는다. Setup 설치형은 전용 virtual service account `NT SERVICE\RACP Gateway`, service SID ACL, 자동 시작을 사용하며 브라우저 로그인 사용자의 로그아웃과 독립적으로 실행한다.

PowerShell에서 배포 폴더로 이동한 후 스크립트를 실행한다. `.ps1` 더블클릭은 기본적으로 편집기를 열 수 있으므로 실행 방법으로 안내하지 않는다. Windows PowerShell 5.1을 지원하며 실행 정책은 자동 변경하지 않는다.

Windows PowerShell 5.1의 정책이 Restricted인 PC에서는 다음처럼 해당 프로세스에만 RemoteSigned를 명시한다. PC의 영구 정책이나 조직 정책을 변경하지 않는다. 다운로드 표시가 있는 배포 스크립트는 실행 정책에 따라 검토/서명이 추가로 필요할 수 있다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\Start-Gateway.ps1
```

```powershell
.\Start-Gateway.ps1
.\Status-Gateway.ps1
.\Create-Connection-File.ps1 -Name "원격 PC 121"
.\Create-Console-Login.ps1
```

저장소에서는 같은 원본을 `scripts/host/`에서 실행한다. 연결/로그인 발급은 기존 단일 랩 상태를 자동 탐색하며 `-StateDir`로 명시할 수도 있다. 배포본은 `.racp/host`를 기본 상태로 사용한다. 공통 `Invoke-RacpHost.ps1`은 다른 실행 스크립트와 함께 둔다.

## 2. Console과 PC 연결

Console 주소는 `https://<host>:<port>/console/`이다. `Create-Console-Login.ps1`로 만든 코드 파일을 로그인 화면에 입력한다(5분·1회). 로컬 생성 CA는 `.racp\host\RACP-Host-CA.crt`이며 인증서를 검토해 브라우저가 신뢰하도록 등록하는 절차는 별도로 수행한다. 런처는 신뢰 저장소나 방화벽을 자동 변경하지 않는다.

`Create-Connection-File.ps1`는 `.racp\host\connection-files\`에 PC 등록용 `.racp`를 만든다(10분·1회). **121 PC**로 전달하고 RACP Client에서 파일을 선택해 등록한 다음 **Agent 시작**을 누른다. 선택한 로컬 작업 폴더와 권한 프로필이 적용된다. Console에서 해당 PC가 ONLINE인지 확인한다. Client/Agent는 호스트 Gateway로 outbound WSS를 연결하며 대상 PC에서 Gateway를 실행할 필요가 없다.

관리 Console에서는 장비별 그룹/태그, 관리 사용자 역할과 장비 grant를 설정할 수 있다. Gateway grant는 Agent가 로컬에서 허용한 capability를 넓히지 않으며 최종 권한은 두 경계의 교집합이다. viewer/operator/owner 역할 변경은 서버 auth revision으로 재평가되므로 오래된 브라우저 상태를 권한 근거로 사용하지 않는다.

## 3. Codex MCP 연결

Gateway가 먼저 실행되어야 한다. Codex MCP 등록과 Client 등록은 이후 서로 독립적으로 수행할 수 있다. AI의 원격 작업에는 **Codex 인증 완료 + 대상 Agent ONLINE + 해당 작업 권한/승인**이 모두 필요하다.

외부 MCP는 HTTPS **`/mcp`** 주소를 사용하며 OAuth/OIDC 공급자를 구성해야 한다. Console의 로그인 코드와 PC 등록 파일은 MCP 인증 수단이 아니다. 기본 배포본은 Gateway/API/Console을 시작하지만 IdP 계정이나 운영 OAuth 설정을 내장하지 않는다. 준비한 공급자 설정은 아래와 같이 지정하며 다음 시작에도 사용한다:

```powershell
.\Start-Gateway.ps1 -OAuthConfig E:\RACP\oauth.json
codex mcp add racp --url https://192.168.29.141:8765/mcp
codex mcp login racp --scopes racp.read,racp.execute
```

OAuth issuer/JWKS, owner subject, 허용 client ID와 callback, resource audience는 실제 공급자에 맞춰야 한다. Codex를 실행하는 PC에서도 Gateway·IdP에 접근할 수 있고 각 TLS 인증서를 신뢰해야 한다. 등록 명령과 OAuth 로그인 지원은 [OpenAI Codex MCP 문서](https://learn.chatgpt.com/docs/developer-commands#codex-mcp)에 따른다. 지금 실행 중인 141 서버의 `owner_bearer` 상태는 외부 Codex OAuth 연동 완료를 뜻하지 않는다.

## 4. 웹 관리·지원 bundle

로그인 뒤 관리 화면은 **Overview / Settings / Users / Logs / Backups / Updates**를 제공한다. Overview의 health/readiness, DB/TLS/disk/MCP 상태와 설정 revision을 먼저 확인하고, Settings 변경은 현재 revision과 검증 결과를 확인한 뒤 적용한다. 비밀 경로/키 값은 설정 응답에 원문으로 되돌려 주지 않는다.

장애 분석 자료가 필요하면 **Logs → 지원 번들 생성**을 사용한다. 지원 bundle은 manifest, 버전, 마스킹된 설정, 제한된 운영 로그만 ZIP에 넣으며 원문 SQLite DB, private key, cookie/token, 전체 command output을 기본 포함하지 않는다. 다운로드는 인증된 관리 세션과 `logs.export` 권한이 필요하고 응답은 `no-store`다. bundle도 운영 데이터로 취급해 필요한 기간만 별도로 보관한다.

## 5. 상태·백업·업데이트·복원

개인 owner credential은 Windows 현재 사용자 DPAPI로 보호하며 DB·서버 키·설정은 현재 사용자만 접근하는 `.racp\host\`에 생성한다. 발급 파일도 이 상태 폴더 안에 저장한다. 원격 PC에는 발급한 `.racp`만 전달한다. 생성 인증서는 사설 LAN 개발 배포용으로 365일 유효하다. 만료 전 운영 인증서 갱신을 계획해야 한다.

업데이트 전에 Backups 화면에서 완전한 backup set과 manifest 검증을 확인한다. maintenance/drain 중에는 신규 작업 admission이 닫히지만 이미 수신 중인 결과는 보존된다. Restore는 먼저 preview/검증을 수행하고, corrupt/incomplete 세트는 사용하지 않는다. 다른 계정/PC로 DPAPI credential 파일만 복사하면 복호화되지 않으므로 owner/credential 이관은 별도 운영 절차가 필요하다.

Updates 화면은 현재 버전/channel/feed/trust-key 상태를 표시한다. updater는 Ed25519 서명과 공개키 `key_id`, 만료, `protocol_major`, Gateway schema min/max, minimum updater version, 플랫폼, 크기, SHA-256과 extraction 경계를 검증한 release만 staging하며, 검증 실패는 Gateway를 중지하기 전에 거절한다. apply 중에는 backup·drain·health receipt를 남기고 실패 시 호환 가능한 binary/DB pair로 rollback한다. 운영자는 FAILED/DEFERRED receipt를 무시하고 강제 덮어쓰기하지 않는다.

Portable 수동 업그레이드는 기존 서버를 정상 종료하고 상태 폴더를 보존한 뒤 새 배포본을 별도 폴더에 해제해 `Start-Gateway.ps1 -StateDir <기존 상태 폴더의 절대 경로>`로 시작한다. 설정한 host/port는 시작 옵션으로 무심코 덮어쓰지 않는다.

## 6. 제거와 데이터 보존

Setup 제거는 `RACP Gateway` 서비스를 제거하고 설치 파일/제품 registry만 정리한다. `%ProgramData%\RACP\Gateway` 운영 상태는 기본적으로 **보존**한다. state 삭제가 필요한 경우 backup과 release/restore 가능성을 확인한 뒤 운영자가 정확한 `%ProgramData%\RACP\Gateway` 범위를 명시적으로 삭제한다. v0.1.19 최종 인수에서는 clean VM에서 guest-local backup hash를 먼저 확인하고 uninstall 뒤 보존된 이 경로만 삭제해 scope를 실제 검증했다. uninstall을 다른 제품 데이터나 임의 사용자 폴더 삭제 수단으로 사용하지 않는다.

Portable은 서비스/제품 registry를 등록하지 않으므로 실행을 종료한 뒤 배포 폴더만 제거할 수 있다. 별도 state root를 사용했다면 그 상태는 배포 폴더와 독립적으로 보존한다.

## 7. 운영 인수 체크리스트

한 후보를 운영 인수할 때는 서로 다른 실행의 결과를 섞지 말고 다음 순서를 같은 version/package hash에 연결한다.

1. **설치/기동**: Setup이면 서명/hash 확인 → 설치 → `RACP Gateway` 서비스가 전용 identity·자동 시작인지 확인한다. Portable이면 ZIP hash/manifest를 확인하고 별도 폴더에서 시작하며 서비스가 등록되지 않았는지 확인한다.
2. **최초 Console 로그인**: 5분·1회 bootstrap/login code로 로그인하고 Overview의 version, readiness, DB/TLS/disk/MCP, settings revision을 확인한다.
3. **첫 PC 등록**: Console에서 `.racp`를 발급하고 대상 PC에서 로컬 workspace/profile/화면 권한을 선택해 등록한다. 같은 등록값 재사용이 거절되고 새 device ID가 ONLINE인지 확인한다.
4. **권한 확인**: viewer/operator/admin/owner 및 device/output/operation grant를 점검한다. Gateway grant가 Agent 로컬 capability를 넓히지 않는지 실제 허용/거절 작업으로 확인한다.
5. **MCP(사용 시)**: 운영 IdP·TLS trust·callback을 고정한 뒤 실제 AI client에서 인증하고 해당 device의 허용 작업을 호출한다. loopback owner catalog 결과를 외부 OAuth 인수로 대체하지 않는다.
6. **로그/지원 bundle**: 감사·운영 로그에서 대상 request/device를 검색하고 Logs의 지원 번들을 생성한다. ZIP에 원문 token/cookie/private key/DB/command output이 없는지 확인한다.
7. **Backup**: COMPLETE set과 manifest/hash를 확인하고 incomplete/corrupt set을 복원 후보로 사용하지 않는다.
8. **Update**: release signature/key/expiry/platform/hash를 검증하고 active 작업이 있으면 DEFERRED인지 확인한다. apply receipt와 새 version/readiness를 보존한다.
9. **Restore**: restore preview와 DB integrity/schema/instance 경고를 확인하고 staged restore/pre-restore snapshot 절차를 따른다. 실패 시 임의 재실행보다 receipt와 rollback 상태를 먼저 확인한다.
10. **제거/보존**: Setup uninstall은 제품 파일/서비스만 제거하고 기본 state 보존을 확인한다. 데이터 삭제는 backup 확인 뒤 명시적으로 수행한다. Portable은 실행 종료 후 배포 폴더와 별도 state root를 구분해 정리한다.

현재 v0.1.19 최종 개발 후보에서는 Portable, current-host SCM, runtime-free clean Windows Setup install/실제 reboot/uninstall/data-delete, support/backup/update/restore 자동화, 50 synthetic Agent acceptance, 1만 device/100만 audit p95와 SQLite writer 경합, 그리고 exact selected Portable + 실제 121의 별도 source Agent v0.1.19 Web Console 인수까지 검증했다. 남은 제한은 실제 interactive logout과 설치 중 Cancel, installed-service N→N+1 rollback, 8시간 soak, 외부 OAuth/Codex MCP, release signing/update feed다. 짧은 functional soak, loopback MCP catalog, 강제 installer process 종료를 각각 장기 soak·external OAuth·실제 cancel의 PASS로 대체하지 않는다.

## 8. 관련 문서

- [Gateway 웹 관리 서버 작업 계획서](../spec/gateway-web-management-plan.md): 구현·시험·인수 기준
- [원격 MCP OAuth 설정](remote-mcp-oauth-setup.md)
- [PC 연결 및 권한 프로필](pc-connect-guide.md)
- [2-PC 운영 절차](two-pc-lab-guide.md)
- [Windows 릴리스 인수 게이트](../quality/windows-release-gates.md)
- [빌드·검증 현황](../quality/implementation-status.md)
