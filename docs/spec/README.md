# RACP 시스템 설계 및 엔지니어링 명세서 (Specifications)

> **Document ID**: `DOC-SPEC-INDEX`  
> **Status**: Active · **Target Version**: v0.1.20  
> **Last Updated**: 2026-10-10 · **Classification**: Specification Index

---

## 1. 개요 (Overview)

본 디렉터리는 **Remote-Desktop-Commander (RACP)**의 최상위 시스템 아키텍처, 프로토콜 계약, 상태 모델, 권한 체계 및 엔지니어링 상세 계획을 담고 있는 공식 명세서 모음입니다.

---

## 2. 수록 명세서 목록

| 문서명 | 문서 ID | 상태 | 설명 |
| :--- | :--- | :--- | :--- |
| [Client 영역 전체 Rust 전환 작업 계획서](rust-client-migration-plan.md) | `DOC-PLAN-RUST-CLIENT` | **Active** | Rust native GUI·Agent·Broker·OS Provider·Client 배포 전체 전환, R00–R14와 레거시 제거. Gateway Python 유지 |
| [Agent 세부 권한 및 원격 OS 기능 아키텍처](agent-permissions-and-capabilities.md) | `DOC-SPEC-AGENT-PERMISSIONS` | **Draft** | Client 세부 권한·Agent 검사·18개 기능 분류·host MCP 결합·향후 중앙 정책 경계. Server→Client 정책 배포는 현재 미구현 |
| [Agent 세부 권한 구현 계획](agent-permissions-implementation-plan.md) | `DOC-PLAN-AGENT-PERMISSIONS` | **Superseded** | Python/Electron 구현의 과거 계획. 후속 실행은 Rust 전체 전환 계획에서 기능·권한 요구를 이어받음 |
| [Gateway 웹 관리 서버 작업 계획서](gateway-web-management-plan.md) | `DOC-SPEC-GATEWAY-WEB-PLAN` | **Draft** | v0.1.15 기준 · 중앙 웹 관리·SQLite·서비스·Setup·업데이트·백업, G00–G12와 GT01–GT29 |
| [RACP 종합 개발정의서 v1.1](racp-specification-v1.1.md) | `DOC-SPEC-CORE-v1.1` | **Active** | 전체 시스템의 마스터 설계 명세서 (통신 프로토콜, 보안 경계, 실행 엔진, 도메인 모델, 부록 E 상세) |
| [Windows 작업 및 테스트 계획서](windows-engineering-plan.md) | `DOC-SPEC-WIN-v1.0` | **Active** | 문서 개정 1.5 · 작업 12개, 상세 시험 54개, 화면·세션 조합 8개, 실제 두 PC 인수 및 리버싱 경로 |

---

## 3. 핵심 아키텍처 원칙

1. **아웃바운드 연결 기본 원칙 (Outbound-Only WSS)**:
   Agent는 공인 IP나 인바운드 포트를 열지 않고, Gateway로 보안 웹소켓(WSS) 아웃바운드 연결을 먼저 맺어 방화벽/NAT 환경을 투과합니다.
2. **엄격한 멱등성 및 저널링 (Strict Idempotency & Journaling)**:
   모든 상태 변경(Mutation) 요청은 클라이언트 발급 `idempotency_key`를 필수로 요구하며, 로컬 SQLite 트랜잭션 저널에 기록된 후 실행됩니다. 중복 요청은 1회만 수행됩니다.
3. **최소 권한 및 격리 (Least Privilege & Isolation)**:
   기본 권한은 `read_only`이며, `standard` 및 `trusted_personal`은 명시적 정책 승인을 거쳐야 합니다. 파일 접근은 허용된 네임드 워크스페이스 경계 내로 제한됩니다.

---

## 4. 관련 문서

- [기술 문서 포털](../README.md)
- [품질 보증 및 구현 현황](../quality/implementation-status.md)
- [아키텍처 결정 레코드 (ADR)](../adr/README.md)
