# RACP 런타임 및 플랫폼 호환성 매트릭스 (Compatibility Matrix)

> **문서 ID**: `DOC-QA-COMPAT`  
> **상태**: Active · **기준 버전**: v0.1.8  
> **최종 개정일**: 2026-10-07 · **분류**: Quality Assurance & Compatibility

---

## 개요 (Overview)

본 문서는 **RACP (Remote Access and Control Protocol)** 시스템의 각 구성 요소가 의존하는 런타임, 라이브러리, 브라우저 엔진, 외부 분석 도구 및 운영체제별 고정 버전과 실제 로컬 및 물리 환경에서의 검증 상태를 정의하는 단일 기준표입니다.

CI 및 배포 환경에서는 임의의 버전 플로팅(Version Floating)을 금지하며, `uv.lock` 및 `pnpm-lock.yaml`에 고정된 의존성을 엄격히 준수합니다.

---

## 목차 (Table of Contents)

- [1. 런타임 및 인프라 검증 현황](#1-런타임-및-인프라-검증-현황)
- [2. 프로토콜, 보안 및 UI 런타임](#2-프로토콜-보안-및-ui-런타임)
- [3. 리버싱 엔지니어링 및 외부 도구 런타임](#3-리버싱-엔지니어링-및-외부-도구-런타임)
- [4. 클라이언트 및 데스크톱 패키징](#4-클라이언트-및-데스크톱-패키징)
- [5. 잠정적 미검증 항목 및 향후 과제](#5-잠정적-미검증-항목-및-향후-과제)
- [6. 관련 문서](#6-관련-문서)

---

## 1. 런타임 및 인프라 검증 현황

| 소프트웨어 / 구성 요소 | 고정 버전 / 규격 | 현재 검증 상태 | 검증 근거 및 비고 |
| :--- | :--- | :---: | :--- |
| **Python** | CPython 3.12.11 (Windows x64) | **PASS** | 로컬 셸, Job Object, DPAPI 암호화 및 341개 테스트 검증 |
| **uv** | 0.12.11 | **PASS** | Frozen sync, monorepo workspace wheel 빌드 검증 |
| **Windows OS** | Windows 10 Pro x64 (10.0.19045) | **PASS** | 로컬 프로세스, ConPTY, UIA, Broker 실증 완료 |
| **Windows 11 x64** | 설계 참조 환경 | *Pending* | 설계 명세상 참조 대상이며 전용 CI 머신 인수 검증 대기 |
| **Ubuntu 24.04 LTS x64** | 설계 참조 환경 | *Pending* | GitHub Actions CI 매트릭스 구성 완료, 실제 호스트 런타임 검증 대기 |
| **Node.js** | 22.23.0 (Portable Windows x64) | **PASS** | SHA-256 체크섬 확인 및 실제 Console 프로덕션 빌드 완료 |
| **pnpm** | 11.19.0 | **PASS** | `pnpm-lock.yaml` 기반 고정 설치 및 일관성 검증 |

---

## 2. 프로토콜, 보안 및 UI 런타임

| 구성 요소 | 고정 버전 / 라이브러리 | 검증 상태 | 설명 |
| :--- | :--- | :---: | :--- |
| **MCP Python SDK** | 2.0.0 | **PASS** | Streamable HTTP SDK Client ↔ Gateway 실연동 검증 |
| **OAuth IdP / JWT** | Keycloak 26.8.0 / PyJWT 2.15.1 | **PASS** | 실제 HTTPS 로그인, 사용자 동의, PKCE S256 토큰 발급 및 MCP 툴 호출 통과 |
| **React / Vite** | React 19.3.0 / Vite 8.3.2 | **PASS** | 웹 브라우저 렌더링, SSE 이벤트 스트림, OpenAPI 클라이언트 통과 |
| **TypeScript** | 5.9.3 | **PASS** | OpenAPI 타입 생성 및 엄격한 정적 타입 검사 통과 |
| **Playwright Browser** | 1.63.0 (Chromium 1243 / 153.0.8010.12) | **PASS** | 격리 브라우저 인스턴스, 폼 입력, 스크린샷 아티팩트 저장 통과 |
| **UI Automation (Win32)**| comtypes 1.4.17 | **PASS** | Win32 UIA 트리 탐색, Value 패턴, Invoke 패턴, 포그라운드 안전 잠금 통과 |
| **JSON Schema** | jsonschema 4.26.0 / referencing 0.37.0 | **PASS** | 엄격한 화이트리스트 스키마 검증 및 외부 스키마 호출 원천 차단 |

---

## 3. 리버싱 엔지니어링 및 외부 도구 런타임

| 툴 / 프레임워크 | 버전 규격 | 상태 | 검증 상세 |
| :--- | :--- | :---: | :--- |
| **Ghidra Headless** | 12.1.4 (Oracle JDK 22.0.2) | **PASS** | Windows 10 환경에서 Headless 분석, 심볼 쿼리, 프로세스 크래시 복구 검증 |
| **GNU GDB** | 17.1 (x86_64-w64-mingw32) | **PASS** | RACP HTTP launch, 중단점 설정, 레지스터/메모리 덤프, 프로세스 정리 검증 |
| **LLVM-MinGW** | LLVM 23.1.1 (20260908 portable) | **PASS** | DWARF 4 / O0 C 테스트 바이너리 빌드 및 아티팩트 생성 검증 |
| **IDA Pro / x64dbg** | 미연동 (선택적 어댑터) | *Unverified* | v1.0 이후 선택적 플러그인 어댑터로 분리 유지 |

---

## 4. 클라이언트 및 데스크톱 패키징

| 패키지 | 버전 / 구성 | 상태 | 비고 |
| :--- | :--- | :---: | :--- |
| **Electron** | 44.5.1 | **PASS** | 네이티브 시스템 트레이, 렌더러 격리, 내부 IPC 보안 검증 |
| **electron-builder**| 26.15.3 | **PASS** | Windows x64 NSIS 설치 패키지 및 무설치 포터블 ZIP 생성 완료 |
| **물리 2-PC 통신** | 192.168.29.140 ↔ 192.168.29.141 | **PASS** | 물리 LAN 환경에서 파일 쓰기, ConPTY 셸 실행, 멱등성 1회 보장 실증 통과 |

---

## 5. 잠정적 미검증 항목 및 향후 과제

> [!NOTE]
> 다음 항목들은 설계상 정의되어 있으나 전용 엔터프라이즈 환경에서의 최종 인수가 남아있는 상태입니다:
> 1. Windows 서비스 계정(SCM) 기반의 무인 자동 시작 및 서로 다른 사용자 세션 Broker 구동.
> 2. 실제 서명 인증서(Authenticode) 서명 및 Microsoft SmartScreen 통과.
> 3. 공개 인터넷 환경에서의 광역 OAuth 운영 및 8시간 지속 Soak 부하 검증.

---

## 6. 관련 문서

- [기술 문서 포털](../README.md)
- [구현 작업 및 검증 현황](implementation-status.md)
- [Windows 릴리스 인수 게이트](windows-release-gates.md)
- [시스템 개발정의서 v1.1](../spec/racp-specification-v1.1.md)
