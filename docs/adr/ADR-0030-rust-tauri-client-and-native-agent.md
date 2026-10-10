# ADR-0030: Rust Agent와 Tauri 클라이언트

> **Document ID**: `DOC-ADR-0030`\
> **Status**: Accepted · **Target Version**: v0.1.21\
> **Last Updated**: 2026-10-10 · **Classification**: Architecture Decision Record

## 결정

클라이언트와 내장 Agent를 Rust로 전환하고 React 화면을 Tauri 호스트에 연결한다. ADR-0023의 Electron·Python 클라이언트 실행 및 배포 결정을 대체한다. Gateway·CLI·공통 서버 Python 패키지는 유지한다.

`racp-contract`는 기존 bridge/wire/schema를, `racp-core`는 DPAPI·로컬 정책·저널·경로를, `racp-runtime`은 Agent와 native provider를 담당한다. 같은 Rust Agent 실행 파일의 고정된 하위 명령으로 사용자 백그라운드 실행, SCM 서비스, 세션 Broker, 독립 Guardian 및 분석 도구 adapter를 제공한다. Chromium은 CDP로 직접 제어하며 Python worker를 실행하지 않는다.

## 배포와 상태 보존

Windows x64 기본 배포물은 NSIS setup, self-extracting portable EXE, ZIP이다. Rust binary, Chromium 1243, SHA-256으로 고정한 WebView2를 포함하고 Python·Node·Electron을 포함하지 않는다. 기존 credential/profile은 동일 사용자 소유권과 DPAPI를 검증하여 읽는다. 설치 변경은 소유한 Agent의 정리와 상태 백업을 확인한다. 포터블 상태는 별도로 격리한다.

서비스는 비관리자 계정과 활성 서비스 SID를 요구한다. 허용 사용자 세션의 Broker 등록은 Named Pipe peer의 실제 SID·session·PID·생성 시각·실행 파일과 등록 디렉터리 소유권을 확인한다. 다른 사용자의 비밀번호나 토큰으로 GUI를 시작하지 않는다.

## 검증 정책

> [!IMPORTANT]
> 사용자 지시로 전체 구현과 구 코드 제거를 먼저 수행하고 테스트는 보류한다. Windows production 빌드 및 패키징은 진행하지만 자동 테스트·GUI·설치/제거·실제 2-PC acceptance의 통과를 주장하지 않는다. 개발 산출물은 unsigned이며 macOS/Linux native 빌드는 별도 요청까지 보류한다.

현재 구현·빌드·미실행 검증의 근거는 [구현 현황](../quality/implementation-status.md)에 기록한다. 실행은 [Rust Agent 가이드](../guides/rust-agent-guide.md), 패키징은 [데스크톱 클라이언트 가이드](../guides/desktop-client-guide.md)를 따른다.

## 관련 문서

- [전환 설계](../spec/client-rust-migration.md)
- [구현 계획](../spec/client-rust-implementation-plan.md)
- [대체된 ADR-0023](ADR-0023-cross-platform-desktop-client.md)
