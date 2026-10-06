# 원격 PC 핵심 흐름: 직접 TLS ingress 결과

2026-10-04 KST · Windows 10 Pro x64 · 수행: Codex 구현/로컬 검증

사용자가 원격 PC를 로컬처럼 쓰는 것을 핵심으로 재강조하여 [우선순위](remote-pc-core-plan.md)를 반영했다.
loopback 고정 Gateway 실행을 명시적 TLS listener로 확장했다. 인증과 Device/owner 권한 분리는 유지한다.

## 실행 계약

```powershell
uv run racp-gateway --host 0.0.0.0 --port 8765 --public-origin https://gateway.example:8765 --tls-cert E:\RACP\server.pem --tls-key E:\RACP\server.key
uv run racp --gateway https://gateway.example:8765 --ca-file E:\RACP\ca.pem agent enroll --token-stdin
uv run racp-agent --workspace E:\AllowedWorkspace
```

명령의 host/certificate/CA는 실제 운영 환경에 맞게 준비한다. public CA를 쓰면 --ca-file은 생략한다.
Agent는 다른 PC에서 실행하고 outbound WSS로 Gateway에 연결하므로 Agent의 inbound port는 필요하지 않는다.
enrollment로 저장한 CA 경로는 Agent 시작 시 사용하며 --ca-file로 명시적으로 지정할 수도 있다.
local loopback 기본 실행은 기존과 같다. 이 결과에는 운영 domain/인증서 발급·설치·배포가 포함되지 않는다.

non-loopback의 평문 listener, certificate/key 한쪽만의 설정, TLS transport와 public origin의 scheme 불일치를
시작 전에 거부한다. public origin은 정확한 scheme/host/port이며 HTTP 및 WS에서 이를 검사한다.
MCP SDK의 Host/Origin 검증도 설정된 origin으로 연결하고 DNS rebinding 검사를 끄지 않는다.
Uvicorn proxy_headers=False로 forwarded header를 신뢰하지 않는다. 직접 TLS 방식이며 TLS offload proxy
설정은 별도 계약을 구현하기 전 제공하지 않는다. default trust 또는 추가 CA를 사용하고 hostname을 검증한다.
SDK Artifact와 terminal, CLI의 조회/전송과 Agent 출력/입력 transfer가 동일 CA를 사용한다.
Agent Service config에도 절대 CA 경로를 지정할 수 있다.

## 실제 증거와 제한

`tests/integration/test_remote_tls.py`의 실제 별도 Gateway CLI process는 제품의 Selector network loop를
사용하고 Agent는 subprocess/ConPTY가 가능한 자체 loop에서 실행한다. 임시 CA는 OS trust store에 설치하지 않는다.
실제 HTTPS/WSS와 인증된 MCP SDK를 통한 파일 수정/읽기/명령 실행, binary Artifact 업로드→Agent write/read→다운로드,
WSS terminal, Gateway process 재시작 후 Agent 재연결을 확인했다. 미승인 인증서와 다른 Host/Origin,
owner API의 Device credential 거부를 검증했다. setup/TLS 경계 unit과 합쳐 **7 passed**다.
최신 전체 검사에서는 Gateway를 TLS로 0.0.0.0에 bind하는 실제 CLI 경로까지 같은 7개 시험이 통과했다.
최신 전체 검사 결과는 [작업 현황](implementation-status.md)에 유지한다.

첫 시험에서 Python locale stdout와 UTF-8 요청 encoding의 차이는 시험 command의 -X utf8로 명시했다.
Artifact metadata의 id 필드도 실제 API에 맞췄다. Windows Proactor의 TLS abort shutdown 문제는
Gateway를 제품과 같은 별도 Selector process로 실행하여 검증했다. 강제 종료로 테스트 assertion을 건너뛰지 않는다.

동일 물리 PC에서의 분리된 process 증거다. 두 PC 간 네트워크, 실제 ChatGPT/Codex host,
OAuth/공개 endpoint·서비스 설치·업데이트, IPv6/다른 OS와 성능/soak gate는 아직 별도 검증이 필요하다.
owner bearer MCP가 동작한다는 이유로 ChatGPT 플러그인 설치/연결을 완료했다고 기록하지 않는다.

후속으로 외부 provider OAuth를 추가했다. 현재 owner bearer MCP는 loopback peer/origin에 한정하며,
위 gateway.example 예시의 원격 MCP를 사용하려면 [OAuth 설정](remote-mcp-oauth-setup.md)의
--oauth-config도 필요하다. 실제 Keycloak OAuth 시험은 별도 결과로 관리하고 실제 host gate는 유지한다.
