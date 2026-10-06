# 원격 MCP 외부 OAuth 결과

2026-10-04 KST · CORE-MCP-02 인증 기반 · 실제 host gate는 미완료

Gateway에 외부 OAuth resource server를 추가했다. issuer/JWKS/subject/client ID는 관리자의
절대 로컬 JSON으로 고정하고, HTTPS·서명·audience/resource·시간·scope를 검증한다.
MCP metadata/challenge와 tool별 securitySchemes를 제공한다. read와 execute scope를 분리하고
기존 profile·승인 정책을 유지한다. owner 관리 API와 Device 인증은 별도이며 bearer를 Agent로 전달하지 않는다.
OAuth 없는 원격 MCP는 차단하고 실제 loopback peer/origin만 로컬 owner bearer를 허용한다.

[설정 절차](remote-mcp-oauth-setup.md), [ADR-0018](adr/ADR-0018-external-oauth-mcp-resource-server.md),
`docs/protocol/oauth-resource-config-v1.schema.json`, `apps/gateway/src/racp_gateway/oauth.py`가 산출물이다.

## 실제 provider 인수시험

실제 Docker Keycloak 26.8.0 image를 다음 digest로 고정했다.

```text
quay.io/keycloak/keycloak@sha256:b0f60d489d51c5d113390bdf5461d4c06e6051be026c05549f2e1e10ec352bcc
```

`tests/integration/test_oauth_keycloak.py`는 임시 private CA와 테스트 전용 realm/사용자,
predefined public client로 실제 로그인 폼과 consent를 제출하고 PKCE S256 code를 교환했다.
Discovery의 S256/issuer identification, callback의 state/iss를 확인하고 code 재사용 400을 검증했다.
이어서 별도 실제 Gateway CLI와 outbound WSS Agent를 사용해 다음을 확인했다.

- 인증 없는 MCP의 401/resource metadata 안내와 metadata resource/issuer 일치.
- read scope로 Device 조회, 변경 도구 실행 거부와 재인증 안내.
- execute scope로 MCP fs_write → shell_exec → 파일 내용 출력 확인.
- OAuth 토큰의 owner 관리 API 접근 거부, owner/Device secret의 OAuth MCP 접근 거부.
- 도구 descriptor의 OAuth securitySchemes 전달.

실제 provider 인수시험은 **1 passed**, `dist/oauth-native-results.xml`이다.
JWT/JWKS/설정/loopback 경계 unit은 **21 passed**다. 잘못된 signature/issuer/audience/subject/client,
만료·미래 시간·과도한 수명·잘못된 scope 타입, token의 임의 jku, 깨진 JWKS와 만료 cache를 검증했다.
깊은 JWT header와 잘못된 provider URL도 거부한다. CLI doctor는 기존 출력에 diagnostics를 추가해
서버의 실제 인증 설정을 보여 준다. 이 추가 경계와 비밀을 출력하지 않는 실제 CLI 시험은
별도 **22 passed**, `dist/oauth-cli-results.xml`로 확인했다.
image는 테스트용 loopback port만 게시했고 자신의 container label을 확인한 뒤 제거했다.
임시 CA를 OS trust store에 설치하지 않았다. 테스트의 start-dev를 운영 배포로 사용하지 않는다.

첫 두 시도는 테스트의 readyz 인증 누락과 상대 consent URL/sub mapper 설정 때문에 실패했다.
제품 인증을 완화하지 않고 테스트 설정을 수정한 뒤 실제 전체 인증/실행 흐름을 통과했다.

## 검증 범위와 남은 일

같은 물리 Windows 10 PC에서 실제 provider/Gateway/Agent를 분리해 실행한 결과다.
ChatGPT/Codex 실제 client, 공개 인증서·도메인, 인터넷 ingress, 서로 다른 두 PC 연결은 미검증이다.
SDK client PASS를 실제 host PASS로 기록하지 않는다. DCR/CIMD 운영 설정과 host의 정확한 callback/client
등록, OAuth host의 Artifact 회수 경로, token introspection/즉시 logout은 남은 gate다.
현재 JWT는 유효 기간/JWKS cache로 검증하며 provider logout을 즉시 반영하지 않는다.

전체 품질·Console 결과의 최신 수치는 [작업 현황](implementation-status.md)에 유지한다.
최종 전체 기준선은 **261 passed, 17 skipped**, 실제 Chromium은 **11 passed**다.
Windows/Linux 타입 target 121개 source file과 8개 개발 wheel을 확인했고,
추가 JWT/CLI 수정 뒤 해당 22개 회귀와 format/lint/type를 통과했다. 최종 wheel의 source 포함과 SHA-256도 확인했다.
사용자 목표는 원격 PC의 파일·terminal·process 작업 흐름이며, 다음 우선순위는 실제 host 연결과
PC 등록 간소화·허용 폴더 선택·백그라운드 Agent다. 전체 Phase 0–12를 완료로 선언하지 않는다.
