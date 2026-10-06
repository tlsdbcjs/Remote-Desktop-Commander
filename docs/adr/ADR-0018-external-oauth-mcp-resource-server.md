# ADR-0018: 외부 OAuth provider와 MCP resource server

2026-10-04 KST · 채택 · remote PC 핵심 흐름 CORE-MCP-02의 인증 기반

Gateway는 OAuth resource server다. 로그인·동의·authorization code·PKCE·refresh token은
기존 provider가 담당한다. Gateway가 자체 authorization server 또는 AI client를 구현하지 않는다.
원문 개발정의서 E6의 remote/public MCP 요구사항을 적용한다.

관리자가 로컬 JSON으로 issuer/JWKS URL, owner subject, 허용 client ID, 서명 알고리즘을 고정한다.
provider와 Gateway는 HTTPS다. 토큰의 jku/x5u URL을 따라가지 않고, issuer를 임의 정규화하지 않는다.
JWKS는 검증된 TLS로 redirect 없이 읽고 크기·키 수를 제한한다. RS256/ES256만 허용한다.
signature, 정확한 issuer, `/mcp` resource audience, exp/iat/nbf, 최대 수명, subject, client를 검사한다.
캐시 만료 후 provider가 응답하지 않으면 인증을 거부한다. opaque owner/Device secret은 OAuth 토큰이 아니다.

모든 MCP 요청은 `racp.read`를 요구한다. registry의 side_effect 도구와 operation/job 취소는
추가 `racp.execute`를 요구한다. 각 tool descriptor에 OAuth securitySchemes를 공개하고,
부족한 scope는 실행 전에 차단하며 `mcp/www_authenticate` 재인증 안내를 반환한다.
Gateway의 기존 profile/승인/Device 권한 검사를 계속 적용한다. OAuth 토큰을 Agent에 보내지 않는다.
owner 관리 API와 Console 인증은 기존 별도 경로다. OAuth 토큰으로 승인·등록·revoke를 수행할 수 없다.

보호 resource metadata를 `/.well-known/oauth-protected-resource/mcp`와 root alias에 노출한다.
인증 없는 MCP 요청은 SDK의 401 WWW-Authenticate challenge를 반환한다.
OAuth 미설정 owner bearer MCP는 실제 loopback peer와 loopback origin에만 허용한다.
원격 peer 또는 public origin은 OAuth 미설정 상태에서 503으로 차단한다.
forwarded header는 peer/TLS 신뢰의 근거가 아니다.

로컬 실제 provider 인수시험은 digest로 고정한 Keycloak 26.8.0, 임시 realm/계정,
predefined public client, consent, PKCE S256, 정확한 redirect와 고정 audience를 사용한다.
테스트 컨테이너는 loopback TLS port만 게시하고 자신이 만든 label을 확인한 뒤 정리한다.
이 테스트의 start-dev 설정은 운영 배포 템플릿이 아니다.

JWT logout/revoke의 즉시 반영은 구현하지 않았다. access token 만료와 JWKS 재조회까지 유효할 수 있으므로
provider의 access token 수명을 짧게 설정한다. Device revoke/lease는 별도로 유지한다.
single owner이며 DCR/CIMD를 Gateway가 대신 구현하거나 임의 client ID를 승인하지 않는다.
실제 ChatGPT/Codex client 등록과 callback, 공개 인증서/도메인, 실제 두 PC 시험은 별도 gate다.

근거: [OpenAI MCP 인증 안내](https://developers.openai.com/plugins/build/auth),
[Keycloak OIDC endpoints](https://www.keycloak.org/securing-apps/oidc-layers),
[Keycloak protocol mappers](https://www.keycloak.org/admin-api/protocol-mappers).
