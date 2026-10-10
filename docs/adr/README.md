# RACP 아키텍처 결정 레코드 (ADR Index)

> **문서 ID**: `DOC-ADR-INDEX`  
> **상태**: Active · **기준 버전**: v0.1.8  
> **최종 개정일**: 2026-10-07 · **분류**: Architecture Decision Records

---

## 1. 개요 (Overview)

본 디렉터리는 **RACP (Remote Access and Control Protocol)** 설계 및 개발 과정에서 도출된 핵심 아키텍처 결정(Architecture Decision Records, ADR) 총 29건을 수록합니다.  
모든 ADR은 **컨텍스트(Context)**, **결정 사항(Decision)**, **결과 및 영향(Consequences)**, **공식 기술 근거(References)**를 일관된 형식으로 명시합니다.

---

## 2. ADR 도메인별 분류 및 요약

### 2.1 기반 인프라 & 보안 (Foundation & Security)
| 번호 | 제목 | 상태 | 핵심 결정 요약 |
| :--- | :--- | :---: | :--- |
| [ADR-0001](ADR-0001-bootstrap-and-verification.md) | 구현 기준과 검증 범위 | **Accepted** | Python 3.12, uv workspace, 단일 worker Gateway, SQLite 저널, DPAPI/0600 보안 자격 증명 |
| [ADR-0018](ADR-0018-external-oauth-mcp-resource-server.md) | 외부 OAuth provider와 MCP resource server | **Accepted** | PKCE S256 기반 OAuth Protected Resource, RFC 8707 Resource Indicator, Keycloak OIDC 연동 |
| [ADR-0019](ADR-0019-protected-one-command-pc-enrollment.md) | 한 명령 PC 등록과 보호된 실행 설정 | **Accepted** | `racp-connect` 대화형 안전 등록, 10분 유효 1회용 토큰, DPAPI 암호화 설정 보존 |
| [ADR-0020](ADR-0020-retired-transient-read-reconciliation.md) | 만료된 임시 읽기 결과의 재연결 | **Accepted** | Gateway 보존 기간 만료 후 재생성된 임시 결과 Replay 시 감사 저널 correlation 확인 후 안전 복구 |

### 2.2 핵심 원격 제어 (Core Operations & Streaming)
| 번호 | 제목 | 상태 | 핵심 결정 요약 |
| :--- | :--- | :---: | :--- |
| [ADR-0002](ADR-0002-filesystem-process-contracts.md) | 파일 경로 고정과 managed process 소유권 | **Accepted** | 절대 경로 검증, 점진적 디렉터리 순회, Windows Job Object 프로세스 트리 격리 |
| [ADR-0003](ADR-0003-artifact-transfer-and-binary-write.md) | 재개 가능한 Artifact 전송과 binary write | **Accepted** | SHA-256 청크 분할 전송, Range 헤더 기반 전송 재개, 원자적 쓰기(Atomic Write) |
| [ADR-0004](ADR-0004-persistent-terminal.md) | persistent terminal과 resource inventory | **Accepted** | ConPTY 기반 영속 가상 터미널, 원격 연결 단절 시 세션 보존 및 재접속 |
| [ADR-0005](ADR-0005-terminal-stream-credit.md) | terminal stream 소비 ACK와 control 전송 | **Accepted** | 슬라이딩 윈도우 크레딧 제어, 백프레셔(Backpressure) 방지, 터미널 리사이즈 제어 |
| [ADR-0006](ADR-0006-job-admission-and-budget.md) | durable Job 접수와 실행 budget | **Accepted** | 비동기 Job 영속 큐잉, 타임아웃/취소 버짓 제어, 좀비 프로세스 방지 |
| [ADR-0007](ADR-0007-retention-and-output-recovery.md) | 실행 결과 보존과 출력 첨부 복구 | **Accepted** | 대용량 출력 파일의 자동 스풀링, Artifact 승격 및 TTL 기반 가비지 컬렉션 |
| [ADR-0008](ADR-0008-console-session-and-event-feed.md) | Console 인증과 realtime 관측 | **Accepted** | HTTP-only 쿠키/CSRF 보호, SSE(Server-Sent Events) 기반 실시간 이벤트 스트리밍 |
| [ADR-0021](ADR-0021-explicit-named-workspaces.md) | PC가 승인한 이름 있는 작업 폴더 | **Accepted** | 기본 default 외 최대 15개 명시적 허용 워크스페이스, `workspace_id` 경로 격리 |
| [ADR-0022](ADR-0022-user-background-agent-control.md) | 사용자 Agent의 백그라운드 수명 제어 | **Accepted** | `racp-agentctl` 명령 기반 백그라운드 시작/상태/정상종료, 프로세스 단일 인스턴스 락 |

### 2.3 브라우저 & 데스크톱 GUI 제어 (Browser & Desktop Automation)
| 번호 | 제목 | 상태 | 핵심 결정 요약 |
| :--- | :--- | :---: | :--- |
| [ADR-0009](ADR-0009-contained-browser-and-observations.md) | 격리 Browser worker와 관측 참조 | **Accepted** | Playwright Chromium 격리 프로세스, 폼 입력, 스크린샷 Artifact, CDP opt-in |
| [ADR-0010](ADR-0010-local-session-broker-and-input-fences.md) | 로컬 Session Broker와 입력 관측 경계 | **Accepted** | 세션 0 격리 우회, 대화형 데스크톱 Broker 및 네임드 파이프 보안 IPC |
| [ADR-0011](ADR-0011-nonadmin-agent-and-user-logon-broker.md) | 비관리자 Agent와 사용자 로그온 Broker | **Accepted** | 비관리자 서비스 계정 분리, 로그온 세션 사용자 SID 바인딩 |
| [ADR-0012](ADR-0012-ui-automation-and-input-watch.md) | UI Automation 관측과 입력 중단 hook | **Accepted** | Win32 UIA 트리 검사, 사용자 물리 마우스/키보드 감지 시 원격 입력 즉시 중단 |
| [ADR-0013](ADR-0013-independent-input-release-guardian.md) | Broker 강제 종료 후 입력 해제 | **Accepted** | 데스크톱 Broker 비정상 종료 시 입력 키/마우스 고착을 해제하는 독립 Guardian 프로세스 |

### 2.4 리버싱 엔지니어링 플러그인 (Reverse Engineering Plugins)
| 번호 | 제목 | 상태 | 핵심 결정 요약 |
| :--- | :--- | :---: | :--- |
| [ADR-0014](ADR-0014-allowlisted-plugin-supervisor.md) | Allowlist plugin protocol과 subprocess 수명 | **Accepted** | 엄격한 화이트리스트 기반 stdio JSON-RPC 플러그인 수퍼바이저, 수명주기 샌드박스 |
| [ADR-0015](ADR-0015-re-application-handles-and-policy.md) | Agent RE operation과 Handle 경계 | **Accepted** | 정적/동적 분석 핸들 추적, 세션 종료 시 리버싱 툴 리소스 원자적 회수 |
| [ADR-0016](ADR-0016-native-gdb-mi-launch-and-owned-target.md) | 실제 GDB/MI launch adapter와 소유 target 수명 | **Accepted** | GNU GDB Machine Interface (MI) 어댑터, 중단점/레지스터/메모리 덤프 제어 |
| [ADR-0017](ADR-0017-native-ghidra-headless-analysis.md) | 실제 Ghidra headless adapter와 private 분석 프로젝트 | **Accepted** | Ghidra Headless Analyzer 실행, 바이너리 디컴파일 및 함수 그래프 분석 |

### 2.5 데스크톱 클라이언트 & 패키징 (Desktop Client & Onboarding)
| 번호 | 제목 | 상태 | 핵심 결정 요약 |
| :--- | :--- | :---: | :--- |
| [ADR-0023](ADR-0023-cross-platform-desktop-client.md) | 공통 데스크톱 클라이언트와 OS별 Agent 패키지 | **Superseded** | ADR-0030으로 대체된 Electron/Python 배포 결정 |
| [ADR-0024](ADR-0024-windows-user-logon-client.md) | 사용자가 선택한 Windows 로그인 후 Agent 연결 | **Accepted** | HKCU 시작프로그램 등록, 사용자 로그인 시 무인 백그라운드 재접속 |
| [ADR-0025](ADR-0025-agent-dashboard-and-tray.md) | Agent 현황·최근 활동과 트레이 수명 | **Accepted** | 시스템 트레이 최소화, 최근 40개 활동 감사 스트림, 안전한 완전 종료 |
| [ADR-0026](ADR-0026-windows-installer-maintenance.md) | Windows 설치·제거의 Agent 정리와 상태 백업 | **Accepted** | NSIS 설치 프로그램, 버전 업그레이드 전 프로세스 정리, SHA-256 상태 백업 |
| [ADR-0027](ADR-0027-one-time-setup-and-safe-diagnostics.md) | 최초 등록과 저장된 설정의 안전한 진단 | **Accepted** | 온보딩 오류 코드별 안내, 설정 손상 시 재등록 폼 방지 및 안전 진단 |
| [ADR-0028](ADR-0028-connection-file-onboarding.md) | Gateway 연결 파일로 최초 PC 등록 | **Accepted** | 단일 `.racp` 연결 파일 드롭만으로 주소, 1회용 토큰, 사설 CA를 일괄 적용 |
| [ADR-0029](ADR-0029-local-settings-repair.md) | 보호된 PC 등록의 설정 수정과 복구 | **Accepted** | 등록된 Gateway 주소, 폴더 목록, 프로필 직접 수정 및 무결성 검증 복구 |

| [ADR-0030](ADR-0030-rust-tauri-client-and-native-agent.md) | Rust Agent와 Tauri 클라이언트 | **Accepted** | 기존 보호된 상태·프로토콜 유지, Python/Electron client 제거, native Windows 패키징 |

---

## 3. 관련 문서

- [기술 문서 포털](../README.md)
- [시스템 개발정의서 v1.1](../spec/racp-specification-v1.1.md)
- [구현 및 검증 현황](../quality/implementation-status.md)
