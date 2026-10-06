# RACP 품질 보증 및 구현 검증 현황 (Quality Assurance)

> **문서 ID**: `DOC-QA-INDEX`  
> **상태**: Active · **기준 버전**: v0.1.8  
> **최종 개정일**: 2026-10-07 · **분류**: Quality Index

---

## 1. 개요 (Overview)

본 디렉터리는 **RACP** 플랫폼의 전 마일스톤 구현 현황, 자동화 테스트 및 물리 환경 실증 결과, 런타임 호환성 기준, 그리고 정식 릴리스 통과를 위한 필수 인수 게이트(Release Gates)를 관리합니다.

---

## 2. 수록 문서 목록

| 문서명 | 문서 ID | 분류 | 설명 |
| :--- | :--- | :--- | :--- |
| [구현 작업 및 검증 현황](implementation-status.md) | `DOC-QA-STATUS` | **SSOT** | Phase 0~9, 확장 마일스톤, 패키징, 2-PC 실증 및 자동화 테스트 종합 현황 단일 진실 공급원 |
| [런타임 및 플랫폼 호환성 매트릭스](compatibility.md) | `DOC-QA-COMPAT` | Specification | Python 3.12, Node 22, uv, pnpm, Chromium, Windows/Linux/macOS 버전 및 상태표 |
| [Windows 릴리스 인수 게이트](windows-release-gates.md) | `DOC-QA-GATES` | Verification | 보안, 멱등성, 수명주기, PTY, UIA, 데스크톱 등 정식 릴리스 합격 판정 기준 |

---

## 3. 품질 보증 원칙 (Anti-Bloat Policy)

- **일회성 결과 파일 생성 금지**: 과거 각 개발 페이즈마다 작성되었던 일회성 결과 파일(`phase-*-result.md`, `two-pc-*-result.md` 등)은 본 디렉터리의 [`implementation-status.md`](implementation-status.md)로 전수 집대성되었습니다.
- **증거 기반 검증 (Evidence-Based)**: 모든 통과 표시는 로컬 단위/통합 테스트 로그(`dist/test-results.xml`), 실제 OS 프로세스 관측 증거, 또는 물리 하드웨어 E2E 통과 로그에 기반해야 합니다.

---

## 4. 관련 문서

- [기술 문서 포털](../README.md)
- [시스템 명세서](../spec/README.md)
- [아키텍처 결정 레코드 (ADR)](../adr/README.md)
