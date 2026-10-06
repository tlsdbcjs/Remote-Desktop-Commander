# 두 Windows PC 시험 클라이언트

2026-10-05 KST · unsigned 개발용 portable client · 실제 141 연결은 실행 후 확인

현재 PC `192.168.29.140`은 Gateway이며 `192.168.29.141`에서 Agent를 실행한다.
Agent는 HTTPS/WSS로 outbound 연결하고 파일/명령/터미널 요청을 RACP 프로토콜로 받는다.
RDP/WMI나 Windows 계정 비밀번호는 이 연결에 사용하지 않는다.

현재 GUI/트레이 버전은 [데스크톱 안내](desktop-client-guide.md)를 따른다. 아래 CMD portable은 초기
시험 artifact다. 0.1.5는 Gateway에서 받은 `RACP-connection.racp`를 선택하고 허용 폴더/profile을
정해 최초 등록한다. 주소·token·CA를 각각 입력하지 않아도 된다. 이미 정상 등록된 PC는 재등록하지
않고 Agent 시작만 사용한다. 파일은 1회/10분 token을 포함하며 등록 뒤 삭제한다.
Windows 0.1.5의 실제 141 재접속은 후속 인수시험이다. 현재까지 검증한 실제 두 PC 실행은
이 문서 말미와 [두 PC 결과](two-pc-141-result.md)의 범위로 한정한다.

`dist/RACP-Client-Windows-x64.zip`은 Python 3.12.11, frozen Agent 의존성과 Chromium을 포함한다.
설치 없이 압축을 풀어 `RACP-Client/Start-Client.cmd`를 실행한다.
등록 상태는 `state`, 파일 작업은 `workspace`에 생성한다.
`Status-Client.cmd`의 `connected: true`를 확인한다. 종료는 `Stop-Client.cmd`다.

시험 profile은 trusted_personal이며 현재 Windows 계정으로 파일 저장/명령 실행을 허용한다.
파일 도구의 경계는 workspace다. 임의 shell은 OS 계정 권한으로 실행한다.
등록 ticket은 10분 유효/1회 사용이며 성공 후 제거한다. 만료되면 새 등록 정보를 발급한다.
CA는 client의 ca.pem으로만 신뢰하고 OS trust store를 수정하지 않는다.

현재 PC에서 이 portable runtime으로 HTTPS 등록, 실제 background 연결, 파일 저장/읽기,
패키지 내부 Python 명령 실행 및 정상 종료를 통과했다. bundled browser health도 확인했다.
현재 PC의 다른 Python 설치/uv에 의존하지 않는 실행이다. ZIP CRC와 SHA-256를 확인했고
현재 PC에서 사용한 credential/state와 Gateway private key/owner token을 포함하지 않았다.
후속으로 사용자가 141에서 desktop client를 실행했고, 실제 PC 간 파일/명령/ConPTY workflow를
확인했다. [두 PC 결과](two-pc-141-result.md). 전체 복구·설치/AI host gate는 남는다.

Gateway는 .racp/two-pc-lab에 격리하고 `https://192.168.29.140:8765`로 실행한다.
원격에서 연결이 안 되면 방화벽을 확인한다. 현재 호스트는 비관리자 token이므로 규칙 변경을
수행하지 않았다. 관리자용 `.racp/two-pc-lab/Allow-Test-Port.ps1`은 peer 141, local address 140,
TCP 8765와 해당 Python program만 허용한다. 시험 후 Remove-Test-Port.ps1로 자기 규칙만 제거한다.

전체 설치/SCM/자동 시작/업데이트·복구/SBOM·전체 license/서명 gate를 완료한 release는 아니다.
remote MCP 실제 AI host는 기존 OAuth 설정과 별도 검증이 필요하다.
