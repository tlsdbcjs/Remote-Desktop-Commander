# RACP 시스템 설계 및 엔지니어링 명세서 (Specifications)

> **Document ID**: `DOC-SPEC-INDEX`  
> **Status**: Active · **Target Version**: v0.1.9  
> **Last Updated**: 2026-10-07 · **Classification**: Specification Index

---

## 1. 개요 (Overview)

본 디렉터리는 **Remote-Desktop-Commander (RACP)**의 최상위 시스템 아키텍처, 프로토콜 계약, 상태 모델, 권한 체계 및 엔지니어링 상세 계획을 담고 있는 공식 명세서 모음입니다.

---

## 2. 수록 명세서 목록

| 문서명 | 문서 ID | 상태 | 설명 |
| :--- | :--- | :--- | :--- |
| [RACP 종합 개발정의서 v1.1](racp-specification-v1.1.md) | `DOC-SPEC-CORE-v1.1` | **Active** | 전체 시스템의 마스터 설계 명세서 (통신 프로토콜, 보안 경계, 실행 엔진, 도메인 모델, 부록 E 상세) |
| [Windows 작업 및 테스트 계획서](windows-engineering-plan.md) | `DOC-SPEC-WIN-v1.0` | **Active** | 문서 개정 1.1 / 코드 기준 v0.1.9 · 작업 12개, 상세 시험 54개, 화면·세션 조합 8개, 실제 두 PC 인수 3개 |
| [Client·Agent Rust 전환 설계안](client-rust-migration.md) | `DOC-SPEC-CLIENT-RUST` | **Draft** | Tauri/Rust와 React 재사용 제안, 내장 Python 제거 범위, 데이터 호환성 및 검증·삭제 조건 |

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
