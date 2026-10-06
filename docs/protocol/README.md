# RACP 프로토콜 계약 및 스키마 (Protocol Contracts)

> **문서 ID**: `DOC-PROTO-INDEX`  
> **상태**: Active · **기준 버전**: v0.1.8  
> **최종 개정일**: 2026-10-07 · **분류**: Protocol Index

---

## 1. 개요 (Overview)

본 디렉터리는 **RACP** 시스템의 핵심 통신 계약인 **Strict Discriminated JSON Schemas** 및 **OpenAPI 3.1 Specification** 파일들을 보존하고 관리합니다.

> [!CAUTION]
> 본 디렉터리에 위치한 모든 `.schema.json` 및 `console-openapi-v1.json`, `registry-v1.json` 파일 경로는  
> `tests/contract/test_schema_drift.py` 및 `scripts/schema.py` 등 자동화 테스트 스위트에 의해 무결성이 검증되므로,  
> 파일명을 임의로 변경하거나 다른 디렉터리로 이동해서는 안 됩니다.

---

## 2. 주요 스키마 구성

| 파일명 | 역할 및 계약 내용 |
| :--- | :--- |
| `agent-protocol-v1.schema.json` | Gateway ↔ Agent 간의 WebSocket (WSS) 양방향 메시지 (Hello, Heartbeat, Operation, Stream, Status 등) |
| `console-openapi-v1.json` | Gateway REST API 명세 (OpenAPI 3.1) - Console UI 및 CLI 클라이언트 상호작용 |
| `registry-v1.json` | 전체 내장/플러그인 도구 및 Operation 식별자 카탈로그 레지스트리 |
| `agent-settings-v1.schema.json` | 에이전트 DPAPI/0600 보호 설정 파일 (`credential.bin` / `ServiceConfig`) 스키마 |
| `oauth-resource-config-v1.schema.json` | 원격 MCP OAuth Protected Resource 설정 스키마 (Issuer, JWKS, Audience, Scope) |
| `job-acceptance-v1.schema.json` / `job-view-v1.schema.json` | 비동기 백그라운드 Job 접수, 상태 추적 및 수명주기 스키마 |
| `terminal-stream-*.schema.json` | 지속적 PTY 가상 터미널 세션 스트리밍 및 윈도우 크레딧 제어 스키마 |
| `artifact-transfer-*.schema.json` | 대용량 바이너리/출력 파일 Scoped 청크 전송 및 무결성(SHA-256) 검증 스키마 |

---

## 3. 스키마 무결성 검증

스키마 파일의 변경 사항은 계약 드리프트(Schema Drift) 검사 도구로 상시 검증됩니다:

```bash
uv run pytest tests/contract/test_schema_drift.py -v
uv run pytest tests/contract/test_console_drift.py -v
```

---

## 4. 관련 문서

- [기술 문서 포털](../README.md)
- [시스템 명세서](../spec/racp-specification-v1.1.md)
- [원격 MCP OAuth 설정 가이드](../guides/remote-mcp-oauth-setup.md)
