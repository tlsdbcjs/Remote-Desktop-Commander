# 원격 MCP OAuth 설정

2026-10-04 KST · 개발용 설정 계약 · 실제 host 연결 gate는 미완료

AI는 `https://gateway.example/mcp/`로 연결하고, 원격 PC의 Agent는 같은 Gateway로 outbound WSS 접속한다.
AI에게 owner setup secret이나 Device credential을 전달하지 않는다. Gateway와 provider는 별도의 역할이다.

## Provider와 Gateway 설정

기존 OAuth/OIDC provider에 client와 로그인 사용자를 준비한다. client는 authorization-code와
PKCE S256을 사용하며 consent와 정확한 redirect URI를 설정한다. Gateway resource는
public origin 뒤의 `/mcp`다. 토큰은 이 resource의 audience, subject, client ID와 요청한 scope를 포함해야 한다.
Keycloak에서는 기본 sub mapper와 access-token audience mapper, racp.read/racp.execute client scope가 필요하다.
검증용 실제 설정은 `tests/integration/test_oauth_keycloak.py`에 있다.

아래 JSON의 예시 값을 실제 provider 값으로 바꾸고 Gateway PC의 절대 로컬 경로에 저장한다.
client ID는 provider가 승인한 실제 ID다. owner_subject는 표시 이름이나 이메일 대신 토큰의 정확한 sub다.
비밀 토큰·client secret을 이 설정에 넣지 않는다.

```json
{
  "version": 1,
  "issuer": "https://identity.example/realms/racp",
  "jwks_uri": "https://identity.example/realms/racp/protocol/openid-connect/certs",
  "owner_subject": "REPLACE_WITH_PROVIDER_SUBJECT",
  "client_ids": ["REPLACE_WITH_APPROVED_CLIENT_ID"],
  "read_scope": "racp.read",
  "execute_scope": "racp.execute",
  "algorithms": ["RS256"],
  "max_token_seconds": 3600
}
```

schema는 `docs/protocol/oauth-resource-config-v1.schema.json`이다. 공개 CA는 OS trust를 사용한다.
private CA를 사용하는 provider에는 선택적으로 `ca_file`의 절대 경로를 지정한다.
Gateway listener의 인증서와 provider 검증용 CA는 별개다. 인증서·hostname 검증 우회는 제공하지 않는다.

```powershell
uv run racp-gateway --host 0.0.0.0 --port 8765 --public-origin https://gateway.example:8765 --tls-cert E:\RACP\tls\gateway.pem --tls-key E:\RACP\tls\gateway.key --oauth-config E:\RACP\oauth.json
```

metadata URL은 `https://gateway.example:8765/.well-known/oauth-protected-resource/mcp`이고
resource 값은 `https://gateway.example:8765/mcp`다. endpoint의 trailing slash와 audience를 구분한다.
인증 없는 MCP 요청에는 401 및 resource_metadata challenge가 나와야 한다.
CLI의 `racp doctor`는 인증 방식, resource, metadata, issuer와 scope를 표시한다.

원격 PC의 enrollment/Agent는 [TLS 설정](phase-1-remote-tls-result.md)의 절차를 사용한다.
OAuth scope가 허용돼도 Agent profile과 별도 승인 정책을 통과해야 한다.
trusted_personal은 Gateway와 Agent 양쪽에서 명시적으로 활성화한 개인 Device에서 사용한다.

## 실제 AI host 연결

host의 MCP 설정에 HTTPS endpoint를 등록하고 provider의 승인된 client를 연결한다.
predefined client, DCR 또는 CIMD 중 provider와 host가 지원하는 방식을 선택하며,
발급된 실제 client ID를 Gateway allowlist에 넣는다. 임의 client를 모두 허용하지 않는다.
정확한 redirect URI는 host 관리 화면에서 복사하여 provider에 등록한다.
고정된 과거 callback 주소나 wildcard를 가정하지 않는다.
[현재 OpenAI 인증 안내](https://developers.openai.com/plugins/build/auth)는 PKCE S256,
resource 검증과 host의 client 등록 방식을 설명한다.

host에서는 Device 조회 → 명시적 Device 선택 → 파일 읽기/저장 → 명령 실행 → Job/terminal
조회·취소 → Artifact 회수 → 재연결을 시험한다. public 도메인/인증서와 실제 host 계정의 연결은
로컬 SDK 시험으로 대체하지 않는다. OAuth 기반 binary Artifact 회수의 host 경로도 후속 검증 대상이다.

JWT 인증은 access token의 유효 기간과 JWKS cache를 사용한다. provider logout을 즉시 반영하는
introspection은 미구현이다. 현재는 single owner이며 DCR/CIMD provider 운영 설정도 별도 gate다.
public origin이 loopback 밖이거나 remote peer가 접속하면 OAuth 미설정 MCP는 503으로 차단한다.
loopback origin과 실제 loopback peer의 로컬 개발 접속만 기존 owner bearer를 허용한다.

## 같은 PC의 Codex와 LAN Agent를 함께 시험할 때

Gateway에 `--local-mcp-port 18765`를 추가하면 같은 process/control plane의 두 번째
HTTPS socket을 `127.0.0.1:18765`에 연다. Agent와 owner API는 기존 `--host`, `--port`,
`--public-origin`을 사용하고 MCP 접점만 `https://127.0.0.1:18765/mcp`로 옮긴다.
이 모드의 LAN MCP/metadata는 거부된다. local socket에는 MCP와 protected-resource
metadata만 허용하며 peer, 실제 socket 주소/port와 origin을 함께 검증한다.
TLS와 OAuth 설정이 모두 필요하고 owner bearer로 대체하지 않는다. Provider access
token의 audience도 정확한 local MCP resource로 설정해야 한다.

Codex 0.158.0의 RMCP 3.2.0은 LAN 주소의 MCP가 안내한 loopback authorization server를
거부한다. 이 모드는 인증 서버도 동일 PC에서 실행하는 개발/인수시험에 사용한다.
외부 AI host 연결은 별도 적절한 인증 서버 주소와 공개/관리 인증서 구성이 필요하다.
Codex HTTP endpoint 변경 후 앱 설정의 MCP server를 다시 시작해야 한다.
현재 UI에 재시작 버튼이 없다면 비활성화/활성화 후 연결을 확인하고, 활성 대화의 tool
catalog가 갱신되지 않았으면 앱을 완전히 종료한 뒤 같은 대화를 다시 연다.

Keycloak fixture의 client에는 Codex가 token refresh에서 요청하는 `offline_access`도
optional client scope로 연결하고 fixture 사용자에게 해당 realm role과 scope mapping을
설정해야 한다. scope를 metadata에 광고하면서 client에 연결하지 않으면 최초 로그인은
성공해도 refresh가 invalid_scope로 실패할 수 있다. 초기화뿐 아니라 access token 만료
이후 native 재연결을 검증한다. `scripts/check_codex_mcp.py`는 모델 실행 없이 별도 Codex
app-server의 인증/도구 초기화 상태를 확인하며 현재 앱의 실제 도구 호출과 구분한다.
