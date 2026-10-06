# ADR-0028: Gateway 연결 파일로 최초 PC 등록

2026-10-05 · 채택 · Windows client 0.1.5

Agent가 Gateway로 먼저 접속하는 방향은 유지한다. 새 PC 등록의 기본 화면은 연결 파일 선택과
허용 폴더/실행 권한 선택이다. IP·토큰·CA를 각각 입력하는 form은 직접 입력 모드로 남긴다.
정상 등록 뒤에는 저장된 설정으로 현황·시작/중지/완전 종료를 사용한다.

Owner API의 enrollment 요청에 `include_connection_file=true`를 추가한다. 기존 token 응답을 유지하고
version 1/gateway/token/expires_at/ca_pem의 파일 객체를 함께 반환한다. owner 인증과 Console CSRF,
no-store를 유지한다. 공개 Gateway origin을 사용하며 등록 token의 1회/10분 제한을 바꾸지 않는다.
Console은 `RACP-connection.racp` 다운로드를 제공한다. 파일에는 bearer 등록 token이 있으므로
해당 PC에만 전달하고 등록 후 삭제한다. 앱에는 token을 붙여 넣을 필요가 없다.

private CA를 쓰는 Gateway는 `--client-ca-file <public-ca.pem>`으로 공개 신뢰 인증서를 지정한다.
최대 16 KiB의 PEM certificate만 허용하며 private key를 넣으면 시작을 거부한다.
CA를 생략하면 Agent는 OS의 기본 신뢰 인증서를 사용한다. TLS/hostname 검증을 끄지 않는다.
현재 두 PC lab의 발급 script도 같은 파일을 만들며 Gateway private key/owner secret은 포함하지 않는다.

파일은 32 KiB 한도/strict schema/HTTPS origin/명시한 만료 시각/CA PEM을 검사한다.
실제 native 파일 선택 경로는 main process에만 보관한다. renderer에는 Gateway·유효 시각·공개 CA hash만
전달하고 token과 원시 JSON은 전달하지 않는다. 등록 버튼을 누르면 같은 파일을 다시 읽고 선택 당시
SHA-256와 일치하는지 검사한다. 변경됐으면 token을 사용하지 않고 재선택을 요구한다.
renderer가 전달한 path/digest/Gateway/token으로 선택 대상을 바꿀 수 없다.

CA는 Agent 사용자 state에 content hash 이름으로 보존한다. 원본 연결 파일이나 전달용 CA가 없어도
다음 Agent 실행에 필요하지 않다. local folder/profile은 사용자가 선택하고 read_only가 기본이다.
이미 등록된 credential은 덮어쓰지 않는다. 파일 선택은 수신한 Gateway와 CA를 신뢰하는 사용자 결정이며
암호학적으로 서명된 config 배포나 임의 LAN 자동 pairing을 제공했다고 주장하지 않는다.

## 검증

관련 Python **27 passed**, mypy **131 source files**, Node **10 passed**를 확인했다.
파일 크기/extra field/private key/만료/변경 거부, token 없는 preview, CA 내부 저장,
원본 삭제 후 설정 로드, 기존 credential 보호와 Owner/Device/익명 발급 경계를 검사한다.
실제 Console의 연결 파일 다운로드/1회 token/만료·메모리 정리는 targeted E2E를 통과했다.
실제 Electron은 private CA의 HTTPS 등록/WSS 접속과 파일·명령·Job·tray 종료/재실행을 통과했다.
만료 파일·선택 뒤 변경한 파일을 거부하고 token을 사용하지 않은 뒤 원본으로 등록했다.
등록 뒤 원본 파일을 삭제하고 saved Agent를 재시작했다. packaged 0.1.5 smoke도 통과했다.
전체 Console **14 passed**, production build/생성 타입 drift 검사와 Windows/Linux mypy를 통과했다.
포터블 ZIP CRC와 Agent **5,175개** SHA-256, EXE/app.asar packaged 일치를 확인했다.
ZIP 546,183,296 bytes, 설치 EXE 399,013,445 bytes와 SHA-256는
`dist/client-desktop-0.1.5/build-manifest.json`에 있다. EXE는 NotSigned 개발 빌드다.
전체 Python 검사는 OpenAPI 갱신 전 drift 1 failed/314 passed/18 skipped를 기록했고,
갱신 후 해당 contract 재검을 통과했다. 후속 전체 검사에서는 기존 browser lease 만료 fixture의
heartbeat 갱신 경합이 발견됐다. 갱신을 마지막 transport 경계에서 차단한 뒤 새로운 open 요청으로
이전 frame 처리를 보장하도록 시험을 보완했다. 제품의 정상 lease 갱신 동작은 바꾸지 않았다.
보완 후 전체 검사 **315 passed / 18 skipped**, format/lint와 mypy **131 source files**를 통과했다.
Windows 전용 registry helper는 플랫폼 guard를 추가해 Linux type gate도 131개를 통과했다.

OpenAPI와 생성 Console 타입은 실제 API에서 갱신한다. 실제 141 업데이트와 AI host/OAuth,
SCM/clean VM/전체 migration·복구·soak gate는 계속 남는다.
실행 중인 두 PC lab Gateway도 연결 파일 API/public CA 옵션으로 갱신했고 readiness와 새 schema를
확인했다. owner/DB/등록 정보는 유지했다. 141 Agent는 갱신 전후 OFFLINE이며 진행 중 operation은 없었다.
실제 140 Gateway의 Owner API로 파일을 발급하고 packaged Python bridge로 preview를 검증했다.
Gateway/public CA hash 일치, token 비출력과 Agent state 미생성을 확인했다. 시험용 미사용 token은
기본 TTL을 유지하며 해당 임시 파일은 시험 뒤 제거했다. 원격 141에서 등록한 증거는 아니다.
