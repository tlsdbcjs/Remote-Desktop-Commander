# ADR-0027: 최초 등록과 저장된 설정의 안전한 진단

2026-10-05 · 채택 · Windows client 0.1.4

사용자와 연결 방향을 다시 검토했고 Agent가 Gateway로 먼저 연결하는 HTTPS/WSS 방식을 유지한다.
연결 후 Gateway는 기존 양방향 채널로 선택된 PC에 명령을 전달한다. 원격 PC의 inbound listener와
Gateway의 LAN 스캔은 추가하지 않았다. MCP client와 Agent의 네트워크 client 역할은 구분한다.

등록은 최초 한 번만 수행하고, 정상 저장된 설정이 있으면 이후 실행은 현황과 시작/중지/완전 종료
화면으로 간다. UI 초기 info가 실패하거나 아직 완료되지 않았을 때 새 PC 등록 form을 표시하던
문제를 수정했다. 저장된 설정/Windows 사용자 credential/CA/폴더를 읽지 못하면 복구 안내와
상태 재확인/완전 종료만 표시한다. 기존 등록을 삭제하거나 새 토큰으로 덮어쓰지 않는다.

private desktop bridge는 실패 응답을 닫힌 code 집합으로 제한한다. Gateway 주소, 로컬 폴더,
CA 파일 preflight를 토큰 사용 전에 확인한다. 토큰 형식/거부, TLS, 연결 실패/시간 초과,
이미 등록/중단된 등록, 서버 등록 후 credential 저장 실패와 기존 설정 읽기 실패를 구분한다.
서버가 이미 사용·만료·미발급 토큰을 같은 인증 거부로 처리하므로 이 세 가지를 단정하지 않는다.

Node는 임의 backend message/exception text를 전달하지 않고 code를 정해진 한국어 안내로 변환한다.
controller는 알려진 진단을 보존하며 알 수 없는 오류는 일반 안내로 바꾼다.
renderer는 Electron의 IPC prefix를 제거하되 알려진 정확한 메시지만 표시한다.
토큰/credential/서버 응답/로컬 경로를 오류 문구에 넣지 않는다. 실패 뒤 토큰은 input에서 지운다.

zip 일부만 복사한 runtime 누락과 잘못된 runtime 응답, bridge timeout도 사용자 안내로 구분한다.
수동 토큰 등록은 아직 최초 setup 방법이다. 설정 import/승인 기반 간편 pairing은 후속이다.

## 검증

관련 Python 19개, Node 10개와 TypeScript/Vite build를 통과했다. 실제 Electron 시험은
만료 token의 거부 안내 → 새 token 등록, 사용된 token의 재사용 거부, 실제 원격 파일/실행,
트레이/자기 Job 정리, 저장된 등록으로 재실행, 손상된 자기 fixture credential의 보존과 재등록 금지를 확인한다.
실제 Electron E2E는 위 시나리오를 통과했다. 최초 실행에서는 장기 Job의 journal RUNNING 표시와
child fixture의 PID receipt 생성 사이 경합으로 시험이 실패했다. 프로세스 준비 receipt를 기다리도록
시험을 보완한 뒤 실제 Job/정리/복구와 후속 등록 진단을 함께 통과했다.
전체 품질 gate는 **307 passed / 18 skipped**, format/lint와 mypy **129 source files**를 통과했다.
Node는 **10 passed**다. packaged 0.1.4의 실제 실행/version/userData/info, CA preflight 안내와
완전 종료도 통과했다. smoke는 현재 Windows 계정의 credential이 없을 때만 실행하며,
이미 등록된 사용자 state에는 실행을 거부한다. 종료 IPC의 결과 반환 전에 창이 닫히는 시험 경합은
닫힘을 기다리는 방식으로 수정했다.
포터블 ZIP CRC와 Agent **5,171개** SHA-256, EXE/app.asar의 packaged 일치를 확인했다.
ZIP은 546,173,563 bytes이고 SHA-256는 `dist/client-desktop-0.1.4/build-manifest.json`에 있다.
설치 EXE는 399,004,902 bytes이며 NotSigned 개발 빌드다. 같은 manifest에 SHA-256를 기록한다.
installer maintenance/제거 로직은 ADR-0026을 유지한다. 이 후속 packaged smoke를 새로운
clean VM 또는 실제 141 업데이트 인수시험으로 간주하지 않는다.

전체 개발정의서와 실제 AI host/OAuth·clean VM·SCM·참조 OS·복구/soak gate를 축소하지 않는다.
