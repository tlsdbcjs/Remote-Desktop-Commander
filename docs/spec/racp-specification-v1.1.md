# Remote AI Control Platform (RACP) 마스터 소프트웨어 개발 정의서

> **문서 ID**: `DOC-SPEC-CORE-v1.1`  
> **상태**: Active · **기준 버전**: v0.1.8  
> **최초 작성일**: 2026-10-01 · **최종 개정일**: 2026-10-07  
> **분류**: Software Design & Master Architecture Specification  
> **참조 문서**: [Windows 엔지니어링 계획서](windows-engineering-plan.md) · [구현 및 검증 현황](../quality/implementation-status.md) · [ADR 색인](../adr/README.md)

---

## 메타데이터 및 설계 기준 안내

- **문서 상태**: 설계 기준선 수립 및 구현 검증 완료 (v0.1.8 기준선 유지).
- **아키텍처 원칙**: Python 중심 모노레포, 엄격한 외부 MCP 경계, 아웃바운드 WSS Agent, 명시적 Handle과 Artifact 원칙 준수.
- **규범적 용어**: 본 문서의 `MUST`, `SHOULD`, `MAY`는 각각 RFC 2119에 준하는 필수, 권장, 선택 사항을 의미합니다.
- **단일 진실 공급원**: 변경 사항 발생 시 [ADR](../adr/README.md) 및 [구현 현황](../quality/implementation-status.md)에 동기화하여 기록합니다.

---


# 0. AI 개발자에게 주는 최상위 지시

이 문서를 구현 지시서로 사용하는 AI 개발자는 다음 원칙을 반드시 따른다.

1. 이 문서에 정의된 **아키텍처 경계, 인터페이스, 프로토콜, 보안 경계, 테스트 기준**을 임의로 무너뜨리지 않는다.
2. 구현 중 더 나은 설계가 필요하면 코드를 먼저 바꾸지 말고 `docs/adr/ADR-XXXX-*.md`에 Architecture Decision Record를 작성한 뒤 변경한다.
3. 각 Phase는 독립적으로 실행 가능하고 테스트 가능해야 한다.
4. 각 Phase 종료 시 다음을 모두 통과해야 한다.
   - formatter
   - linter
   - static type checker
   - unit tests
   - contract tests
   - 해당 Phase의 integration/e2e tests
5. “나중에 구현”이라는 이유로 핵심 인터페이스를 비워두지 않는다. 다만 아직 지원하지 않는 capability는 명시적으로 `CAPABILITY_UNAVAILABLE`을 반환한다.
6. Agent와 Gateway 사이의 내부 통신은 **MCP에 종속시키지 않는다**. MCP는 외부 AI 인터페이스다.
7. 원격 PC의 상태를 MCP 연결 자체의 상태에 의존시키지 않는다. 터미널, 브라우저, 디버거 등 지속 상태는 명시적인 Handle로 관리한다.
8. 모든 원격 작업은 `trace_id`, `request_id`, `device_id`, `operation`을 포함한 구조화 로그를 남긴다.
9. 프로그램이 실패했을 때 “무슨 계층에서 실패했는지” 식별할 수 있어야 한다.
10. 모든 명령은 timeout과 cancellation semantics를 가진다.
11. 대용량 바이너리, 스크린샷, 덤프를 JSON에 base64로 반복 삽입하지 않는다. Artifact subsystem을 사용한다.
12. GUI/브라우저 제어는 가능한 경우 구조화 API를 우선하고 화면 좌표 제어는 fallback으로 사용한다.
13. Windows GUI 제어는 서비스 프로세스와 interactive user session을 분리한다.
14. 신뢰하지 않는 바이너리의 동적 분석은 Host가 아니라 disposable VM에서 실행할 수 있는 구조를 유지한다.
15. 제품 구현을 지시받았을 때는 README만 작성하고 끝내지 말고 실제 실행 가능한 코드와 build artifact를 생성한다. 문서 검토 요청만으로 구현·배포를 시작하지 않는다.
16. §78과 부록 E의 범위·상태·오류·재시도 계약을 구현한다. 설명용 Python 코드의 생략된 필드는 실제 schema에서 임의로 생략하지 않는다.
17. 실행 환경에서 확인하지 못한 OS·클라이언트·플러그인은 `UNVERIFIED`로 기록한다. 미검증을 지원 성공으로 보고하지 않는다.

---

# 1. 프로젝트 정의

## 1.1 프로젝트명

기본 프로젝트명:

`Remote AI Control Platform`

약칭:

`RACP`

CLI command:

```text
racp
```

Python package namespace:

```text
racp
```

웹 UI 명칭:

```text
RACP Console
```

이름은 추후 변경 가능하나 내부 protocol 및 package name 변경은 별도 ADR을 요구한다.

---

## 1.2 프로젝트 목표

RACP는 다음 사용 경험을 제공해야 한다.

AI에게:

```text
"win-re-01에서 C:\samples\sample.exe의 SHA256을 계산하고
PE header를 확인한 후 IDA 분석을 열어.
의심스러운 함수가 있으면 x64dbg를 attach해서 breakpoint를 걸어."
```

라고 요청했을 때 AI가 다음 계층을 자연스럽게 조합할 수 있어야 한다.

```text
AI
 │
 ├─ filesystem
 ├─ shell
 ├─ terminal
 ├─ process
 ├─ browser
 ├─ desktop
 ├─ static-re
 └─ debugger
       │
       ▼
Remote PC / Analysis VM
```

사용자는 AI가 원격 PC를 별도의 “원격 장비”로 느끼기보다 다음과 같이 **로컬 tool namespace가 확장된 것처럼** 사용해야 한다.

```text
local filesystem  ≈ remote filesystem
local shell       ≈ remote shell
local browser     ≈ remote browser
local desktop     ≈ remote desktop
local debugger    ≈ remote debugger
```

네트워크 경계가 있다는 사실은 인증, latency, permission, artifact transfer를 제외하면 대부분의 tool semantics에 드러나지 않아야 한다.

---

# 2. 범위

## 2.1 필수 범위

초기 정식 버전 `v1.0`은 다음을 MUST 지원한다. 중간 검증 릴리스 MVP는 Phase 0–5와 인증된 CLI를 포함한다. MVP 완료를 v1.0 완료로 부르지 않는다.

v1.0 RE의 필수 기준은 backend abstraction, External MCP bridge, **실제 static backend 한 개와 debugger backend 한 개**다. 참조 조합은 Ghidra + GDB이며 IDA/x64dbg를 선택해 대체할 수 있다. 모든 상용·선택 플러그인을 동시에 완성하는 것은 필수가 아니다. Frida는 선택 확장이다.

### Device
- 원격 Device 등록
- Device online/offline 상태
- Device capability discovery
- OS/architecture/version 조회
- heartbeat
- reconnect
- revoke

### Shell
- 비대화형 명령 실행
- stdout/stderr 분리
- exit code
- timeout
- cancellation
- environment variable override
- working directory 지정

### Terminal
- persistent PTY
- open/write/read/resize/close
- terminal handle
- process 종료 이벤트
- reconnect 후 session 상태 판단

### Filesystem
- read
- write
- append
- stat
- list
- mkdir
- copy
- move
- delete
- search
- hash
- path normalization
- binary artifact upload/download

### Process
- list
- inspect
- spawn
- terminate
- kill
- wait
- process tree

### Jobs
- long-running operation
- status
- progress
- result
- error
- cancellation

### Artifact
- screenshot
- binary
- dump
- archive
- log
- text
- metadata
- content hash

### Browser
- browser process/session 생성
- Playwright 기반 navigate
- snapshot
- screenshot
- click
- type
- keyboard
- evaluate
- tabs/pages
- close

### Desktop
- screenshot
- window listing
- foreground window
- activate window
- mouse move/click/double click/drag
- keyboard input
- text typing
- scroll
- screen size
- DPI awareness

### Reverse Engineering Extension
- static analysis backend abstraction
- debugger backend abstraction
- existing MCP bridge
- IDA/Ghidra/GDB/x64dbg/Frida adapter를 연결할 수 있는 plugin API

### Management
- CLI
- Web Console
- structured logs
- audit
- policy
- health/ready endpoints

---

## 2.2 v1.0에서 제외 가능한 범위

다음 기능은 구조만 고려하고 `v1.0` 필수 구현에서는 제외할 수 있다.

- 다중 사용자 조직/팀 RBAC
- SaaS billing
- Kubernetes
- multi-region
- Kafka
- Redis cluster
- mobile native application
- video streaming remote desktop
- full VNC/RDP replacement
- automatic malware verdict
- autonomous privilege escalation
- public marketplace plugin system

---

# 3. 핵심 아키텍처 결정

## 3.1 Client를 직접 구현해야 하는가?

**ChatGPT와 Codex용 MCP Client는 직접 구현하지 않는다.**

ChatGPT/Codex가 MCP Host/Client 역할을 수행한다.

우리가 구현하는 구성요소는 다음과 같다.

```text
┌──────────────────────────────┐
│ ChatGPT / Codex / MCP Client │  ← 외부, 구현하지 않음
└───────────────┬──────────────┘
                │ MCP
                ▼
┌──────────────────────────────┐
│ RACP Gateway                 │  ← 구현
│ MCP Server + Control Plane   │
└───────────────┬──────────────┘
                │ RACP Agent Protocol
                ▼
┌──────────────────────────────┐
│ RACP Agent                   │  ← 구현
└───────────────┬──────────────┘
                │
        ┌───────┴────────┐
        │ Local Drivers  │
        └────────────────┘
```

추가 구현 요소:

```text
RACP CLI
RACP Web Console
Windows Session Broker
Optional RE plugins
Optional OpenAI Computer-Use adapter
```

---

# 4. 언어 및 기술 스택 결정

## 4.1 최종 권고

RACP는 **Python-first backend/agent + TypeScript GUI** 구조로 구현한다.

### Python이 담당할 영역

```text
Gateway backend
MCP server
Device Agent
CLI
Protocol models
Policy engine
Process/PTY control
Filesystem
Windows integration
Browser automation backend
RE integrations
Plugin supervisor
```

### TypeScript가 담당할 영역

```text
Web Console
React UI
Browser-side state
Realtime dashboard
Optional desktop shell(Tauri/Electron)
Optional browser extension
```

---

## 4.2 왜 Agent를 TypeScript보다 Python으로 구현하는가

리버싱/시스템 자동화라는 본 프로젝트의 핵심 요구에서 Python이 더 유리하다.

주요 이유:

- `psutil`
- `pywin32`
- `pywinauto`
- `ctypes`
- `frida-python`
- `capstone`
- `unicorn`
- `lief`
- `pefile`
- `pyelftools`
- `playwright`
- `pillow`
- `httpx`
- 풍부한 GDB/LLDB/IDA scripting ecosystem
- Python subprocess/asyncio integration
- 빠른 plugin 개발
- AI가 생성/수정하기 쉬운 코드

Node/TypeScript도 process와 filesystem 제어가 가능하지만 Win32, RE 도구, 바이너리 분석, debugger script와의 결합에서는 Python이 자연스럽다.

따라서 **Agent 핵심을 TypeScript/Electron으로 구현하지 않는다.**

---

## 4.3 왜 GUI는 TypeScript인가

관리 GUI에는 TypeScript가 더 적합하다.

이유:

- React 생태계
- WebSocket realtime UI
- 브라우저 기반 배포
- typed API client
- dashboard 및 log viewer 구현 용이
- 필요 시 Tauri/Electron으로 wrapping 가능
- Gateway가 static frontend를 직접 serve 가능

기본 GUI는 **웹 애플리케이션**으로 구현한다.

즉 사용자는:

```text
http://localhost:PORT
```

또는 private network 주소를 통해 Console에 접속한다.

초기부터 Electron을 도입하지 않는다.

---

## 4.4 Rust/Go는 필요한가?

v1에서는 필수가 아니다.

Agent daemon의 장기 안정성이나 높은 성능이 실제 bottleneck으로 확인되면 일부를 Rust로 교체할 수 있도록 interface를 유지한다.

예:

```text
Python Agent
    │
    ├─ Python providers
    └─ Native helper
          └─ Rust executable
```

그러나 “언젠가 성능이 필요할 수 있다”는 이유만으로 v1부터 Rust/Go를 도입하지 않는다.

---

## 4.5 기준 버전

권장 기준:

```text
Python >= 3.12
Node.js >= 22
TypeScript >= 5.8
React >= 19
MCP Python SDK v2 stable line (공식 문서 확인, 실제 patch와 host 호환성은 Phase 0에서 고정)
```

정확한 patch/minor 버전은 lockfile에서 pin한다.

Python dependency:

```text
uv.lock
```

Node dependency:

```text
pnpm-lock.yaml
```

CI는 버전 floating을 금지한다. 위 `>=` 표기는 후보 하한이며 무제한 상위 버전 지원 약속이 아니다. Phase 0에서 실제 Python/Node/uv/pnpm 버전, OS 빌드, MCP SDK patch, Playwright와 browser revision을 `docs/compatibility.md`에 고정한다. v1 참조 런타임 후보는 Python 3.12.x와 Node 22.x이며 설치·보안 유지 여부 확인 후 lockfile을 생성한다. Python의 Windows extras에는 OS marker를 적용한다.

---

# 5. 전체 시스템 구조

```text
                              ┌─────────────────────┐
                              │   ChatGPT / Codex   │
                              │     MCP Client      │
                              └──────────┬──────────┘
                                         │
                               Streamable HTTP / MCP
                                         │
                                         ▼
                  ┌────────────────────────────────────┐
                  │            RACP Gateway            │
                  │                                    │
                  │ MCP Adapter                        │
                  │ Device Registry                    │
                  │ Operation Router                   │
                  │ Policy Engine                      │
                  │ Handle Manager                     │
                  │ Job Manager                        │
                  │ Artifact Manager                   │
                  │ Audit                              │
                  │ Web Console API                    │
                  └───────────────┬────────────────────┘
                                  │
                         WSS + device credential
                                  │
            ┌─────────────────────┼──────────────────────┐
            │                     │                      │
            ▼                     ▼                      ▼
┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐
│ Windows RE Device   │ │ Linux Analysis Box  │ │ Browser Workstation │
│                     │ │                     │ │                     │
│ RACP Agent Service  │ │ RACP Agent          │ │ RACP Agent          │
│         │           │ │                     │ │                     │
│ Session Broker      │ │ GDB / Frida         │ │ Playwright          │
│         │           │ │ Ghidra              │ │ Chrome              │
│ IDA / x64dbg        │ │                     │ │                     │
└─────────────────────┘ └─────────────────────┘ └─────────────────────┘
```

---

# 6. Architecture Style

RACP는 다음 원칙을 적용한다.

## 6.1 Hexagonal Architecture

도메인 및 application logic은 MCP, FastAPI, WebSocket, OS API를 직접 알지 않는다.

```text
External Adapter
      │
      ▼
Application Port
      │
      ▼
Domain
      │
      ▼
Infrastructure Adapter
```

예:

```python
class OperationExecutor(Protocol):
    async def execute(
        self,
        operation: Operation,
        context: ExecutionContext,
    ) -> OperationResult: ...
```

Gateway의 MCP handler는 이 interface를 호출할 뿐이다.

---

## 6.2 Plugin Architecture

Agent 기능은 capability provider로 분리한다.

```text
Agent Runtime
   │
   ├─ shell provider
   ├─ fs provider
   ├─ process provider
   ├─ terminal provider
   ├─ browser provider
   ├─ desktop provider
   ├─ re provider
   └─ external MCP bridge provider
```

Plugin 하나가 crash해도 전체 Agent가 반드시 종료될 필요는 없다.

외부 plugin은 subprocess로 격리할 수 있어야 한다.

---

## 6.3 Capability-based Architecture

Agent는 자신이 제공 가능한 capability를 Gateway에 선언한다.

예:

```json
{
  "device_id": "win-re-01",
  "capabilities": {
    "shell": "1.0",
    "filesystem": "1.0",
    "process": "1.0",
    "terminal": "1.0",
    "desktop": "1.0",
    "browser": "1.0",
    "ida": "0.1",
    "x64dbg": "0.1"
  }
}
```

Gateway는 존재하지 않는 capability 요청을 Agent까지 보내지 않는다.

---

# 7. 도메인 모델

RACP의 핵심 도메인은 다음 7개로 제한한다.

```text
Device
Capability
Operation
Handle
Job
Artifact
Policy
```

## 7.1 Device

```python
@dataclass(frozen=True)
class Device:
    id: DeviceId
    name: str
    platform: Platform
    architecture: str
    agent_version: str
    protocol_version: int
    status: DeviceStatus
    capabilities: tuple[Capability, ...]
```

Device 상태:

```text
ENROLLING
CONNECTING
ONLINE
DEGRADED
OFFLINE
REVOKED
```

---

## 7.2 Capability

```python
@dataclass(frozen=True)
class Capability:
    name: str
    version: str
    attributes: Mapping[str, Any]
```

Capability version은 Agent version과 독립적이어야 한다.

---

## 7.3 Operation

모든 실제 작업은 Operation으로 표현한다.

```python
@dataclass(frozen=True)
class Operation:
    name: str
    payload: Mapping[str, Any]
    timeout_ms: int
```

예:

```text
shell.exec
filesystem.read
terminal.write
browser.navigate
desktop.click
debugger.continue
```

---

## 7.4 Handle

지속 상태를 가진 리소스는 반드시 Handle을 반환한다.

```text
terminal
browser
debugger
analysis
interactive-process
```

Handle example:

```json
{
  "id": "term_01K...",
  "type": "terminal",
  "device_id": "win-re-01",
  "created_at": "...",
  "last_access_at": "...",
  "expires_at": null
}
```

MCP connection ID를 상태 식별자로 사용하지 않는다.

---

## 7.5 Job

장시간 동작은 Job이다.

상태:

```text
QUEUED
RUNNING
WAITING
CANCEL_REQUESTED
RECONCILING
COMPLETED
FAILED
CANCELLED
TIMED_OUT
UNKNOWN
```

---

## 7.6 Artifact

Artifact는 바이너리 및 대용량 결과를 저장한다.

```text
screenshot
memory-dump
file
archive
pcap
log
trace
report
```

Artifact metadata:

```json
{
  "id": "art_01K...",
  "device_id": "win-re-01",
  "media_type": "application/octet-stream",
  "size": 102400,
  "sha256": "...",
  "created_at": "..."
}
```

---

# 8. 저장소 구조

Python은 `uv workspace` 방식의 monorepo를 권장한다.

```text
racp/
│
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .editorconfig
├── .gitignore
├── .env.example
│
├── apps/
│   ├── gateway/
│   │   ├── pyproject.toml
│   │   └── src/racp_gateway/
│   │       ├── main.py
│   │       ├── bootstrap.py
│   │       ├── config.py
│   │       ├── mcp/
│   │       ├── http/
│   │       ├── ws/
│   │       ├── services/
│   │       └── persistence/
│   │
│   ├── agent/
│   │   ├── pyproject.toml
│   │   └── src/racp_agent/
│   │       ├── main.py
│   │       ├── runtime/
│   │       ├── transport/
│   │       ├── providers/
│   │       ├── plugins/
│   │       └── platform/
│   │
│   ├── cli/
│   │   ├── pyproject.toml
│   │   └── src/racp_cli/
│   │
│   └── session_broker/
│       ├── pyproject.toml
│       └── src/racp_session_broker/
│
├── packages/
│   ├── domain/
│   │   └── src/racp_domain/
│   ├── protocol/
│   │   └── src/racp_protocol/
│   ├── policy/
│   │   └── src/racp_policy/
│   ├── observability/
│   │   └── src/racp_observability/
│   ├── sdk/
│   │   └── src/racp_sdk/
│   └── testkit/
│       └── src/racp_testkit/
│
├── web/
│   └── console/
│       ├── package.json
│       ├── pnpm-lock.yaml
│       ├── vite.config.ts
│       └── src/
│
├── plugins/
│   ├── ida/
│   ├── ghidra/
│   ├── gdb/
│   ├── x64dbg/
│   ├── frida/
│   └── external_mcp/
│
├── deploy/
│   ├── docker/
│   ├── windows/
│   ├── linux/
│   └── compose/
│
├── scripts/
│   ├── bootstrap.py
│   ├── build.py
│   ├── package.py
│   └── e2e.py
│
├── tests/
│   ├── contract/
│   ├── integration/
│   ├── e2e/
│   └── chaos/
│
└── docs/
    ├── architecture/
    ├── protocol/
    ├── threat-model/
    └── adr/
```

---

# 9. Dependency Rule

다음 dependency 방향을 위반하면 안 된다.

```text
domain
  ↑
application/services
  ↑
adapters
```

허용:

```text
gateway -> domain
agent -> domain
gateway -> protocol
agent -> protocol
provider -> domain
```

금지:

```text
domain -> FastAPI
domain -> MCP SDK
domain -> WebSocket library
domain -> SQLAlchemy
domain -> OS API
```

---

# 10. Gateway 설계

Gateway의 책임:

```text
MCP endpoint
Device connection registry
Agent request routing
Authentication
Policy evaluation
Handle registry
Job registry
Artifact registry
Audit
Console API
Health metrics
```

Gateway는 사용자 요청의 원격 OS 명령을 실행하지 않는다. Gateway 자체의 저장·백업·운영 작업은 이 제약과 구분한다.

---

## 10.1 Gateway 내부 모듈

```text
gateway/
│
├── mcp_adapter
├── operation_service
├── device_service
├── connection_registry
├── handle_service
├── job_service
├── artifact_service
├── policy_service
├── audit_service
└── console_api
```

---

## 10.2 MCP Tool Surface

MCP tool은 지나치게 세분화하지 않는다.

v1 권장 tool:

### Device

```text
device_list
device_info
device_capabilities
```

### Shell

```text
shell_exec
```

### Terminal

```text
terminal_open
terminal_write
terminal_read
terminal_resize
terminal_close
```

### Filesystem

```text
fs_read
fs_write
fs_list
fs_stat
fs_search
fs_mkdir
fs_copy
fs_move
fs_delete
fs_hash
```

### Process

```text
process_list
process_info
process_spawn
process_terminate
```

### Job

```text
job_get
job_cancel
```

### Artifact

```text
artifact_info
artifact_read
```

### Browser

```text
browser_open
browser_navigate
browser_snapshot
browser_screenshot
browser_click
browser_type
browser_key
browser_evaluate
browser_close
```

### Desktop

```text
desktop_screenshot
desktop_windows
desktop_activate
desktop_click
desktop_type
desktop_key
desktop_scroll
desktop_drag
```

### RE

```text
re_backends
re_open
re_query
re_command
re_close

debugger_backends
debugger_launch
debugger_attach
debugger_command
debugger_close
```

위 목록은 최소 노출안이다. 필수 기능과 tool 간 누락을 막기 위해 부록 E2의 매핑을 추가한다. `re_query`, `re_command`, `debugger_command`는 자유 문자열만 받지 않고 지원 action별 입력 schema를 가진 tagged union으로 정의한다. raw backend 명령은 별도 권한이 필요하다. Phase 0에서 operation registry를 만들고, 각 Phase에서 지원하는 schema와 테스트를 채운다.

---

# 11. MCP Adapter 규칙

MCP Adapter는 다음만 수행한다.

```text
validate MCP input
↓
map to domain command
↓
call application service
↓
map domain result to MCP result
```

Agent routing, permission logic, DB query를 MCP handler 안에 직접 작성하지 않는다.

---

# 12. Gateway HTTP endpoint

기본 endpoint:

```text
POST /mcp
GET  /healthz
GET  /readyz
GET  /metrics

GET  /api/v1/devices
GET  /api/v1/jobs
GET  /api/v1/audit

WS   /agent/v1/connect
```

Console static:

```text
GET /
GET /assets/*
```

---

# 13. Agent 설계

Agent는 원격 PC에 설치되는 long-running daemon이다.

책임:

```text
Gateway outbound connection
Authentication
Capability registration
Operation dispatch
Local provider lifecycle
PTY/process lifecycle
Plugin lifecycle
Artifact upload
Health reporting
```

Agent는 inbound network port를 기본적으로 열지 않는다.

---

# 14. Agent Provider Interface

모든 capability는 다음 interface concept를 따른다.

```python
class CapabilityProvider(Protocol):
    @property
    def manifest(self) -> CapabilityManifest: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def execute(
        self,
        operation: Operation,
        context: ExecutionContext,
    ) -> OperationResult: ...

    async def cancel(self, operation_id: str) -> None: ...

    async def health(self) -> HealthStatus: ...
```

각 provider는 다른 provider 구현에 직접 의존하지 않는다.

공통 coordination이 필요한 경우 Application Service를 통해 수행한다.

---

# 15. Agent Provider 목록

v1 MUST:

```text
ShellProvider
FilesystemProvider
ProcessProvider
TerminalProvider
ArtifactProvider
BrowserProvider
DesktopProvider (Windows first)
ExternalMcpProvider
```

v1 extension:

```text
IdaProvider
GhidraProvider
GdbProvider
X64dbgProvider
FridaProvider
```

---

# 16. Internal Agent Protocol

Gateway와 Agent는 MCP와 독립적인 RACP Protocol 1을 WSS로 교환한다. Agent가 연결을 시작한다. message type의 wire 표기는 소문자로 통일한다.

```json
{
  "protocol": 1,
  "type": "request",
  "request_id": "req_01",
  "operation_id": "op_01",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "device_id": "dev_01",
  "agent_boot_id": "boot_01",
  "connection_epoch": 7,
  "timestamp": "2026-10-01T00:00:00Z",
  "operation": "shell.exec",
  "timeout_ms": 30000,
  "remaining_timeout_ms": 28500,
  "execution_mode": "sync",
  "idempotency_key": "client-generated-unique-key",
  "context": {
    "principal_id": "owner_01",
    "execution_profile_id": "standard",
    "policy_revision": 3
  },
  "payload": {"mode": "argv", "argv": ["python", "--version"]}
}
```

`operation_id`는 논리 실행 식별자이고 `request_id`는 전송 시도 식별자다. context는 인증된 Gateway가 생성하며 Agent의 로컬 정책을 넓힐 수 없다. `device_id`는 credential과 일치해야 한다. `deadline_ms`처럼 절대시각과 기간을 혼동하는 필드는 사용하지 않는다.

Agent는 실행 전 durable execution journal을 commit한 뒤 `ack`를 보낸다. ACK는 접수 확인이며 성공 응답이 아니다. result/error는 동일 correlation 필드와 `operation_id`, `agent_boot_id`, `connection_epoch`를 포함한다.

```json
{
  "protocol": 1,
  "type": "error",
  "request_id": "req_01",
  "operation_id": "op_01",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "device_id": "dev_01",
  "agent_boot_id": "boot_01",
  "connection_epoch": 7,
  "error": {
    "code": "EXECUTION_UNKNOWN",
    "message": "execution outcome could not be recovered",
    "layer": "agent",
    "retryable": false,
    "execution_state": "unknown",
    "details": {"next_action": "operation_get"}
  }
}
```

메시지별 필수·금지 필드, handshake와 stream 계약은 부록 E3–E6에 정의한다. 설명용 ID는 예시이며 실제 schema는 형식·길이를 검증한다.

---

# 17. Agent Protocol Message Types

wire message types:

```text
hello welcome heartbeat capabilities
request ack result error cancel
stream_open stream_data stream_ack stream_end
event reconcile
```

`stream_data`는 PTY 등의 증분 텍스트다. 바이너리 대용량 전송은 Artifact HTTPS API로 분리한다. 취소 요청의 접수와 실제 종료는 별개다. `event`에 `operation.cancel_requested`, `operation.state_changed`, `capability.changed` 등 registry에 등록한 event name만 사용한다.

---

# 18. Protocol Versioning

Agent version과 protocol version을 분리한다.

```json
{
  "agent_version": "0.3.0",
  "supported_protocols": [1]
}
```

Gateway는 연결 시 common protocol을 선택한다.

Capability도 별도 version을 가진다.

```text
filesystem 1.0
terminal 1.1
desktop 1.0
```

---

# 19. 오류 코드

기존 오류 코드를 유지하고 아래 집합을 stable public code로 사용한다.

```text
INVALID_ARGUMENT UNAUTHENTICATED PERMISSION_DENIED
DEVICE_OFFLINE DEVICE_REVOKED CAPABILITY_UNAVAILABLE OPERATION_NOT_SUPPORTED
HANDLE_NOT_FOUND HANDLE_EXPIRED JOB_NOT_FOUND OPERATION_NOT_FOUND
PROCESS_NOT_FOUND PROCESS_EXITED PATH_NOT_FOUND PATH_ACCESS_DENIED
TIMEOUT CANCELLED CONFLICT PLUGIN_ERROR TRANSPORT_ERROR INTERNAL_ERROR
PROTOCOL_MISMATCH STALE_CONNECTION EXECUTION_UNKNOWN CANCEL_NOT_SUPPORTED
IDEMPOTENCY_CONFLICT RESOURCE_EXHAUSTED RATE_LIMITED CURSOR_EXPIRED
ARTIFACT_NOT_FOUND ARTIFACT_EXPIRED CHECKSUM_MISMATCH
APPROVAL_REQUIRED APPROVAL_EXPIRED PRECONDITION_FAILED
SESSION_UNAVAILABLE SESSION_LOCKED STALE_OBSERVATION INTEGRITY_LEVEL_MISMATCH
```

모든 error는 `code`, `message`, `layer`, `retryable`, `execution_state`를 가진다. `layer`는 gateway/transport/agent/provider/os 중 하나, `execution_state`는 not_started/running/completed/unknown 중 하나다. 선택 필드는 `details`, `retry_after_ms`, `job_id`, `operation_id`다. stack trace와 secret을 공개 응답에 포함하지 않는다.

`retryable=true`만으로 mutation을 다시 실행하지 않는다. 기존 operation을 조회하고 §55의 재시도 규칙을 적용한다. HTTP/MCP 변환은 부록 E2를 따른다.

---

# 20. Shell Provider

기본 mode는 shell을 경유하지 않는 `argv`다.

```json
{
  "mode": "argv",
  "argv": ["python", "--version"],
  "cwd": "C:\\work",
  "env": {"FOO": "bar"},
  "timeout_ms": 30000,
  "execution_mode": "sync"
}
```

문자열 명령은 `mode: shell`, `shell: powershell|pwsh|cmd|bash`, `command`를 모두 명시한다. argv와 command를 동시에 받지 않는다. 문자열을 임의로 split하지 않으며 shell executable은 Agent 설정의 승인된 경로로 해석한다.

출력은 `operation_id`, `exit_code`, `stdout`, `stderr`, `duration_ms`, `truncated`, `artifact_id`, `termination_reason`, `cleanup_status`를 포함한다. 비정상 exit code는 프로그램 실행 결과이며 RPC 장애와 구분한다. timeout/cancel에서는 exit_code가 null일 수 있으며 오류 details에 수집된 부분 결과를 포함한다.

- 환경은 Agent 서비스 secret을 제거한 실행 profile 기본 환경에 env를 병합한다. 값 null은 삭제, Windows 키는 대소문자를 구분하지 않는다. 원격 payload로 인증 정보·loader 관련 보호 변수를 덮어쓸 수 없다.
- cwd 생략 시 실행 profile에 고정한 workspace root를 사용한다. 존재하지 않으면 실패하며 프로세스 전역 cwd를 변경하지 않는다.
- stdout/stderr를 동시에 읽고 byte limit을 적용한다. UTF-8 기본과 명시적 encoding override를 지원하며 decoding 오류 수를 보고한다. 원본 bytes가 필요하면 Artifact를 제공한다.
- 일반 요청은 요청 수명에 종속된다. 분리 실행은 `execution_mode: job`를 시작 전에 지정한다. 타임아웃 후 같은 명령을 새 Job으로 다시 실행하지 않는다.
- 프로세스 트리 종료와 output 상한은 부록 E5와 E8을 따른다.

---

# 21. Terminal Provider

Terminal은 PTY 기반이다.

API semantics:

```text
open
write
read
resize
close
```

Open result:

```json
{
  "handle_id": "term_...",
  "pid": 1234,
  "cols": 120,
  "rows": 40
}
```

Windows:

```text
ConPTY / pywinpty
```

Linux/macOS:

```text
pty / pexpect-compatible implementation
```

Terminal output은 ring buffer를 유지한다.

권장:

```text
default 4 MiB
configurable
```

`terminal_read`은 offset/cursor 또는 sequence 기반으로 중복 없는 읽기를 지원한다.

---

# 22. Filesystem Provider

Filesystem path는 Agent OS 기준 native path다.

반드시 처리:

```text
path normalization
symlink/junction
permissions
binary/text
large file
atomic write
hash
```

쓰기 API는 overwrite 여부를 명시한다.

중요 파일 수정 시 atomic write 권장:

```text
temp file
fsync
rename
```

대용량 파일은 Artifact channel을 사용한다.

---

# 23. Process Provider

모델:

```python
class ProcessInfo(BaseModel):
    pid: int
    ppid: int | None
    name: str
    exe: str | None
    cmdline: list[str]
    username: str | None
    status: str
    create_time: float | None
```

구현은 `psutil`을 우선 사용한다.

Process tree 조회가 가능해야 한다.

---

# 24. Job subsystem

장기 작업을 request-response에 묶지 않는다.

예:

```text
full disk search
IDA auto analysis
memory dump
large archive
long Frida trace
```

Job API:

```text
start internally
job_get
job_cancel
```

Job result가 Artifact를 반환할 수 있다.

---

# 25. Artifact subsystem

Artifact metadata는 DB에 저장하고 payload는 파일 또는 object storage에 저장한다.

v1 storage:

```text
local filesystem
```

향후:

```text
S3 / MinIO
```

interface:

```python
class ArtifactStore(Protocol):
    async def put(...)
    async def open(...)
    async def stat(...)
    async def delete(...)
```

디스크 layout 예:

```text
data/artifacts/
  ab/
    cd/
      <sha256>
```

content-addressed storage를 권장한다.

---

# 26. Browser Provider

기본 구현은 **Python Playwright**를 사용한다.

Browser를 Computer Use 좌표 제어로 먼저 구현하지 않는다.

우선순위:

```text
DOM/Playwright
↓
CDP
↓
Desktop fallback
```

Browser Handle:

```text
browser_...
```

각 Browser session은 page 목록을 가진다.

지원:

```text
navigate
snapshot
screenshot
click
type
key
evaluate
new_page
close_page
close
```

기본적으로 RACP 전용 browser profile을 사용한다.

사용자의 평상시 Chrome profile에 자동 attach하지 않는다.

CDP attach는 별도 옵션으로 허용한다.

---

# 27. Browser snapshot

AI용 snapshot은 screenshot만 반환하지 않는다.

가능하면 다음 구조 데이터를 반환한다.

```text
URL
title
accessibility tree / semantic DOM
interactive elements
focused element
viewport
```

Screenshot은 Artifact로 별도 반환한다.

---

# 28. Desktop / Computer Control Provider

Desktop Provider 목적은 **GUI fallback**이다.

구현 API:

```python
class DesktopDriver(Protocol):
    async def screenshot(self) -> ArtifactRef: ...
    async def list_windows(self) -> list[WindowInfo]: ...
    async def activate_window(self, window_id: str) -> None: ...
    async def move(self, x: int, y: int) -> None: ...
    async def click(self, x: int, y: int, button: str) -> None: ...
    async def double_click(self, x: int, y: int, button: str) -> None: ...
    async def drag(self, start: Point, end: Point) -> None: ...
    async def type_text(self, text: str) -> None: ...
    async def key(self, keys: list[str]) -> None: ...
    async def scroll(self, dx: int, dy: int) -> None: ...
```

---

# 29. Windows GUI Architecture

Windows Service는 Session 0에서 실행될 수 있으므로 GUI 조작을 Agent Service에 직접 넣지 않는다.

구조:

```text
┌──────────────────────────┐
│ RACP Agent Service       │
│                          │
│ Network                  │
│ Auth                     │
│ Operations               │
│ Plugin supervisor        │
└────────────┬─────────────┘
             │
       Named Pipe / local IPC
             │
┌────────────▼─────────────┐
│ RACP Session Broker      │
│ interactive user session │
│                          │
│ screenshot               │
│ mouse                    │
│ keyboard                 │
│ window API               │
│ IDA/x64dbg GUI bridge    │
└──────────────────────────┘
```

Agent Service는 interactive session의 Broker를 찾는다.

여러 세션이 존재하면 session ID를 선택할 수 있어야 한다.

---

# 30. Windows Desktop 구현 후보

우선순위:

```text
Win32 APIs
UI Automation
pywinauto
SendInput
Pillow/ImageGrab or native capture
```

`pyautogui`는 fallback으로만 사용해도 된다.

DPI scaling을 반드시 고려한다.

좌표 기준은 명확히 정의한다.

권장:

```text
physical pixel coordinate
```

Screenshot metadata:

```json
{
  "width": 2560,
  "height": 1440,
  "dpi_scale": 1.25,
  "monitor": 0
}
```

---

# 31. RE Architecture

리버싱 도구는 AI에게 가능한 한 GUI가 아니라 구조화된 tool로 제공한다.

구조:

```text
                    RACP RE layer
                          │
            ┌─────────────┴────────────┐
            │                          │
   StaticAnalysisBackend        DebuggerBackend
            │                          │
      ┌─────┴─────┐            ┌──────┴─────────┐
      │           │            │                │
     IDA       Ghidra          GDB            x64dbg
```

---

# 32. StaticAnalysisBackend

```python
class StaticAnalysisBackend(Protocol):
    async def open(self, target: Target) -> AnalysisHandle: ...
    async def close(self, handle: AnalysisHandle) -> None: ...
    async def info(self, handle: AnalysisHandle) -> AnalysisInfo: ...
    async def functions(self, handle: AnalysisHandle) -> list[FunctionInfo]: ...
    async def disassemble(self, handle: AnalysisHandle, address: int) -> Disassembly: ...
    async def decompile(self, handle: AnalysisHandle, address: int) -> Decompilation: ...
    async def xrefs(self, handle: AnalysisHandle, address: int) -> list[Xref]: ...
    async def strings(self, handle: AnalysisHandle) -> list[StringEntry]: ...
    async def rename(self, handle: AnalysisHandle, address: int, name: str) -> None: ...
    async def comment(self, handle: AnalysisHandle, address: int, text: str) -> None: ...
```

모든 backend가 모든 기능을 지원할 필요는 없다.

Capability metadata에 operation support를 표시한다.

---

# 33. DebuggerBackend

```python
class DebuggerBackend(Protocol):
    async def launch(self, executable: str, args: list[str]) -> DebuggerHandle: ...
    async def attach(self, pid: int) -> DebuggerHandle: ...
    async def close(self, handle: DebuggerHandle) -> None: ...
    async def continue_(self, handle: DebuggerHandle) -> StopEvent: ...
    async def step_into(self, handle: DebuggerHandle) -> StopEvent: ...
    async def step_over(self, handle: DebuggerHandle) -> StopEvent: ...
    async def set_breakpoint(self, handle: DebuggerHandle, address: int) -> Breakpoint: ...
    async def remove_breakpoint(self, handle: DebuggerHandle, breakpoint_id: str) -> None: ...
    async def registers(self, handle: DebuggerHandle) -> Mapping[str, int]: ...
    async def read_memory(self, handle: DebuggerHandle, address: int, size: int) -> bytes: ...
    async def backtrace(self, handle: DebuggerHandle) -> list[StackFrame]: ...
```

---

# 34. Existing MCP Bridge

기존 MCP 서버를 재사용하기 위한 bridge를 반드시 설계한다.

예:

```text
RACP Agent
   │
   └─ ExternalMcpProvider
          │
          ├─ IDA MCP
          ├─ Ghidra MCP
          └─ other MCP
```

Agent는 local MCP server를 stdio 또는 localhost transport로 spawn/connect할 수 있다.

외부 MCP tool schema를 그대로 Gateway에 무제한 노출하는 대신 RACP capability namespace로 mapping하거나 explicit passthrough mode를 사용한다.

---

# 35. Policy Architecture

Gateway 정책과 Agent의 로컬 제한을 모두 통과해야 실행된다. 판정 우선순위는 **DENY > REQUIRE_APPROVAL > ALLOW**이며 일치하지 않는 요청은 DENY다. 인증 실패·정책 파싱 실패·정책 저장소 장애에서 허용으로 전환하지 않는다.

개인용 `trusted_personal`은 명시적으로 활성화하는 profile이다. 초기 설치는 enrolled Device와 선택한 실행 계정에 대한 최소 read-only profile을 사용한다. OS command를 문자열 필터로 안전하게 sandbox할 수 있다고 가정하지 않는다.

```yaml
profile: trusted_personal
default_decision: deny
rules:
  - operations: [filesystem.read, filesystem.write, filesystem.stat, filesystem.list]
    decision: allow
  - operations: [shell.exec]
    decision: allow
  - operations: [desktop.click, desktop.type, desktop.key]
    decision: allow
  - operations: [system.shutdown, system.privileged]
    decision: require_approval
  - operations: [agent.modify_security]
    decision: deny
```

operation은 registry의 canonical name으로만 지정한다. 그룹 규칙은 registry에 선언한 operation 목록으로 확장한다. `shell.execute`, `desktop.input` 같은 미등록 별칭을 조용히 허용하지 않는다. 예시 system operation은 확장 예약명이며 provider가 없으면 CAPABILITY_UNAVAILABLE다.

요청자는 자격증명에서 principal로 결정하고 각 Device/Handle/Job/Artifact마다 소유권을 검사한다. 단일 사용자 버전에도 객체 수준 권한 검사는 필수다. 승인 계약은 §106, 인증은 부록 E7을 따른다.

---

# 36. Privilege Separation

Agent를 기본적으로 Administrator/root로 실행하지 않는다.

필요 시 별도 Privileged Helper를 둔다.

```text
Agent
  │
  │ narrow IPC
  ▼
Privileged Helper
```

Helper가 제공하는 operation은 최소화한다.

예:

```text
install debugger driver
capture privileged process metadata
VM snapshot integration
```

---

# 37. Device Enrollment

v1 Device credential은 TLS 위의 고엔트로피 opaque token으로 통일한다. 사용되지 않는 key pair는 생성하지 않는다. mTLS/key-bound 인증은 별도 ADR로 도입한다.

1. 인증된 owner가 `racp device create-enrollment`로 256-bit 이상 난수의 1회 토큰을 만든다. 기본 유효기간은 10분이며 Gateway는 원문 대신 digest를 저장한다.
2. Agent는 인증서와 hostname을 검증한 HTTPS enrollment endpoint에 토큰을 제출한다. TLS 검증 우회 옵션은 릴리스에서 허용하지 않는다.
3. Gateway는 토큰 소비와 Device/credential 생성을 하나의 DB transaction으로 처리한다. 동시 제출 중 한 번만 성공한다.
4. Device credential을 한 번 반환하고 Agent의 secure store에 저장한다. 분실한 응답은 새 enrollment로 복구하며 이미 사용한 토큰을 재사용하지 않는다.
5. WSS upgrade의 Authorization header로 인증한다. URL query, CLI history, 일반 YAML, 로그에 토큰을 넣지 않는다. CLI 기본 입력은 숨김 prompt 또는 `--token-stdin`이다.

Device credential은 자기 Device의 연결·상태 보고·할당된 artifact transfer만 허용하며 다른 Device 제어 또는 Console 관리 API에는 사용할 수 없다. rotation/revoke 및 오프라인 실행 lease는 부록 E7을 따른다.

---

# 38. Credential Storage

Windows:

```text
DPAPI 또는 machine/user protected store
```

Linux:

```text
0600 permission file
또는 keyring
```

credential을 일반 YAML에 평문으로 넣지 않는다.

---

# 39. Threat Model

최소 위협:

```text
Prompt injection
Malicious webpage
Untrusted binary
Compromised analysis VM
Credential theft
Arbitrary shell execution
Accidental destructive command
Sensitive file exfiltration
Agent compromise
Gateway compromise
Plugin compromise
```

중요 원칙:

**Policy allowlist는 완전한 security boundary가 아니다.**

진짜 격리는:

```text
OS account
VM
hypervisor
filesystem permissions
network isolation
```

이 담당한다.

---

# 40. Reversing / Untrusted Binary Isolation

권장 구성:

```text
Physical Host
│
├─ Control/Gateway
│
└─ Analysis VM
    │
    ├─ RACP Agent
    ├─ IDA
    ├─ x64dbg
    ├─ Frida
    └─ Target
```

더 강한 격리:

```text
Trusted Agent
   │
   └─ Guest Worker
       └─ Analysis VM
```

Guest에는 장기 Gateway credential을 두지 않는다.

---

# 41. Database

개인용 v1:

```text
SQLite
```

multi-user 확장:

```text
PostgreSQL
```

ORM:

```text
SQLAlchemy 2.x
Alembic
```

주요 table:

```text
devices
device_capabilities
device_credentials
handles
jobs
artifacts
audit_events
policies
enrollment_tokens
```

---

# 42. Audit

모든 high-level operation은 audit event를 생성한다.

```json
{
  "id": "aud_...",
  "timestamp": "...",
  "actor": "mcp:codex",
  "device_id": "win-re-01",
  "operation": "shell.exec",
  "decision": "allow",
  "status": "success",
  "trace_id": "tr_...",
  "duration_ms": 123
}
```

민감한 command payload는 configurable redaction을 제공한다.

---

# 43. Observability

모든 process에 structured logging을 적용한다.

권장:

```text
structlog
OpenTelemetry
```

필수 correlation:

```text
trace_id
request_id
device_id
operation
job_id(optional)
handle_id(optional)
```

---

# 44. Metrics

최소 metrics:

```text
racp_agent_connected
racp_agent_disconnect_total

racp_rpc_requests_total
racp_rpc_errors_total
racp_rpc_duration_seconds

racp_active_handles
racp_active_jobs

racp_artifact_bytes_total

racp_plugin_failures_total
racp_browser_sessions
racp_terminal_sessions
```

---

# 45. Health

Gateway:

```text
/healthz
/readyz
```

Agent internal health report:

```text
transport
filesystem
process
terminal
browser
desktop
plugins
```

CLI:

```text
racp doctor
racp doctor <device>
```

예:

```text
Gateway             OK
Database            OK
Artifact store      OK
Device win-re-01    ONLINE
  shell             OK
  filesystem        OK
  terminal          OK
  browser           OK
  desktop           OK
  ida               OFFLINE
  x64dbg            OK
```

---

# 46. CLI

CLI framework:

```text
Typer
```

필수 command:

```text
racp gateway run

racp device list
racp device info <id>
racp device create-enrollment
racp device revoke <id>

racp exec <device> -- <command>

racp terminal open <device>
racp terminal attach <handle>

racp job list
racp job get <id>
racp job cancel <id>

racp artifact info <id>
racp artifact download <id>

racp audit tail

racp doctor
racp doctor <device>
```

CLI는 Gateway public/application API를 사용한다.

Agent 내부 class를 직접 import해서 우회하지 않는다.

---

# 47. Web Console

기술:

```text
React
TypeScript
Vite
TanStack Query
Zod
```

선택:

```text
Zustand
```

v1 화면:

```text
Dashboard
Devices
Device detail
Capabilities
Running jobs
Active sessions
Artifacts
Audit log
Settings
Doctor
```

Device detail:

```text
status
platform
agent version
last seen
capabilities
running processes
active handles
health
```

Console에서 arbitrary shell 실행 UI를 제공할 수 있다.

---

# 48. GUI Native Packaging

기본은 Web Console이다.

Native app이 필요한 경우 v2에서:

```text
Tauri + existing React Console
```

을 우선 고려한다.

Electron은 브라우저 extension이나 Node integration이 반드시 필요한 경우에만 고려한다.

---

# 49. Configuration

Gateway:

```yaml
gateway:
  bind: 127.0.0.1
  port: 8765

database:
  url: sqlite:///./data/racp.db

artifacts:
  backend: filesystem
  path: ./data/artifacts

logging:
  level: INFO
  format: json

policy:
  profile: read_only  # trusted_personal은 인증된 owner가 명시적으로 선택
```

Agent:

```yaml
agent:
  name: win-re-01

gateway:
  url: wss://gateway.example/agent/v1/connect

providers:
  shell:
    enabled: true
  filesystem:
    enabled: true
  terminal:
    enabled: true
  browser:
    enabled: true
  desktop:
    enabled: true
```

secret은 별도 secure storage에 보관한다.

---

# 50. Dependency 후보

## Gateway/Agent Python

```text
mcp
fastapi
uvicorn
websockets
pydantic
pydantic-settings
sqlalchemy
alembic
aiosqlite
httpx
structlog
opentelemetry-api
opentelemetry-sdk
typer
rich
psutil
aiofiles
platformdirs
cryptography
```

## Optional Windows

```text
pywin32
pywinauto
pywinpty
Pillow
```

## Optional Browser

```text
playwright
```

## Optional RE

```text
frida
capstone
unicorn
lief
pefile
pyelftools
```

의존성은 extras로 분리한다.

예:

```text
racp-agent[windows,browser,re]
```

---

# 51. Dependency Injection

Global singleton 남용을 금지한다.

bootstrap에서 dependency graph를 조립한다.

```python
def build_gateway(config: GatewayConfig) -> GatewayApplication:
    db = create_database(config.database)
    artifact_store = FileArtifactStore(config.artifacts)
    connection_registry = ConnectionRegistry()
    policy = PolicyEngine(...)
    ...
    return GatewayApplication(...)
```

테스트에서는 in-memory fake를 주입한다.

---

# 52. Cancellation

취소는 실행 결과가 아닌 의도 전달이다. Gateway는 `cancel(operation_id)`를 Agent에 전달하고 Agent는 `CANCEL_REQUESTED`를 기록한다. Provider coroutine 취소만으로 OS process가 종료되었다고 판단하지 않는다.

프로세스 작업은 graceful stop → 기본 3초 대기 → 소유한 process tree 강제 종료 → 최대 2초 확인 순서다. 종료 확인 후 CANCELLED, 종료 여부가 불명확하면 UNKNOWN과 cleanup_status를 반환한다. 이미 COMPLETED인 작업에 늦게 도착한 취소는 결과를 바꾸지 않는다.

MCP transport의 취소 신호는 SDK adapter에서 RACP cancellation으로 변환한다. 동기 요청의 응답 stream 종료는 best-effort 취소로 처리한다. 의도된 장기 실행은 미리 Job으로 접수하고 즉시 job_id를 반환하므로, 이후 polling 연결 종료가 Job을 취소하지 않는다. 최신/구버전 MCP 차이는 §126을 따른다.

Provider별 cancel 지원, 원자적 commit 이후 취소 불가 구간, attach한 기존 process의 detach 규칙은 registry와 부록 E5에 기록한다.

---

# 53. Timeout

시간 단위는 정수 milliseconds다. 요청 `timeout_ms`는 Gateway가 실행 접수를 수락한 시점부터 queue·전송·실행을 포함한다. 승인 대기는 별도 TTL이며 승인 이후 새 실행 budget을 시작한다.

Gateway와 Agent는 duration 측정에 monotonic clock을 사용한다. Gateway는 전송 직전 remaining_timeout_ms를 계산한다. Agent는 수신 시 이 값으로 로컬 deadline을 잡는다. 전송 지연으로 Agent deadline이 늦어질 수 있으므로 Gateway는 자신의 deadline에 취소를 전달하고, 연결 단절 시 Agent의 제한된 execution lease가 상한을 제공한다. timestamp는 로그용 UTC이며 장비 간 clock 동기화가 실행 정확성 전제는 아니다.

기본 timeout: filesystem.stat 5초, filesystem.read 30초, shell.exec 60초, browser.click 10초, desktop.click 5초. 동기 실행 상한은 120초, Job은 기본 1시간·상한 24시간이다. 더 긴 작업은 operation/profile별 명시적 설정을 요구한다. 종료 확인을 위한 cleanup grace 최대 5초는 실행 budget과 별도로 보고한다.

MCP 동기 대기 시간은 기본 20초다. 이 시간을 넘길 가능성이 있는 호출은 시작 전에 Job으로 접수한다. 먼저 실행한 뒤 timeout을 이유로 같은 명령을 Job으로 재시작하지 않는다.

---

# 54. Reconnect

Agent는 0.5초부터 최대 30초까지 exponential backoff와 full jitter로 재연결하며 60초 안정 연결 후 backoff를 초기화한다. heartbeat는 10초, Gateway는 3회 누락 시 OFFLINE으로 표시한다.

연결마다 Gateway가 증가하는 connection_epoch를 발급한다. 같은 Device의 이전 연결은 폐쇄하고 이전 epoch의 새 요청·상태 갱신을 거부한다. Agent boot_id는 Agent runtime 재시작마다 바뀐다. credential 복제 의심은 경고/audit에 기록한다.

연결 복구 시 active handles, jobs, execution journal, 마지막 event cursor를 제한된 페이지로 reconcile한다. Gateway DB가 정책·소유권의 원본이고 Agent journal이 실제 실행 사실의 원본이다. 둘 중 하나의 보고만으로 미확정 실행을 성공으로 바꾸지 않는다.

Gateway 재시작만으로 Agent의 살아 있는 Handle을 무조건 만료시키지 않는다. 같은 boot_id의 리소스는 재인증·소유권 확인 후 복구한다. Agent 재시작 시 v1 PTY/browser/debugger handle은 복원하지 않으며 HANDLE_EXPIRED를 반환한다. 진행 중이던 비멱등 작업은 결과를 확인할 수 없으면 UNKNOWN으로 남긴다. 상세 정책은 부록 E4다.

---

# 55. Idempotency

모든 side effect 작업에 idempotency_key가 필요하다. 파일 삭제·쓰기·프로세스 시작·터미널 입력·GUI 입력도 예외가 아니다. Gateway 내부 재전송은 같은 operation_id/key를 유지한다. 새 MCP 호출의 JSON-RPC ID는 재시도 식별자로 사용하지 않는다.

키 범위는 `(principal_id, device_id, operation, idempotency_key)`다. 정규화된 payload·실행 profile·대상 Handle을 canonical JSON digest로 비교한다. 같은 키+같은 입력은 기존 상태/결과를 반환하고 다른 입력이면 IDEMPOTENCY_CONFLICT다. digest에 env secret이 들어가도 원문은 journal/log에 기록하지 않는다.

Gateway와 Agent는 실행 전 durable journal을 기록하고 완료 상태를 보존한다. 완료 후 기본 24시간은 같은 키를 중복 실행하지 않는다. 활성 작업 기록은 TTL로 제거하지 않는다. 키 유효기간 밖 요청은 같은 키의 재실행을 거부하며, 사용자가 새로운 작업으로 판단한 경우에만 새 키를 쓴다. 부록 E4의 key expiry/tombstone 규칙을 적용한다.

외부 OS 부작용과 DB commit을 하나의 transaction으로 묶을 수 없으므로 exactly-once를 보장하지 않는다. 실행 후 기록 전 crash에서는 EXECUTION_UNKNOWN을 반환하고 자동 재실행하지 않는다. 읽기 전용 요청은 제한된 backoff 재시도가 가능하며 mutation은 상태 조회·재조정 이후에도 실행되지 않았다는 증거가 있을 때만 동일 논리 작업으로 전송한다.

---

# 56. Backpressure

PTY output 또는 log stream이 소비 속도보다 빠를 수 있다.

반드시 buffer limit을 둔다.

정책:

```text
ring buffer
drop oldest + dropped byte count
또는 artifact spill
```

무제한 memory buffer를 금지한다.

---

# 57. Computer-Use Compatibility

RACP의 기본 ChatGPT/Codex 통합은 MCP tool이다.

즉 AI가:

```text
desktop_screenshot
desktop_click
desktop_type
```

를 tool로 호출한다.

추가로 자체 OpenAI API 기반 agent application을 만들 경우 다음 adapter를 구현할 수 있다.

```text
OpenAI computer action
        ↓
ComputerUseAdapter
        ↓
DesktopDriver
```

`DesktopDriver` 자체는 OpenAI API에 의존하지 않는다.

---

# 58. Local-like Usability Layer

“원격인데 로컬처럼 느끼는 것”을 위해 다음 UX를 구현한다.

## 58.1 Default Device

Gateway 사용자 설정에:

```text
default_device
```

를 둘 수 있다.

단, AI tool schema에서는 가능하면 device ID를 명시한다.

---

## 58.2 Workspace

선택적으로 Workspace 개념을 둔다.

```text
workspace = device + working directory + environment
```

예:

```json
{
  "id": "ws_...",
  "device_id": "win-re-01",
  "cwd": "C:\\work\\sample",
  "env": {}
}
```

이후:

```text
shell_exec(workspace_id=...)
fs_read(workspace_id=..., path="main.py")
```

와 같이 상대 경로를 사용할 수 있다.

---

## 58.3 Path Semantics

AI에게 항상 다음 metadata를 제공한다.

```text
platform
path separator
case sensitivity
home path
temp path
```

---

# 59. Testing Strategy

테스트는 5층으로 구성한다.

## Unit

```text
domain
policy
protocol validation
state machine
path normalization
job lifecycle
```

## Contract

```text
Gateway ↔ Agent
Gateway ↔ MCP
Agent ↔ Provider
Agent ↔ Session Broker
```

## Integration

실제:

```text
SQLite
filesystem
process
PTY
Playwright
```

사용.

## E2E

```text
MCP client
↓
Gateway
↓
Agent
↓
actual local test machine/container
```

## Chaos

```text
Agent disconnect
Gateway restart
Browser crash
Plugin crash
Timeout
Malformed frame
Duplicate request
Partial stream
Artifact disk full
```

---

# 60. Test Naming

예:

```text
test_shell_exec_returns_exit_code
test_shell_exec_times_out
test_terminal_survives_multiple_reads
test_agent_reconnect_updates_capabilities
test_duplicate_request_is_not_executed_twice
test_browser_session_isolated_profile
```

---

# 61. Fake Agent

Gateway 테스트를 위해 `FakeAgent`를 구현한다.

```python
class FakeAgent:
    async def connect(...)
    async def register_capabilities(...)
    async def respond(...)
    async def disconnect(...)
```

네트워크 없이 Gateway core test가 가능해야 한다.

---

# 62. Fake Gateway

Agent 테스트용 Fake Gateway도 제공한다.

이를 통해:

```text
Agent reconnect
Agent cancellation
Agent heartbeat
```

을 deterministic하게 테스트한다.

---

# 63. Coding Standards

Python:

```text
ruff format
ruff check
mypy --strict (서드파티 stub 예외는 모듈별 근거 기록)
pytest
```

TypeScript:

```text
eslint
prettier
tsc --noEmit
vitest
```

함수는 가능한 한 typed 한다.

`Any` 남용을 금지한다.

---

# 64. Python Style

- public API는 type annotation 필수
- blocking OS call은 asyncio event loop를 막지 않도록 thread executor 고려
- cancellation propagation 고려
- exception을 무조건 `except Exception: pass`로 삼키지 않는다
- platform-specific code는 `platform/windows`, `platform/linux`로 분리
- domain object에서 Pydantic에 직접 의존하지 않는 것을 우선 고려
- transport schema에는 Pydantic 사용 가능

---

# 65. Security Coding

금지:

```text
eval
exec on untrusted payload
shell=True by default
pickle over network
unsafe YAML
unbounded decompression
plaintext long-lived token in log
```

Command string mode에서 shell invocation이 필요한 경우 명시적으로 옵션을 요구한다.

---

# 66. Data Migration

DB schema는 Alembic migration을 사용한다.

코드 startup에서 자동 destructive migration을 하지 않는다.

개인용 편의를 위해:

```text
racp db upgrade
```

를 제공한다.

---

# 67. Build System

Python package manager:

```text
uv
```

Frontend:

```text
pnpm
```

build orchestrator:

```text
scripts/build.py
```

Makefile은 optional convenience wrapper로만 사용한다.

---

# 68. 개발 환경 Bootstrap

Linux/macOS:

```bash
git clone <repo>
cd racp

uv sync --all-packages --extra browser --dev  # Windows extras는 해당 OS에서 별도 추가

corepack enable
pnpm -C web/console install --frozen-lockfile

uv run playwright install
```

Windows PowerShell에서도 같은 개념으로 동작해야 한다.

---

# 69. 개발 실행

Gateway:

```bash
uv run racp-gateway
```

Agent:

```bash
uv run racp-agent --config ./agent.yaml
```

Console:

```bash
pnpm -C web/console dev
```

CLI:

```bash
uv run racp device list
```

---

# 70. Quality Gate

CI 또는 local pre-release에서 다음 순서를 통과한다.

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict apps packages plugins
uv run pytest

pnpm -C web/console lint
pnpm -C web/console typecheck
pnpm -C web/console test
pnpm -C web/console build
```

---

# 71. Build

Frontend:

```bash
pnpm -C web/console build
```

출력:

```text
web/console/dist
```

release build에서 Gateway static directory로 copy 또는 package data로 포함한다.

Python wheel:

```bash
uv build --all-packages
```

---

# 72. Agent Packaging

v1 권장:

```text
PyInstaller --onedir
```

이유:

- cross-platform
- 개인용 배포에 충분
- onefile보다 service startup/AV false-positive 측면에서 다루기 쉬움

Windows output:

```text
dist/
  racp-agent/
    racp-agent.exe
    ...
  racp-session-broker/
    racp-session-broker.exe
```

향후 Nuitka binary build를 선택적으로 추가할 수 있다.

---

# 73. Windows 설치

설치 패키지:

```text
WiX Toolset 또는 Inno Setup
```

설치 대상:

```text
C:\Program Files\RACP\
```

데이터:

```text
C:\ProgramData\RACP\
```

Agent:

```text
Windows Service
```

Session Broker:

```text
로그인 사용자 세션에서 자동 시작
```

Service와 Broker 사이에는 Named Pipe ACL을 설정한다.

---

# 74. Linux 설치

권장:

```text
/opt/racp
/etc/racp/agent.yaml
/var/lib/racp
/var/log/racp
```

systemd unit:

```text
racp-agent.service
```

서비스 user:

```text
racp
```

GUI/Desktop control은 별도 user service를 고려한다.

---

# 75. Gateway 배포

개인용 기본:

```text
Docker Compose
```

구성:

```text
gateway
volume(data)
```

SQLite 사용 시 Gateway replica는 1개다.

Postgres로 전환해도 WS connection owner routing, distributed lock, shared artifact store가 추가로 필요하다. DB 전환만으로 여러 Gateway replica를 실행하지 않는다. v1은 process worker도 1개다.

---

# 76. Docker Compose 예시 구조

```yaml
services:
  gateway:
    image: ${RACP_GATEWAY_IMAGE:?set a verified release tag or digest}
    ports:
      - "127.0.0.1:8765:8765"
    volumes:
      - ./data:/data
    environment:
      RACP_DATABASE_URL: sqlite:////data/racp.db
      RACP_ARTIFACT_PATH: /data/artifacts
```

컨테이너 내부 bind는 0.0.0.0:8765로 설정하고 host publish는 위처럼 loopback으로 제한한다. 외부 노출 시 TLS reverse proxy 또는 secure tunnel과 인증을 함께 사용한다. image digest, healthcheck, non-root UID, data volume 권한, shutdown grace를 실제 배포 파일에 명시한다.

---

# 77. MCP Exposure Mode

## Mode A: Local Codex

```text
Codex
  ↓
localhost/private network
  ↓
RACP Gateway MCP
```

## Mode B: ChatGPT/Codex via private tunnel

```text
OpenAI
  ↓
Secure MCP tunnel
  ↓
local Gateway
```

## Mode C: Public HTTPS

```text
ChatGPT/Codex
  ↓
HTTPS
  ↓
reverse proxy
  ↓
Gateway
```

개인용 기본은 A 또는 B다.

---

# 78. Support Matrix

v1 참조 플랫폼은 **Windows 11 x64 interactive workstation**과 **Ubuntu 24.04 LTS x64**다. 정확한 OS build와 패키지 버전은 Phase 0에서 검증해 고정한다. 그 외 Windows Server, Linux 배포판, ARM64, macOS는 검증 전 지원을 약속하지 않는다.

| 기능 | Windows 참조 환경 | Linux 참조 환경 | macOS |
|---|---|---|---|
| Shell / Filesystem / Process / PTY | MUST | MUST | 후속 확장 |
| Browser | MUST | MUST | 후속 확장 |
| Desktop | MUST, 활성화된 사용자 세션 필요 | SHOULD, backend별 opt-in | 후속 확장 |
| RE plugin API / External MCP bridge | MUST | MUST | 후속 확장 |
| 실제 static backend | Ghidra 또는 IDA를 검증한 OS 한 곳 이상에서 MUST | 동일 릴리스 기준 적용 | 선택 |
| 실제 debugger backend | x64dbg 또는 GDB를 검증한 OS 한 곳 이상에서 MUST | 동일 릴리스 기준 적용 | 선택 |
| IDA / x64dbg 개별 adapter | 선택, backend/version/license 명시 | 지원 가능한 adapter만 선택 | 선택 |
| Frida | 선택 확장 | 선택 확장 | 선택 |

권장 v1 검증 조합은 Windows Desktop + Linux Ghidra/GDB다. Windows 중심 사용성을 위해 IDA/x64dbg를 추가할 수 있으나 라이선스나 설치 환경이 없을 때 성공한 것처럼 대체하지 않는다. 제품 전체의 실제 static/debugger 각 1개 연동 조건은 유지한다.

capability는 installed/supported/enabled/healthy 상태를 구분하고 unavailable_reason을 반환한다. 설치되었으나 로그온 세션이 없는 desktop은 shell까지 OFFLINE으로 만들지 않는다.

---

# 79. 개발 Phase

## Phase 0 — Repository/Architecture Bootstrap

구현:

- monorepo
- domain package
- protocol package
- config
- logging
- lint/type/test setup
- ADR directory
- CI skeleton
- compatibility matrix와 dependency lock 검증
- operation registry 초안 및 부록 E 상세 계약 schema
- 실행 계정·인증·artifact 접근 경계 ADR

완료 조건:

```text
all format/lint/type/unit pass
gateway hello endpoint works
agent process boots
```

---

# 80. Phase 1 — Agent Connection

구현:

```text
Gateway WSS endpoint
Agent outbound connect
HELLO/WELCOME
heartbeat
device registry
capability registration
reconnect
enrollment / device auth / credential revoke
Gateway owner auth / baseline policy / audit
execution journal / connection fencing
```

E2E:

```text
agent start
↓
gateway device_list
↓
device appears ONLINE
↓
agent stop
↓
device becomes OFFLINE
```

---

# 81. Phase 2 — Shell

구현:

```text
shell.exec
timeout
cancel
cwd
env
stdout/stderr
mutation idempotency / operation status lookup
minimal Job acceptance / minimal Artifact spill
```

E2E:

```text
MCP client
↓
shell_exec(device_id=device, mode="argv", argv=["python", "--version"], idempotency_key="phase2-001")
↓
exit_code == 0
```

이 Phase 완료 시 **Codex → MCP → Gateway → Agent → command** 전체 경로가 검증되어야 한다.

---

# 82. Phase 3 — Filesystem + Process

구현:

```text
fs
process
artifact basic
```

Acceptance:

- text read/write
- binary transfer
- directory list
- process list
- process termination test

---

# 83. Phase 4 — PTY

구현:

```text
terminal_open
terminal_write
terminal_read
terminal_resize
terminal_close
```

Acceptance scenario:

```text
open PowerShell/bash
cd test-dir
start python REPL
send expression
read result
exit
```

---

# 84. Phase 5 — Job + Artifact

구현:

```text
long-running jobs
artifact content store
hash
download
cleanup
```

Acceptance:

100MB test artifact를 JSON base64 없이 전송 가능해야 한다.

---

# 85. Phase 6 — Web Console

구현:

- dashboard
- device list
- device detail
- jobs
- audit
- doctor
- terminal diagnostic UI

Acceptance:

브라우저에서 Device online status가 realtime으로 변경된다.

---

# 86. Phase 7 — Browser

구현:

```text
Playwright Provider
browser sessions
snapshot
screenshot
navigation
interaction
```

Acceptance:

테스트 웹 페이지에서:

```text
open
navigate
snapshot
click
type
read resulting DOM
screenshot
```

성공.

---

# 87. Phase 8 — Desktop / Computer Control

Windows first.

구현:

```text
Session Broker
Named Pipe
screenshot
window list
activate
mouse
keyboard
```

Acceptance:

Calculator/Notepad 같은 테스트 프로그램을 열고:

```text
window detect
activate
click/type
screenshot
```

성공.

---

# 88. Phase 9 — RE Plugin Layer

구현:

```text
StaticAnalysisBackend
DebuggerBackend
ExternalMcpProvider
```

먼저 fake backend로 contract test를 만든다.

그 후 실제:

```text
IDA or Ghidra
GDB or x64dbg
```

최소 하나씩 연동한다.

---

# 89. Phase 10 — Frida / Dynamic Instrumentation

Frida integration은 선택 plugin으로 추가한다. Phase 10을 생략해도 v1.0 release gate를 통과할 수 있으며, 포함하면 아래 기능에 해당하는 검증을 반드시 수행한다.

지원 후보:

```text
enumerate processes
attach
spawn
load script
enumerate modules
read memory
basic hook event stream
detach
```

Agent core에 Frida-specific model을 직접 넣지 않는다.

---

# 90. Phase 11 — Packaging

구현:

```text
PyInstaller
Windows installer
systemd unit
Docker gateway
release zip
checksums
```

Acceptance:

새 Windows VM에 installer만으로 Agent 설치 가능.

---

# 91. Phase 12 — Security Hardening

이 단계는 이미 구현한 보호 장치를 종합 검증하는 단계다. 인증·Device revoke·권한 검사·audit·실행 journal은 Phase 1–2부터 필수다. Artifact quota는 Phase 3, broker ACL은 Phase 8과 함께 구현한다.

최종 검증:

- credential rotation 및 오프라인 revoke lease 만료
- approval payload 변조·재사용·만료 거부
- 잘못된 Origin·issuer·audience·object owner 접근 거부
- audit redaction 및 storage failure 처리
- rate limit, artifact quota, 부분 업로드 cleanup
- protocol fuzz·duplicate execution·restart fault injection
- clean install, upgrade rollback, backup restore

보안 관련 필수 검증 실패를 Known limitation으로 바꾸어 v1.0 완료로 처리하지 않는다.

---

# 92. CI/CD

GitHub Actions 기준 추천 jobs:

```text
python-quality
python-unit
python-integration-linux
python-integration-windows
frontend-quality
frontend-test
frontend-build
protocol-contract
package-windows
package-linux
docker-build
```

tag release:

```text
vX.Y.Z
```

생성:

```text
gateway image
windows agent installer
linux agent archive/package
sha256sums
SBOM
```

---

# 93. Versioning

Semantic Versioning:

```text
MAJOR.MINOR.PATCH
```

Protocol compatibility는 app version과 독립적이다.

Breaking protocol change는 protocol version 증가.

---

# 94. Release Checklist

```text
[ ] tests pass
[ ] protocol contract pass
[ ] migrations tested
[ ] Windows clean install tested
[ ] Linux clean install tested
[ ] reconnect tested
[ ] credential revoke tested
[ ] artifact cleanup tested
[ ] browser test passed
[ ] desktop test passed
[ ] SBOM generated
[ ] checksums generated
[ ] release notes written
```

---

# 95. Definition of Done — v1.0

v1.0은 다음 시나리오가 모두 성공해야 한다.

## Scenario A — Shell

AI:

```text
win-re-01에서 whoami와 python --version 실행해
```

결과 성공.

## Scenario B — File

AI:

```text
C:\work\sample.txt를 읽고 수정해
```

결과 성공.

## Scenario C — Persistent Terminal

AI:

```text
PowerShell을 열고 현재 디렉터리를 바꾼 뒤 여러 명령을 이어서 실행해
```

같은 session에서 성공.

## Scenario D — Browser

AI:

```text
원격 Chrome을 열고 테스트 페이지에 접속해 폼을 입력해
```

Playwright로 성공.

## Scenario E — GUI

AI:

```text
원격 PC의 Notepad 창을 찾아 활성화하고 텍스트를 입력해
```

Desktop Provider로 성공.

## Scenario F — RE

AI:

```text
분석 backend를 열고 함수 목록/디컴파일 또는 debugger 상태를 질의해
```

최소 한 static backend + 한 debugger backend에서 성공.

---

# 96. 성능 목표

다음은 측정 전의 release 목표다. 기준 환경은 Gateway 4 vCPU/8 GiB, Agent 4 vCPU/8 GiB, SSD, 1 Gbps LAN, RTT 10ms 이하, Device 1대·동시 일반 요청 4개다. OS/CPU/browser/version을 결과에 함께 기록한다.

warm-up 20회 후 200회 측정한 p50/p95/p99 및 실패율을 보고한다. 아래 값은 p95 목표이며 앱 실행 시간과 network RTT 포함 여부를 구분한다.

| 지표 | p95 목표 | 측정 범위 |
|---|---:|---|
| device_list | 100ms | 인증된 Gateway API 왕복 |
| shell dispatch overhead | 100ms | Agent noop fixture 실행으로 명령 자체 시간 분리 |
| filesystem.stat | 100ms | warm local file, API 왕복 |
| desktop screenshot | 800ms | 1920×1080 1모니터 PNG, 저장 완료·metadata 응답까지 |
| desktop input | 150ms | API 호출부터 OS dispatch 확인까지 |
| browser click overhead | 300ms | 준비된 local fixture, navigation 대기 제외 |

구조 변경 없이 목표를 완화하려면 측정 근거와 ADR을 남긴다. 10 Device·합계 40 일반 요청의 혼합 부하를 30분, PTY 4개+browser 2개를 8시간 soak test한다. 유휴로 돌아온 뒤 RSS가 warm baseline의 120% 이내이고 orphan process/미종료 handle이 없어야 한다. 실패율은 deterministic fixture에서 0%, timing outlier는 별도 분석한다.

---

# 97. Resource Limits

개인용 기본값이며 설정 변경 시 validation과 metric을 제공한다.

| 항목 | 기본 제한 |
|---|---:|
| 실행 중 일반 operation / Device | 16 |
| 대기 operation / Device | 64 |
| terminal / Device | 8 |
| browser context / Device | 4 |
| RE heavyweight Job / Device | 2 |
| WS text frame / 전체 JSON message | 1 MiB |
| stream_data chunk | 64 KiB |
| inline text 결과 | 64 KiB |
| PTY ring buffer / session | 4 MiB |
| screenshot 원본 | 20 MiB |
| Artifact 한 개 / store 총량 | 1 GiB / 10 GiB |
| 동시 artifact transfer / Device | 2 |
| shell stdout+stderr 총 수집량 | 64 MiB |
| mutation result 보존 | 완료 후 24시간 이상 |
| artifact TTL / audit retention | 7일 / 30일 |

PTY ring에서 사라진 bytes는 cursor gap으로 알린다. shell 출력 수집 상한 초과는 기본적으로 작업을 종료하고 RESOURCE_EXHAUSTED+부분 결과를 반환한다. 64 KiB 이후는 Artifact로 spill하며 quota 실패를 숨기지 않는다.

control queue는 heartbeat/cancel/reconcile을 위한 용량을 따로 확보한다. 일반 queue 포화는 RESOURCE_EXHAUSTED와 retry_after_ms를 반환하며 무제한 대기하지 않는다. 메모리·디스크 사용량, output cap 적용, queue depth를 관측한다.

---

# 98. Debuggability Requirements

어떤 실패도 최소 다음 질문에 답할 수 있어야 한다.

```text
어느 요청인가?
어느 Device인가?
어느 operation인가?
Gateway에서 실패했나?
Agent에서 실패했나?
Provider에서 실패했나?
OS process에서 실패했나?
retry 가능한가?
```

모든 error log에는 correlation ID가 있어야 한다.

---

# 99. `racp doctor` 상세 요구사항

검사:

Gateway:

```text
HTTP
database
artifact directory
MCP registration
WebSocket acceptor
```

Device:

```text
agent heartbeat
protocol version
provider health
disk access
shell
PTY
browser availability
desktop broker
RE plugin endpoint
```

`--json` 옵션을 제공한다.

---

# 100. Architecture Decision Records

최초 ADR을 생성한다.

```text
ADR-0001-python-first-agent-and-gateway.md
ADR-0002-typescript-react-console.md
ADR-0003-mcp-only-external-boundary.md
ADR-0004-websocket-agent-transport.md
ADR-0005-explicit-resource-handles.md
ADR-0006-artifact-store-for-binary-results.md
ADR-0007-windows-session-broker.md
ADR-0008-sqlite-first.md
ADR-0009-plugin-capability-model.md
ADR-0010-outbound-only-agent.md
```

---

# 101. 초기 API Schema 예시

## shell_exec

```json
{
  "device_id": "dev_01",
  "mode": "argv",
  "argv": ["python", "--version"],
  "cwd": null,
  "env": {},
  "timeout_ms": 30000,
  "execution_mode": "sync",
  "idempotency_key": "client-unique-001"
}
```

sync 결과 예:

```json
{
  "operation_id": "op_01",
  "exit_code": 0,
  "stdout": "Python 3.12.x\n",
  "stderr": "",
  "duration_ms": 87,
  "truncated": false,
  "artifact_id": null,
  "termination_reason": "exited",
  "cleanup_status": "complete"
}
```

job 접수 결과는 `operation_id`, `job_id`, `state: QUEUED`, `poll_after_ms`를 반환하며 stdout 성공 결과와 혼합하지 않는다. HTTP는 202, MCP는 구조화된 정상 접수 결과로 매핑한다. 변경 작업의 idempotency_key는 뒤의 축약된 사용 예에도 공통으로 적용된다.

---

# 102. terminal_open

Input:

```json
{
  "device_id": "win-re-01",
  "shell": "powershell.exe",
  "cwd": "C:\\work",
  "cols": 120,
  "rows": 40
}
```

Output:

```json
{
  "handle_id": "term_...",
  "pid": 1024
}
```

---

# 103. desktop_screenshot

Input:

```json
{
  "device_id": "dev_01",
  "session_id": "session_1",
  "monitor_id": "monitor_primary",
  "preview": true
}
```

Output metadata 예시이며 preview image는 MCP image content로 별도 제공한다.

```json
{
  "artifact_id": "art_01",
  "observation_id": "obs_01",
  "session_id": "session_1",
  "monitor_id": "monitor_primary",
  "layout_revision": 4,
  "captured_at": "2026-10-01T00:00:00Z",
  "virtual_origin": {"x": 0, "y": 0},
  "width": 2560,
  "height": 1440,
  "format": "png",
  "monitor_scale": 1.25,
  "preview": {
    "width": 1600,
    "height": 900,
    "crop_origin": {"x": 0, "y": 0},
    "physical_pixels_per_preview_pixel": 1.6
  }
}
```

다중 monitor 캡처는 각 monitor의 origin/scale/rectangle 목록을 포함한다. desktop input은 이 observation과 별도 input lease를 요구한다.

---

# 104. Browser Example

아래는 공통 device_id/idempotency_key를 생략한 호출 흐름이다.

```text
browser_open -> browser_id, initial_page_id
browser_navigate(browser_id, page_id, URL)
browser_snapshot(browser_id, page_id) -> observation_id, navigation_revision, semantic tree
browser_click(browser_id, page_id, observation_id, locator)
browser_screenshot(browser_id, page_id) -> artifact + bounded image preview
```

page를 임의 선택하지 않으며 오래된 observation/element ref는 재사용하지 않는다. 자세한 locator와 navigation 계약은 E11을 따른다.

---

# 105. RE Example

```text
re_backends(device)
    ↓
["ida", "ghidra"]

re_open(
    device="win-re-01",
    backend="ida",
    target="C:\\samples\\sample.exe"
)
    ↓ analysis_id

re_query(
    analysis_id,
    query="functions"
)
```

Debugger:

```text
debugger_launch(
    backend="x64dbg",
    executable="C:\\samples\\sample.exe"
)
    ↓ debug_id
```

---

# 106. Security Approval UX

정책이 REQUIRE_APPROVAL이면 Gateway는 부작용 실행 전에 승인 객체를 만든다. `APPROVAL_REQUIRED`와 approval_id를 응답하며, 동기 MCP 호출을 승인될 때까지 붙잡지 않는다.

승인 대상에는 principal/device/operation/실행 profile/정규화 payload digest/policy_revision/대상 리소스 revision/만료시각을 결합한다. 기본 TTL은 5분이며 상태는 PENDING → APPROVED/DENIED/EXPIRED, APPROVED → CONSUMED 또는 EXPIRED다.

인증된 owner만 Console 또는 CLI에서 Approve once/Deny를 선택한다. 승인된 요청은 동일 operation_id로만 진행하며 claim을 원자적으로 소비한다. 실행 직전 정책·대상 revision을 다시 검사하고 변경되었으면 승인을 재요청한다. 승인 재전송이 중복 실행을 만들지 않도록 execution journal과 연동한다.

CLI/API 승인은 Phase 2의 shell E2E 전에 제공하고 Console UX는 Phase 6에서 추가한다. approval 기능을 끈 경우 REQUIRE_APPROVAL 요청을 DENY로 처리한다. 자동 허용이 필요하면 해당 operation의 정책을 명시적으로 ALLOW로 설정해야 한다.

---

# 107. Audit Redaction

다음 패턴은 기본 log redaction 후보:

```text
Authorization
Cookie
password
token
api_key
private_key
```

stdout/stderr 자체는 기본 audit DB에 전부 저장하지 않는다.

필요하면 별도 artifact/log retention 설정을 사용한다.

---

# 108. Plugin Process Model

외부 plugin은 다음 protocol을 권장한다.

```text
stdio JSON message
```

또는 local socket.

Plugin manifest:

```json
{
  "name": "ida",
  "version": "0.1.0",
  "capability": "static-analysis",
  "transport": "stdio",
  "command": ["python", "-m", "racp_ida_plugin"]
}
```

Agent plugin supervisor:

```text
start
health
restart
stop
backoff
```

---

# 109. Plugin Failure Isolation

Plugin crash 시:

```text
Agent stays alive
capability becomes DEGRADED
Gateway receives capability health event
```

설정에 따라 자동 restart.

---

# 110. External MCP Reuse

기존 MCP 서버는 가능한 한 재사용한다.

Bridge interface:

```python
class LocalMcpBridge:
    async def start(...)
    async def list_tools(...)
    async def call_tool(...)
    async def stop(...)
```

기존 MCP server가 unstable하면 subprocess restart 정책을 적용한다.

---

# 111. Browser Security

RACP 전용 브라우저 profile 기본.

다음은 explicit 설정 없이는 금지:

```text
사용자의 기본 Chrome profile 자동 사용
password manager 자동 접근
browser cookie dump
```

개인용으로 필요하면 policy와 config를 통해 opt-in 한다.

---

# 112. Desktop Safety

Desktop input은 실제 사용자 작업과 충돌할 수 있으므로 v1부터 session별 짧은 exclusive input lease를 사용한다. 기본 TTL은 15초이며 lease_id, observation_id, layout_revision, expected_window_id를 검사한다.

lease 만료·session 잠금·foreground 변경 시 입력을 중단하고 새 관측을 요구한다. 실제 사용자 입력 감지와 modifier 해제 정책, UAC/RDP 한계, 좌표 변환은 E10을 따른다. 경고만 표시하고 충돌한 입력을 계속 실행하지 않는다.

---

# 113. Concurrency

Device별 OperationScheduler를 둔다.

독립 operation:

```text
filesystem.read
process.list
```

는 병렬 가능.

exclusive resource:

```text
desktop input
same debugger handle mutation
same terminal write
```

에는 lock을 사용한다.

lock scope를 global device lock 하나로 과도하게 만들지 않는다.

---

# 114. State Machine Tests

아래 상태를 schema의 단일 정의로 사용하고 각 transition의 선행 조건·event·terminal 여부를 테스트한다.

```text
Device: ENROLLING -> CONNECTING -> ONLINE <-> DEGRADED
        ONLINE/DEGRADED/CONNECTING -> OFFLINE -> CONNECTING
        ENROLLING/CONNECTING/ONLINE/DEGRADED/OFFLINE -> REVOKED

Handle: CREATING -> ACTIVE -> CLOSING -> CLOSED
        CREATING -> FAILED
        ACTIVE -> EXPIRED | FAILED
        CLOSING -> FAILED

Job: QUEUED -> RUNNING -> COMPLETED | FAILED | TIMED_OUT
     QUEUED -> CANCELLED | TIMED_OUT
     RUNNING <-> WAITING
     RUNNING/WAITING -> CANCEL_REQUESTED -> CANCELLED | COMPLETED | FAILED | TIMED_OUT | UNKNOWN
     RUNNING/WAITING/CANCEL_REQUESTED -> RECONCILING
     RECONCILING -> RUNNING | WAITING | CANCEL_REQUESTED | COMPLETED | FAILED | CANCELLED | TIMED_OUT | UNKNOWN
     WAITING -> FAILED | TIMED_OUT
```

REVOKED는 terminal이며 재등록은 새 Device ID다. UNKNOWN은 자동 실행을 멈춘 terminal 상태이며 나중에 발견한 결과는 별도 resolution record로 첨부한다. 기존 audit와 terminal 상태를 조용히 덮어쓰지 않는다. network 연결 상태는 리소스 lifecycle과 독립적인 availability 필드로 표현한다.

응답 유실 뒤 재연결, 취소와 완료 경합, 종료 뒤 중복 result, Gateway 재시작, Agent crash, 승인 뒤 대상 변경을 반드시 포함한다. 부록 E15의 검증 ID와 테스트를 연결한다.

---

# 115. Documentation Deliverables

코드와 함께 다음 문서를 유지한다.

```text
README.md
docs/architecture/overview.md
docs/architecture/security.md
docs/protocol/agent-protocol.md
docs/protocol/mcp-tools.md
docs/development.md
docs/deployment-windows.md
docs/deployment-linux.md
docs/reversing-plugins.md
docs/troubleshooting.md
```

---

# 116. README의 Quick Start 목표

README만 보고 다음이 가능해야 한다.

```text
1. Gateway 실행
2. enrollment token 생성
3. Agent 연결
4. Codex에 MCP 등록
5. shell_exec 실행
```

---

# 117. Codex Integration

Gateway가 MCP Streamable HTTP endpoint를 제공한다.

사용자는 Codex의 MCP server configuration을 통해 Gateway를 연결한다.

Gateway는 MCP server instructions에서 다음을 간단히 알려준다.

```text
- device_list first when device is unknown
- prefer browser tools over desktop coordinate tools for web pages
- prefer structured RE tools over desktop GUI
- use Artifact references for binary/large outputs
```

---

# 118. ChatGPT Integration

ChatGPT와 연결할 때 Gateway는 remote MCP endpoint 또는 지원되는 private tunnel 경로로 노출한다.

구현 자체는 특정 ChatGPT UI에 종속되지 않는다.

MCP 인증/권한 요구사항이 변경되더라도 Gateway MCP Adapter만 교체할 수 있어야 한다.

---

# 119. MCP SDK Strategy

MCP 구현은 공식 SDK의 stable release line을 사용한다.

MCP protocol 세부 wire format을 직접 재구현하지 않는다.

Gateway core는 MCP SDK type에 의존하지 않도록 Adapter에서 변환한다.

---

# 120. 최종 기술 선택 요약

| 영역 | 기술 |
|---|---|
| Core language | Python |
| Python package manager | uv |
| MCP Server | Official MCP Python SDK |
| HTTP | FastAPI |
| Agent transport | WebSocket/WSS |
| Validation | Pydantic |
| DB | SQLite → PostgreSQL |
| ORM | SQLAlchemy + Alembic |
| CLI | Typer |
| Logging | structlog |
| Tracing | OpenTelemetry |
| Process | psutil / asyncio subprocess |
| PTY Windows | ConPTY/pywinpty |
| PTY Unix | pty |
| Browser | Playwright Python |
| Windows GUI | Win32/UI Automation/pywinauto |
| Frontend | TypeScript + React + Vite |
| Frontend data | TanStack Query |
| Packaging | PyInstaller onedir |
| Gateway deployment | Docker Compose |
| Tests | pytest + Vitest |
| CI | GitHub Actions |

---

# 121. AI 구현 작업 순서 — 반드시 이 순서를 기본으로 사용

기본 Phase 번호는 유지하되 선행 의존성은 다음과 같다.

1. Phase 0: scaffold, compatibility spike, domain/protocol/operation registry, 위협 경계 ADR.
2. Phase 1: Gateway/Agent bootstrap, owner 및 Device 인증, enrollment/revoke, WSS handshake, heartbeat, policy/audit, execution journal.
3. Phase 2: shell, cancellation/process cleanup, operation_get, 승인 CLI/API, 최소 Job 접수·Artifact spill, MCP shell E2E.
4. Phase 3: filesystem/process와 binary Artifact 기본 전송·quota·hash 검증.
5. Phase 4: PTY와 cursor/backpressure/reconnect 검증.
6. Phase 5: Job/Artifact의 resume·retention·reconcile 완성. 인증된 CLI와 함께 MVP 종료.
7. Phase 6: Console 및 API/realtime 상태·승인 UX.
8. Phase 7: Playwright, profile/page/observation 수명 관리.
9. Phase 8: Windows Broker와 desktop 계정·세션·DPI·lease 검증.
10. Phase 9: RE 계약과 실제 static/debugger 각각 한 개 연동.
11. Phase 10: 선택 Frida plugin. 선택하지 않으면 명시적으로 범위 제외.
12. Phase 11: packaging, clean install, upgrade/rollback, backup restore.
13. Phase 12: 종합 보안·fault injection·성능 검증 후 release.

패키징과 Windows Broker의 실행 가능성 spike는 Phase 0에서 먼저 한다. 본 구현은 해당 Phase에서 수행한다. 보안은 마지막에 처음 구현하는 기능이 아니다. 상세 milestone, 산출물, gate, 역할과 미확정 결정은 부록 E15–E17을 따른다.

---

# 122. AI가 Phase를 완료할 때 출력할 보고 형식

각 Phase 완료 시 AI는 다음을 보고해야 한다.

```markdown
## Phase X Result

### Implemented
- ...

### Changed files
- ...

### Tests
- command
- result

### Build
- command
- result

### Known limitations
- ...

### Next phase
- ...
```

테스트를 실행하지 못했다면 “통과했다”고 쓰지 않는다.

---

# 123. AI 개발 실행 프롬프트

아래 문장을 본 문서와 함께 AI 코딩 에이전트에 전달할 수 있다.

```text
첨부된 RACP Software Design & Development Specification을 authoritative implementation specification으로 사용하라.

목표는 단순 프로토타입이 아니라 실제 실행 가능하고 테스트 가능한 개인용 Remote AI Control Platform을 구현하는 것이다.

우선 Phase 0부터 시작하고, 각 Phase마다:
1. 구현
2. unit/contract/integration tests
3. lint/type check
4. 실제 실행 검증
5. 문서 업데이트
를 수행하라.

아키텍처 경계를 임의로 변경하지 말고 변경이 필요한 경우 ADR을 먼저 작성하라.

MCP는 AI와 Gateway의 외부 인터페이스에만 사용하고 Gateway-Agent 내부 transport에는 RACP Agent Protocol을 사용하라.

최우선 E2E milestone은:
Codex/MCP client -> Gateway -> WSS -> Agent -> shell.exec
경로가 실제로 실행되는 것이다.

그 후 specification의 Phase 순서대로 구현하라.

미구현 코드는 TODO placeholder로 성공한 척하지 말고 명확한 Unsupported/CapabilityUnavailable 상태를 반환하라.

테스트나 빌드 실패가 있으면 원인을 수정한 뒤 다음 Phase로 넘어가라.

최종적으로 Windows Agent installer, Gateway Docker image, Web Console build, CLI, 문서, release checksums를 생성할 수 있는 build pipeline을 완성하라.
```

---

# 124. 최종 제품의 기대 사용 예

최종적으로 사용자는 Codex/ChatGPT에서 다음과 같은 작업을 요청할 수 있어야 한다.

```text
win-re-01에서 C:\samples\foo.exe 파일을 확인해.
SHA256을 계산하고 PE header와 imports를 분석해.
IDA backend가 있으면 열어서 entry point 주변을 조사해.
필요하면 x64dbg를 실행해서 breakpoint를 걸어.
```

또는:

```text
browser-box에서 Chrome을 열어 internal test site에 접속해.
DOM을 우선 사용해서 조작하고, DOM으로 불가능한 UI만 desktop control로 처리해.
```

또는:

```text
linux-lab의 /srv/project에 들어가 git 상태를 확인하고
테스트를 실행한 뒤 실패 로그를 분석해.
```

AI는 사용자의 로컬 도구를 다루는 것과 유사한 흐름으로 원격 리소스를 사용할 수 있어야 한다.

---

# 125. 최종 아키텍처 원칙 요약

본 프로젝트에서 가장 중요한 설계 규칙은 다음과 같다.

```text
AI Client는 구현하지 않는다.
Gateway와 Agent를 구현한다.

MCP는 외부 boundary다.
Agent transport는 별도 protocol이다.

Python이 system/reversing core를 담당한다.
TypeScript는 UI를 담당한다.

모든 기능은 Capability Provider다.
모든 지속 리소스는 Handle이다.
모든 장시간 작업은 Job이다.
모든 큰 결과는 Artifact다.

Browser API가 Desktop 좌표보다 우선이다.
RE API가 GUI 자동화보다 우선이다.
GUI는 fallback이다.

Agent는 outbound-only가 기본이다.
Windows GUI는 Session Broker로 분리한다.

격리는 command filter가 아니라 VM/OS permission에서 수행한다.

모든 요청은 trace 가능해야 한다.
모든 Phase는 테스트와 빌드가 끝나야 완료다.
```

---

# 126. 구현 기준선 / 참고 표준

공식 자료 확인일: **2026-10-01 KST**. 아래 확인 사실과 프로젝트 설계 선택을 구분한다.

- [공식 MCP Python SDK 문서](https://py.sdk.modelcontextprotocol.io/)는 v2를 current stable release line으로 안내한다. 이 검토에서는 설치 가능한 정확한 patch와 RACP 빌드를 검증하지 않았으므로 Phase 0에서 lockfile과 실제 host 호환성을 확인한다.
- [MCP 2026-07-28 Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)는 POST endpoint, 요청별 SSE, GET stream/protocol session 제거를 설명한다. 해당 revision을 목표로 한 구현에서 구버전 session 모델을 강제하지 않는다.
- [MCP transport 개요](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)는 HTTP response stream 종료와 stdio 취소 notification을 구분한다. RACP는 SDK가 해석한 취소를 application command로 변환한다.
- [MCP HTTP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)은 OAuth resource server, discovery 및 resource 검증의 근거다. RACP는 public/remote MCP 노출에서 이를 구현하고 별도의 Device credential과 혼합하지 않는다.
- [Microsoft Interactive Services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)는 서비스와 사용자 GUI 분리의 근거다.
- [Microsoft SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)은 UIPI 제한을 설명한다. Broker가 높은 무결성 수준 창을 자동 제어할 수 있다고 가정하지 않는다.
- [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)는 소유한 process 집합의 관리에 참고한다. 종료 확인·breakaway 제한은 RACP의 별도 검증 대상이다.

MCP 지원 revision과 외부 client 지원 revision은 별도다. Codex/ChatGPT의 실제 연결·인증·tool call·취소·image 표시는 클라이언트별 호환 표에 PASS/FAIL/UNVERIFIED와 버전을 기록한다. 최신 SDK 사용만으로 모든 host 호환을 보장하지 않는다. 구버전 host가 필요하면 SDK compatibility adapter를 사용하며 protocol을 임의 재구현하지 않는다.

---

# 127. 마지막 구현 지침

이 문서의 목표는 “원격 셸 프로그램”이 아니다.

목표는 다음과 같은 **AI-native remote execution substrate**다.

```text
Remote Device
   │
   ├── compute
   ├── filesystem
   ├── process
   ├── terminal
   ├── browser
   ├── desktop
   ├── debugger
   └── analysis tools
          │
          ▼
      unified capability layer
          │
          ▼
       RACP Gateway
          │
          ▼
      ChatGPT / Codex
```

구현자는 각 feature를 개별적인 “명령 함수”로 덧붙이는 방식이 아니라, Device / Capability / Operation / Handle / Job / Artifact라는 공통 모델 위에 구현해야 한다.

이 원칙을 지키면 이후 다음 기능을 같은 구조로 추가할 수 있다.

```text
VM snapshot
Docker control
ADB/Android
iOS device
SSH bridge
RDP/VNC bridge
WinDbg
LLDB
kernel debugger
network capture
hardware debugger
serial port
JTAG bridge
cloud VM
remote build host
```

따라서 v1 구현에서 가장 중요한 품질은 기능 개수보다 **명확한 추상화, protocol stability, observability, testability, failure isolation**이다.

---

# Appendix A — 초기 개발자가 먼저 생성해야 할 파일

AI는 Phase 0에서 최소 다음 파일을 생성한다.

```text
pyproject.toml
.python-version
.editorconfig
.gitignore
README.md

apps/gateway/pyproject.toml
apps/gateway/src/racp_gateway/__init__.py
apps/gateway/src/racp_gateway/main.py

apps/agent/pyproject.toml
apps/agent/src/racp_agent/__init__.py
apps/agent/src/racp_agent/main.py

apps/cli/pyproject.toml

packages/domain/pyproject.toml
packages/domain/src/racp_domain/__init__.py
packages/domain/src/racp_domain/device.py
packages/domain/src/racp_domain/operation.py
packages/domain/src/racp_domain/handle.py
packages/domain/src/racp_domain/job.py
packages/domain/src/racp_domain/artifact.py

packages/protocol/pyproject.toml
packages/protocol/src/racp_protocol/__init__.py
packages/protocol/src/racp_protocol/messages.py

tests/contract/test_agent_protocol.py
tests/integration/test_gateway_boot.py

docs/adr/ADR-0001-python-first-agent-and-gateway.md
docs/adr/ADR-0002-typescript-react-console.md
docs/adr/ADR-0003-mcp-only-external-boundary.md
```

---

# Appendix B — 첫 번째 E2E 테스트

가장 먼저 완성해야 할 실제 기능은 아래 하나다.

```text
MCP Client
   ↓
shell_exec
   ↓
RACP Gateway
   ↓
WSS
   ↓
RACP Agent
   ↓
subprocess
   ↓
stdout / stderr / exit code
```

테스트 성공 예:

```json
{
  "device_id": "test-agent",
  "mode": "argv",
  "argv": ["python", "-c", "print('RACP_OK')"],
  "idempotency_key": "e2e-shell-001"
}
```

expected:

```json
{
  "exit_code": 0,
  "stdout": "RACP_OK\n",
  "stderr": ""
}
```

이 경로가 완성되기 전에는 Browser, GUI, RE plugin 개발로 넘어가지 않는다.

---

# Appendix C — 첫 release의 권장 디렉터리 결과물

```text
release/
├── racp-gateway-1.0.0.tar.gz
├── racp-gateway-1.0.0-py3-none-any.whl
├── racp-agent-windows-x64-1.0.0.zip
├── racp-agent-linux-x64-1.0.0.tar.gz
├── racp-windows-setup-1.0.0.exe
├── racp-console-1.0.0.tar.gz
├── docker-compose.yml
├── checksums.sha256
├── sbom.spdx.json
└── RELEASE_NOTES.md
```

---

# Appendix D — 비기능 요구사항 요약

## Maintainability
- domain/infrastructure separation
- protocol schemas centralized
- typed interfaces
- ADR
- dependency injection

## Extensibility
- capability plugins
- external MCP bridge
- backend abstractions
- transport-independent core

## Reliability
- timeout
- cancellation
- reconnect
- idempotency
- health
- bounded buffers

## Debuggability
- trace/request IDs
- structured logs
- doctor command
- contract tests
- deterministic fake components

## Security
- outbound-only Agent
- secure credentials
- policy engine
- privilege separation
- audit
- VM isolation for untrusted targets

## Usability
- one Gateway
- multiple Devices
- local-like tools
- persistent handles
- browser semantic automation
- GUI fallback
- Web Console

---

# Appendix E — 구현 계약 및 인수 기준

이 부록은 구현자가 임의로 해석하면 결과가 달라지는 부분을 규정한다. 원문의 예시는 설명용이며, 이 부록의 구체적인 필드·상태·제한을 실제 schema와 contract test에 반영한다. 새 범위나 기본값을 변경할 때는 관련 본문도 동시에 갱신한다.

## E1 구성요소별 책임과 데이터 소유권

| 구성요소 | 원본으로 관리하는 정보 | 맡지 않는 책임 |
|---|---|---|
| Gateway | 인증 principal, policy, Device 등록, 요청 접수, Handle 소유권, Job 조회, Artifact metadata | 원격 실행 사실 추측, 사용자 원격 명령의 로컬 실행 |
| Agent runtime | boot/connection epoch, durable execution journal, 실제 provider 실행과 자원 상태 | Gateway owner 자격증명 보관, 다른 Device 제어 |
| Session Broker | 특정 Windows session의 GUI 관측·입력, input lease | 네트워크 listener, owner 인증, 임의 권한 상승 |
| Plugin supervisor | manifest 검증, subprocess 수명, timeout/health | 미신뢰 코드를 OS 수준으로 완전히 격리한다고 주장 |
| Console/CLI/MCP adapter | typed input/output 변환, 인증된 application API 호출 | 정책 우회, DB 또는 Agent provider 직접 접근 |

핵심 domain 7개는 유지한다. Principal, Approval, ExecutionRecord, Transfer는 인증·실행·저장 지원 모델이다. DB table 수가 핵심 domain 수와 같아야 한다는 뜻은 아니다.

v1은 Gateway 1 process/1 worker, local SSD SQLite WAL, local filesystem Artifact store다. NFS/SMB 위 SQLite와 여러 Gateway worker는 지원 범위에서 제외한다. busy timeout 5초, transaction은 짧게 유지하며 DB transaction 안에서 원격 네트워크 응답을 기다리지 않는다.

추가 persistence에는 `principals`, `execution_records`, `idempotency_tombstones`, `approvals`, `artifact_transfers`, `schema_versions`가 필요하다. Handle/Job/Artifact에는 owner principal, device_id, created_at, revision을 저장한다. Agent journal은 Gateway DB와 별도이며 재시작 후 남아야 한다.

## E2 공통 API 계약과 기능 누락 보완

각 operation registry 항목은 name, capability/version, input/output schema, sync/job 지원, side_effect, retry_class, cancel 방식, timeout 상한, resource lock key, 권한 범위, 최소 OS, 검증 ID를 MUST 가진다. JSON Schema와 OpenAPI/TypeScript client는 transport schema에서 생성하고 CI에서 drift를 검사한다.

Canonical 내부 이름은 `filesystem.read`, 외부 MCP 이름은 `fs_read`처럼 명시적으로 매핑한다. `fs.read`를 내부 이름으로 혼용하지 않는다. 입력의 미등록 필드는 거부하고, versioned output의 추가 선택 필드는 구버전 client가 무시할 수 있게 한다. 64-bit 주소·정수는 JavaScript 정밀도 손실을 피하도록 주소는 `0x...`, 큰 offset/sequence는 10진 문자열로 전달한다. UTC timestamp는 RFC 3339, duration/size 단위는 이름에 명시한다.

| 원문에서 API가 부족한 필수 기능 | v1 노출 계약 |
|---|---|
| 요청 상태 확인·취소 | `operation_get`, `operation_cancel`; operation_id 또는 자신의 idempotency_key로 조회 |
| append | `fs_write(mode=create|replace|append)`; append도 mutation key 필수 |
| process kill/wait/tree | `process_terminate(force=false|true)`, `process_wait`, `process_tree` |
| browser page 관리 | `browser_pages`, `browser_new_page`, `browser_close_page`; page_id 명시 |
| mouse move/double click | `desktop_move`, `desktop_click(click_count=1|2)` |
| session/monitor/foreground | `desktop_sessions`, `desktop_monitors`, `desktop_foreground` |
| Artifact upload/download | transfer HTTP API와 CLI; MCP에는 metadata/read 또는 bounded preview |
| 승인과 관리 | CLI/Console API; AI tool에는 승인할 권한을 자동 부여하지 않음 |

최소 HTTP surface는 다음과 같다. `{id}`의 형태·소유권 검증을 생략하지 않는다.

```text
POST /mcp
POST /api/v1/enrollment-tokens
POST /agent/v1/enroll
WS   /agent/v1/connect
POST /api/v1/operations
GET  /api/v1/operations/{id}
POST /api/v1/operations/{id}/cancel
GET  /api/v1/devices[/{id}]
POST /api/v1/devices/{id}/revoke
POST /api/v1/devices/{id}/credentials/rotate
GET  /api/v1/handles[/{id}]
GET  /api/v1/jobs[/{id}]
POST /api/v1/jobs/{id}/cancel
GET  /api/v1/approvals[/{id}]
POST /api/v1/approvals/{id}/approve
POST /api/v1/approvals/{id}/deny
GET  /api/v1/artifacts[/{id}]
GET  /api/v1/artifacts/{id}/content
POST /api/v1/artifact-transfers
GET  /api/v1/artifact-transfers/{id}
PUT  /api/v1/artifact-transfers/{id}/content
POST /api/v1/artifact-transfers/{id}/complete
GET  /api/v1/audit
GET  /api/v1/events
```

`[/{id}]`는 목록과 단건 endpoint를 모두 구현한다는 문서 표기이며 실제 URL이 아니다. 단일 Device 내부 동작은 operation API로 통일한다. 인증 없는 healthz는 단순 liveness만 공개하고 readyz/metrics/doctor 상세는 관리 권한으로 제한한다.

HTTP 오류는 invalid 400, unauthenticated 401, forbidden 403, missing 404, conflict/approval-required 409, expired 410, precondition 412, quota/rate 429, offline 503, timeout 504로 매핑한다. 미지원 operation은 422, protocol 불일치는 handshake 거절로 처리한다. MCP는 SDK 표준 protocol 오류와 tool 실행 오류를 구분하고 tool 오류에 `isError` 및 동일 structured error를 제공한다. Job 접수는 오류가 아니다.

목록은 기본 100, 최대 500개 및 opaque cursor를 사용한다. cursor에는 filter/sort/revision과 만료를 결합한다. 통상 목록은 best-effort consistency를 명시하고 변경 중 중복·누락 가능성을 숨기지 않는다. 파일 tree/search 결과는 최대 depth/count/time을 입력과 결과에 표시한다.

## E3 연결과 메시지 검증

TLS·Device 인증 → hello → protocol 선택 → welcome → reconcile → ONLINE 순서다. handshake 상한은 10초이며 중간 실패 상태에서 operation을 보내지 않는다.

hello는 device_id, agent_boot_id, agent_version, supported_protocols, platform, architecture, capabilities, last_event_cursor를 가진다. welcome은 선택 protocol, connection_epoch, heartbeat_interval_ms, max_message_bytes, 실행 lease TTL을 반환한다. protocol 교집합이 없으면 PROTOCOL_MISMATCH로 종료하며 자동 downgrade 재시도를 반복하지 않는다.

type별 schema는 discriminated union이다. request는 operation/payload/context/budget가 필수이고 result/error는 operation_id/request_id가 필수다. heartbeat는 boot_id/epoch와 resource health만 가진다. cancel은 새 request_id와 target_operation_id를 가진다. stream은 stream_id/handle_id/byte_offset을 가진다. 서버는 허용 state에서만 해당 message type을 수신한다.

JSON은 UTF-8, 최대 nesting 32, message 1 MiB, 개별 문자열 256 KiB다. 프레임 fragmentation을 합친 전체 크기도 제한하고 compression은 초기에는 비활성화한다. malformed payload는 실행 전에 거부한다. request_id/trace_id는 길이를 제한하고 로그 주입 문자를 제거한다. trace_id는 32자리 hex, 별도 span_id는 16자리 hex를 사용한다.

Capability version은 semver이며 major 호환 범위와 operation 목록을 보낸다. GUI session lock·browser crash·plugin 종료 시 capability revision을 증가시키고 변경 event를 보낸다. Gateway는 오래된 캐시만 믿지 않으며 Agent는 dispatch 직전 실제 availability를 다시 확인한다.

## E4 실행 기록과 장애 복구

실행 절차는 인증·정책·리소스 검증 → Gateway journal 접수 commit → dispatch 의도 commit → Agent journal 접수 commit → ack → provider 실행 → Agent 결과 commit → Gateway 결과 commit이다. 실제 전송 전에 dispatch 의도를 저장해 전송 직후 crash를 전송 전으로 오판하지 않는다. 재연결은 새 실행 허가가 아니라 기존 실행 사실의 확인이다.

| 장애 지점 | 복구 동작 | 자동 mutation 재실행 |
|---|---|---|
| Agent 전송 전 Gateway crash | 접수 record 확인, timeout 유효 시 dispatch | 아직 dispatch되지 않은 기록만 가능 |
| 전송 후 ACK 유실 | 동일 operation/key 상태 조회, 재전송 시 Agent journal dedupe | 새 ID로 금지 |
| 부작용 후 result 기록 전 Agent crash | EXECUTION_UNKNOWN, manual reconciliation | 금지 |
| 결과 기록 후 전송 유실 | 저장된 결과 재전송 | 금지 |
| Gateway만 재시작 | boot 동일한 Agent journal과 handle inventory 대조 | 금지 |
| Agent runtime 재시작 | 이전 volatile handle 만료, Job은 journal로 판정 | 부작용 불명확 시 금지 |
| 이전 WS에서 지연 result 수신 | epoch 검사 후 현재 연결을 통한 reconcile로만 반영 | 금지 |

idempotency full result는 완료 후 24시간 이상 보관한다. 삭제 후에도 최소 tombstone `(scope hash, key hash, payload digest, operation_id, outcome_available)`은 해당 Device 수명 동안 유지해 만료된 키를 재실행하지 않는다. 결과가 삭제된 키는 410과 operation_id를 반환한다. tombstone 상한은 Device당 100만 건이며 도달 시 새 mutation 접수를 거부하고 export/새 Device로의 관리 전환을 안내한다. 공간 회수를 위해 조용히 tombstone을 지우지 않는다.

Agent와 Gateway journal 중 하나라도 저장에 실패하면 새 mutation을 시작하지 않는다. 복제 VM은 원본 Device credential과 journal을 복사해 동시에 연결하지 않는다. VM snapshot restore 또는 clone은 credential 제거 후 재등록을 원칙으로 한다.

OperationRecord는 request 접수, 승인 대기, 실행, 결과의 전체 수명을 추적한다. Job은 긴 실행을 사용자에게 조회 가능하게 하는 별도 domain이며 모든 Operation이 Job인 것은 아니다. Job 상태는 §114를 사용하고 승인 대기는 Job WAITING과 혼동하지 않는다. WAITING은 backend의 외부 event 대기이며 waiting_reason을 필수로 기록한다.

## E5 자원 수명과 종료 확인

Handle은 id/type/device/owner/agent_boot_id/provider_instance_id/resource_revision/created_at/last_access_at/expires_at/state/availability를 가진다. ID는 추측하기 어려워야 하지만 ID 자체를 권한 증명으로 사용하지 않는다.

기본 idle TTL은 terminal 8시간, browser 1시간, debugger/analysis 4시간이다. 명시적인 무기한 세션은 profile opt-in이며 수량 제한과 owner close는 유지한다. poll만으로 idle TTL이 늘어나지 않으며 keepalive operation을 명시적으로 사용한다. close는 같은 Handle에 반복 호출해도 추가 side effect 없이 기존 terminal 결과를 반환한다.

연결 단절이 짧으면 Handle을 유지하되 새 입력은 불가능하다. 실행 lease 60초가 만료되면 managed process/PTY/browser를 종료하고 debugger는 소유권에 맞게 detach 또는 종료한다. 장기 offline 실행은 v1 기본 지원에서 제외한다. 살아 있는 Handle의 TTL이 길어도 lease 만료 정책을 우선한다.

Windows 소유 process는 Job Object로 묶고 breakaway를 허용하지 않는다. Linux는 process group과 가능한 경우 전용 systemd/cgroup scope를 사용한다. provider가 containment를 보장하지 못하면 capability에 한계를 표시하고 child 생존 여부를 확인한다. 이미 존재하는 외부 process에 attach한 debugger의 close는 기본 detach이며 원래 process를 종료하지 않는다. PID 재사용을 막기 위해 `(pid, create_time, boot_id)`로 대상 identity를 비교한다.

원자적 파일 rename 등 commit이 끝난 작업의 취소는 완료 결과를 유지한다. RE 취소를 지원하지 않는 backend는 CANCEL_NOT_SUPPORTED와 계속 실행 중인 job_id를 반환한다. 취소 후 cleanup_status는 complete/partial/unknown이며 잔여 리소스 목록을 doctor에서 볼 수 있어야 한다.

## E6 PTY와 event stream

PTY 출력은 stdout/stderr가 합쳐진 terminal byte stream이다. shell.exec의 분리 출력과 동일한 API로 가장하지 않는다. `terminal_read` 입력은 handle_id, cursor, max_bytes, wait_ms이며 max_bytes는 64 KiB, wait_ms는 최대 20초다.

cursor는 해당 Handle의 단조 증가 byte offset이다. 응답은 data, next_cursor, earliest_cursor, lost_bytes, eof, process_exit를 포함한다. 소비자마다 cursor를 유지하고 읽기가 다른 소비자의 데이터를 제거하지 않는다. 오래된 cursor는 CURSOR_EXPIRED와 earliest_cursor/lost_bytes를 반환하며 사용자가 새 위치를 선택해야 한다.

UTF-8 문자가 chunk 경계에서 깨지지 않도록 decoder 상태를 유지한다. byte offset은 원본 bytes 기준이다. invalid byte가 있으면 replacement 수를 표시하고 필요한 경우 제한된 raw chunk 또는 Artifact를 제공한다. arbitrary PTY byte write는 64 KiB 이하의 명시적 raw encoding으로만 허용한다.

write는 원자적인 OS 입력 transaction이 아니다. 부분 write가 있으면 accepted_bytes를 기록하며 결과 불명확 시 같은 텍스트를 자동으로 다시 입력하지 않는다. resize는 cols 10–500, rows 5–300 범위를 검증한다.

WS stream은 stream_ack의 소비 offset과 최대 in-flight 256 KiB를 사용한다. consumer가 느리면 ring/drop-oldest를 적용하고 누락 event를 전달한다. Console SSE `/events`는 event_id와 Last-Event-ID를 지원하며 기본 10분/1만 건 중 먼저 도달한 제한까지 replay한다. 범위 밖 cursor는 full refresh를 요청한다. SSE는 지연된 cache일 수 있으므로 상태 최종 확인은 GET API로 한다.

## E7 인증과 로컬 실행 권한

세 가지 identity를 분리한다. owner/AI client는 Gateway API, Device credential은 Agent 접속, service/broker OS identity는 실제 실행 권한이다. `actor=mcp:codex` 같은 이름은 표시용이며 인증 근거가 아니다.

로컬 bootstrap은 OS 사용자만 읽을 수 있는 owner setup secret을 한 번 발급한다. CLI는 OS secure store의 owner token을 사용한다. Console은 secret을 localStorage에 저장하지 않고 HttpOnly/SameSite cookie session으로 교환한다. 세션 유휴 30분·절대 12시간, 상태 변경 요청에 CSRF 검증, CORS/Origin/Host allowlist를 적용한다. cookie Secure는 HTTPS에서 필수이며 loopback 개발 HTTP만 제한적으로 예외다.

remote/public MCP는 검증된 OAuth provider를 사용하며 issuer, audience/resource, expiry, scope를 검증한다. provider metadata URL은 관리자가 고정하고 임의 요청의 URL을 따라가지 않는다. bearer token을 Agent로 전달하지 않는다. LAN/tunnel 사용이 인증 면제 사유가 되지 않는다. OAuth 적용 범위는 공식 §126을 따르고 구체 provider 선택은 Phase 0 결정사항이다.

Device opaque credential은 최대 90일, 기본 30일마다 rotation한다. 새 credential 저장 확인 후 이전 것을 폐기하며 장애 복구 overlap은 최대 10분이다. revoke는 새 접속을 즉시 거부하고 기존 WS를 폐쇄하며 연결 중이면 cancel을 우선 전송한다. Agent는 heartbeat 응답마다 갱신하는 로컬 monotonic execution lease 60초가 만료되면 새 실행을 거부하고 §E5 cleanup을 수행한다. 네트워크 단절 시 즉시 원격 폐기를 보장한다고 표현하지 않는다.

Windows Agent Service는 설치 시 지정한 비관리자 전용 계정/서비스 identity로 동작하며 LocalSystem을 기본으로 사용하지 않는다. credential store ACL은 Agent identity와 관리자로 제한한다. 일반 shell/fs는 그 실행 identity의 권한을 사용한다. 개인 사용자 profile·네트워크 drive·credential을 자동 상속하지 않는다. 사용자 계정으로 별도 Agent instance를 실행하려면 새 Device ID로 등록해 identity를 구분한다.

GUI Broker는 지정한 로그온 사용자 권한으로 동작하므로 desktop scope는 그 사용자가 GUI로 접근할 수 있는 범위에 대한 권한임을 Console에 표시한다. Agent credential과 owner token을 Broker나 실행 child의 env로 전달하지 않는다. 동일 OS identity에서 임의 shell을 허용하면 해당 identity의 credential도 보호할 수 없는 한계가 있으므로 이 계정과 Device는 신뢰 단위다. 불신 작업은 별도 VM/계정·단기 credential에 배치한다.

audit는 접수/판정/dispatch/완료/취소/revoke/승인 이벤트를 남긴다. payload 전체 대신 정규화 digest와 안전한 요약을 저장한다. command 출력·키 입력·cookie·password는 기본 저장하지 않는다. 필수 audit 쓰기 실패 시 새 mutation을 거부하고 기존 실행 cleanup을 계속한다. metric label에 request_id/임의 path를 넣지 않아 cardinality를 제한한다.

## E8 파일과 프로세스의 세부 동작

상대 path는 명시한 workspace 또는 execution profile root 기준이다. `~`, env expansion은 기본 비활성화한다. Windows drive-relative `C:foo`, device namespace, ADS는 기본 거부하며 UNC/network share는 profile opt-in이다. 경로 접근 정책은 문자열 prefix 비교만으로 구현하지 않는다.

read/stat/list는 링크 자체 정보와 최종 target을 구분한다. write/delete/search에서 symlink·junction·reparse point를 따라가는 것은 기본 비활성화한다. 허용 root 검사는 실제 열린 객체/부모를 기준으로 재검사하고 TOCTOU 방어가 불가능한 경우 제한된 모드에서 쓰기를 거부한다. shell이 같은 OS 권한으로 경로를 접근할 수 있다는 점에서 filesystem API 제한은 OS sandbox를 대신하지 못한다.

`fs_write`의 mode=create는 기존 파일이 있으면 CONFLICT, replace는 overwrite=true를 명시한다. 중요 수정은 expected_sha256/expected_revision으로 변경 충돌을 확인한다. 동시 RACP writer를 serialize하고 rename 직전에 precondition을 재검사한다. 외부 writer와 완전한 compare-and-swap을 보장하지 못하는 플랫폼은 그 한계를 출력한다. 같은 filesystem의 임시 파일에 쓰고 flush/fsync 후 replace하며 원래 ACL/권한을 보존한다.

append는 offset precondition과 idempotency를 적용하며 atomic replace로 가장하지 않는다. cross-volume move는 기본 실패하며 사용자가 copy-and-delete를 선택한 경우 진행/부분 완료 결과를 Job으로 기록한다. copy 기본은 no-overwrite다. recursive delete는 recursive=true, 대상 식별정보, 정책 판정을 요구하며 root/system path 보호를 적용한다. 중간 실패의 completed/failed 경로를 제한된 보고서 Artifact로 반환한다.

text read/write의 기본 encoding은 UTF-8, BOM/newline은 보존한다. decode 실패 시 임의 변환으로 저장하지 않고 binary Artifact 경로를 안내한다. fs_search는 기본 literal, max_depth 20, max_results 1000, timeout 30초다. regex opt-in은 timeout 가능한 engine 또는 격리 worker에서 처리하고 ReDoS로 event loop를 막지 않는다.

process terminate는 기본 graceful, force=true만 kill로 처리한다. 본 Agent/Gateway/Broker 및 OS 보호 process는 일반 terminate 대상에서 제외한다. process_wait는 PID identity를 확인하며 timeout은 기다림 종료이지 process kill이 아니다. spawn은 argv, cwd, env 규칙을 shell.exec와 공유하고 반환된 process Handle에 owner/cleanup policy를 연결한다.

## E9 Artifact 전송과 AI의 이미지 접근

Artifact는 `UPLOADING → VERIFYING → READY → EXPIRED → DELETED` 또는 FAILED 상태다. READY 이전에는 읽을 수 없다. content hash는 물리 blob dedupe용이며 hash를 안다는 이유로 접근을 허용하지 않는다. 논리 Artifact마다 owner/device/operation/retention을 따로 저장한다.

전송은 create transfer → scoped transfer credential 발급 → content upload → complete → size/hash 검증 → atomic publish 순서다. Agent가 Gateway의 HTTPS endpoint로 outbound 업로드·다운로드하므로 Agent inbound port는 필요 없다. scope는 transfer_id, device_id, read/write, 최대 bytes, TTL 10분에 결합한다. 만료된 전송 credential은 owner 또는 유효한 Device identity로 transfer 상태를 조회하고 재인가해 갱신하며, 원래 bytes/권한 범위를 넓히지 않는다.

chunk는 기본 4 MiB이며 Content-Range/offset과 chunk hash를 검사한다. 이미 저장한 동일 offset·hash 재전송은 성공하고 다른 내용은 CONFLICT다. 완료 API는 total size와 SHA-256을 검증한다. download는 HTTP Range와 ETag를 지원한다. 전송 상태 조회는 committed byte offset을 반환하며 disconnect 후 이어받는다. 예약 quota에 incomplete bytes도 포함하고 미완료 transfer는 1시간 뒤 정리한다.

retention 기본은 7일이다. 진행 중 Job·유효 Handle이 참조한 blob은 pin하고 마지막 참조 및 transfer lease가 끝난 뒤 GC한다. GC와 다운로드가 경쟁하면 진행 중 download를 완료할 때까지 삭제를 미룬다. hash 불일치·disk full에서 READY metadata를 남기지 않는다. archive는 자동 해제하지 않으며 요청된 해제에서 traversal/symlink/bomb 제한을 적용한다.

Artifact ID만 반환하면 AI가 화면 내용을 볼 수 없는 host가 있다. 원본은 Artifact에 저장하고 screenshot tool 또는 `artifact_read(preview=true)`는 별도의 축소 이미지도 MCP image content로 전달할 수 있어야 한다. 이 adapter 경계에서만 bounded image encoding을 허용하며 내부 WSS와 일반 JSON 응답에 원본 base64를 반복 삽입하지 않는다.

preview 기본 상한은 긴 변 1600px·2 MiB다. 반환 metadata는 원본 크기, preview 크기, crop origin, scaling transform, observation_id를 포함한다. crop/tile을 추가 요청할 수 있다. 이미지 내용은 secret이 포함될 수 있으므로 원본과 같은 접근 권한을 적용한다. 임의 public URL을 만들지 않으며 URL 방식이 필요한 host는 짧은 TTL의 object-scoped URL을 사용한다.

## E10 Windows Broker와 화면 좌표

Broker는 session_id와 user SID에 결합한다. Named Pipe는 local-only이며 해당 service SID와 지정 사용자 SID만 허용한다. peer PID/session/SID와 nonce challenge를 검사하고 Agent가 보낸 임의 token으로 impersonation하지 않는다. 로그온 시작, 로그오프 종료, crash backoff, version mismatch를 처리한다. IPC에 GUI operation allowlist를 두고 임의 shell 실행을 넣지 않는다.

headless, locked, disconnected RDP, UAC secure desktop, elevated window는 구분해 보고한다. 지원하지 않는 상태에서 SESSION_UNAVAILABLE/SESSION_LOCKED/INTEGRITY_LEVEL_MISMATCH를 반환하며 자동 unlock·UAC 우회를 시도하지 않는다. UI Automation/SendInput이 성공 응답했다고 실제 intended window가 입력을 받았다고 단정하지 않는다.

입력은 session별 exclusive lease가 필수다. `desktop_lease_acquire/renew/release`를 제공하고 owner/device/session에 결합한다. lease TTL 기본 15초, 갱신 가능, lease 만료·foreground 변경·사용자 입력 감지 시 중단하고 새 관측을 요구한다. OS/driver상 사용자 입력 감지가 완전하지 않을 수 있음을 capability에 표시한다. 모든 종료·예외 경로에서 눌린 modifier/mouse button을 해제한다.

좌표는 virtual desktop physical pixel이며 음수 origin과 여러 DPI를 허용한다. screenshot에는 session_id, monitor_id, virtual_origin, dimensions, 각 monitor scale, layout_revision, captured_at, observation_id를 포함한다. 입력에는 lease_id와 observation_id, layout_revision, expected_window_id를 요구한다. 관측 기본 TTL은 5초이며 window/layout이 달라지면 STALE_OBSERVATION이다.

preview 좌표와 원본 물리 좌표의 변환은 metadata에 따라 수행한다. 다중 모니터를 단일 dpi_scale 하나로 표현하지 않는다. monitor index는 영구 ID가 아니므로 재연결 시 monitor_id/layout_revision을 새로 조회한다. text typing은 Unicode 입력을 기본으로 하고 clipboard 변경이 필요한 fallback은 원래 clipboard 보존/복원과 명시적 설정을 요구한다.

## E11 브라우저 세션과 관측

browser Handle은 격리된 profile/context, page Handle은 특정 tab을 가리킨다. 모든 조작은 browser_id와 page_id를 받으며 last active tab을 추측하지 않는다. 신규 context는 RACP 전용 profile, browser sandbox 유지, CDP listen은 loopback으로 제한한다. 사용자 기존 profile/CDP attach는 opt-in이고 기존 브라우저의 소유권을 구분한다.

snapshot은 URL/title/frames/semantic tree/interactive elements/viewport와 observation_id/navigation_revision을 반환한다. locator는 role/name/test-id 등 구조화된 selector를 기본으로 하며 ambiguous match는 오류다. element ref는 해당 observation 범위에서만 유효하고 navigation/context 종료 후 재사용하면 STALE_OBSERVATION이다.

click/type는 expected page와 navigation revision을 검사한다. auto-wait는 pinned Playwright 동작에 맞추되 operation deadline을 넘지 않는다. dialog는 기본 dismiss하고 event를 남긴다. download는 임시 sandbox에 받고 Artifact로 게시한다. upload는 권한 있는 Artifact를 Agent 임시 경로에 materialize한 뒤 수행하며 임의 로컬 경로 업로드는 별도 filesystem scope가 필요하다.

`browser_evaluate`는 명시적 고권한 operation이다. read-only라고 분류하지 않으며 timeout·출력 크기를 제한하고 browser host function을 노출하지 않는다. page가 무한 루프에 빠지면 worker/page/context 종료까지 수행할 수 있어야 한다. 동일 page mutation은 serialize한다.

기본 허용 URL scheme은 http/https, file은 opt-in이다. 브라우저가 접근할 수 있는 내부망도 실행 계정의 권한에 포함되므로 profile별 network 정책을 정의한다. Gateway가 결과 URL을 임의 fetch하여 SSRF 경로를 만들지 않는다. 웹 페이지/파일/플러그인 출력은 비신뢰 데이터이며 그 안의 지시를 정책 변경이나 승인으로 해석하지 않는다.

## E12 RE와 외부 MCP plugin

Plugin manifest는 name/version/protocol_version/capabilities/operation schema/executable argv/health timeout/required permissions/backend version을 가진다. v1 설치는 로컬 관리자가 지정한 allowlisted plugin만 허용한다. Agent는 plugin을 임의 인터넷 주소에서 다운로드·실행하지 않는다.

stdio framing은 한 줄당 UTF-8 JSON, 최대 1 MiB이며 stdout은 protocol 전용, 로그는 stderr다. 요청마다 ID, deadline, cancel, result/error를 사용하고 무응답은 종료/restart한다. restart는 최대 분당 3회 후 DEGRADED로 유지한다. 재시작한 plugin의 이전 handle은 만료한다. subprocess 분리는 crash 격리이며 동일 사용자 권한의 악성 plugin을 sandbox하지는 못한다.

외부 MCP bridge는 시작 시 tool schema를 읽고 관리자가 허용한 mapping만 노출한다. tool 목록/schema hash가 바뀌면 해당 mapping을 비활성화하고 재검증한다. passthrough는 별도 permission이며 opaque tool은 side-effect 가능으로 취급한다. bridge가 arbitrary token을 외부 endpoint로 전달하지 않도록 env/credential allowlist를 적용한다.

정적 분석 Handle에는 target sha256, backend/version, analysis database path, architecture, image base, analysis state를 기록한다. 함수/string/xref 목록은 pagination을 지원한다. address는 hex string, module+RVA와 absolute address를 구분하며 rename/comment는 mutation이다. decompile 미지원은 OPERATION_NOT_SUPPORTED다.

Debugger state는 STARTING/STOPPED/RUNNING/DETACHED/EXITED/FAILED이며 registers/read_memory는 기본 STOPPED에서만 허용한다. continue/step은 실행 접수와 stop event를 분리하고 장기 대기는 Job으로 노출한다. launch한 target과 attach한 target의 소유권·close semantics를 기록한다. memory read는 기본 최대 1 MiB, dump는 Artifact/Job으로 처리한다.

실제 검증 fixture는 소스가 알려진 작은 프로그램과 symbols를 사용한다. static backend에서 함수/문자열/주소를 조회하고 debugger에서 launch→breakpoint→continue→stop→registers→detach/exit를 검증한다. 분석 도구·상용 라이선스와 plugin 재배포 조건은 build manifest에 명시한다.

## E13 Console과 CLI의 사용자 흐름

최초 흐름은 owner 초기화 → Device enrollment → 실행 identity/capability 확인 → profile 선택 → shell E2E → 작업 조회다. 모든 destructive action과 승인 화면에는 Device 이름뿐 아니라 stable ID·OS 실행 계정·session·대상 경로·operation 요약을 보여준다.

Console은 ONLINE/OFFLINE/DEGRADED와 마지막 관측 시각을 표시한다. SSE disconnect 상태에서 마지막 ONLINE 표시를 최신 사실처럼 유지하지 않는다. Job 취소 버튼은 CANCEL_REQUESTED와 종료 완료를 구분한다. UNKNOWN은 다시 실행 버튼을 기본 제안하지 않고 상태 조사 링크를 제공한다.

CLI 모든 조회는 `--json`을 제공한다. exit code는 0 성공, 1 operation 실패, 2 입력 오류, 3 인증/권한 거부, 4 offline/통신 불확정, 5 승인 필요다. shell 원래 exit_code는 JSON field로 보존하고 CLI exit code와 섞지 않는다. secrets는 출력에 재표시하지 않는다.

Terminal viewer는 제어문자를 렌더링하되 clipboard/URL open 등 escape sequence로 로컬 side effect를 실행하지 않는다. Artifact HTML/SVG를 Console origin에서 직접 실행하지 않으며 attachment 또는 격리 preview로 제공한다. loading/empty/error/reconnecting/expired 상태와 keyboard 접근성을 UI 인수 테스트에 포함한다.

## E14 배포와 복구

설치 package는 실행 파일·서비스/broker·설정 template·uninstaller·version manifest를 포함한다. Playwright browser는 Agent 실행 identity가 읽을 수 있는 고정 경로에 설치하고 browser revision을 runtime과 함께 기록한다. RE backend는 별도 설치가 기본이며 미설치 상태를 doctor가 설명한다.

Windows/Linux binary는 해당 OS runner에서 build한다. release는 frozen lockfile, dependency license 목록, SBOM, SHA-256, 서명 가능한 환경의 code signing/provenance를 포함한다. 서명 키가 없는 개인용 build는 unsigned로 명시하고 서명된 제품으로 표시하지 않는다. 인터넷 없는 clean VM 설치가 요구되는 배포는 browser 포함 패키지를 별도 제공한다.

upgrade는 새 실행 접수 중단 → 진행 작업 drain 또는 명시적 종료 → DB/설정/metadata backup → migration → binary switch → readiness/smoke test 순서다. destructive migration은 별도 명시적 실행으로만 수행한다. 실패 시 이전 binary와 검증된 backup을 함께 복구하며 새 schema DB에 구버전 binary만 붙이지 않는다.

backup에는 SQLite의 일관된 snapshot, Artifact manifest/blob, config, policy와 필요한 secret 복구 절차를 포함한다. DB 파일만 복사하지 않는다. 기본 운영 목표는 일일 backup의 RPO 24시간, 검증된 clean environment의 RTO 30분이며 실제 복원 측정값으로 판단한다. backup은 Artifact GC와 충돌하지 않도록 snapshot 참조를 pin한다.

uninstall은 service/broker/process를 종료하고 program files를 제거한다. 사용자 data/artifact/credential 제거는 별도 옵션으로 명시한다. v1 자동 원격 self-update는 제외하며 수동 upgrade 절차와 rollback을 먼저 검증한다.

## E15 요구사항과 검증의 추적표

아래 ID는 pytest/contract/E2E 결과와 release checklist에 연결한다. 이번 문서 개정에서 테스트가 실행되었다는 의미는 아니다.

| ID | 요구사항과 합격 조건 | 필수 시점 |
|---|---|---|
| AUTH-01 | 만료/재사용 enrollment, 잘못된 Device token, owner 대신 Device token을 사용한 관리 요청 모두 거부 | Phase 1 |
| AUTH-02 | issuer/audience/Origin/CSRF/객체 소유권 변조가 실행으로 이어지지 않음 | 해당 ingress 구현 시, Phase 12 재검증 |
| RPC-01 | 동일 mutation key 100회 동시 호출 시 side-effect counter=1, payload 변조는 conflict | Phase 2 |
| RPC-02 | 실행 후 ACK/result 유실·Agent crash를 주입해 중복 실행 0, 불명확 결과 UNKNOWN | Phase 2 |
| RPC-03 | protocol/epoch/schema 불일치, 1 MiB 초과/깊이 초과 frame 거부 후 Agent 생존 | Phase 1–2 |
| AUTH-03 | 연결 중 revoke 즉시 접수 차단, 연결 단절 시 lease 60초+cleanup 5초 이내 소유 process 종료 | Phase 2 |
| SHELL-01 | Windows/Linux에서 한글·공백 경로 argv, stdout/stderr·nonzero exit·환경 삭제 확인 | Phase 2 |
| LIFE-01 | child+grandchild 생성 후 timeout/cancel, 소유 process와 port 누수 0 | Phase 2 |
| POLICY-01 | deny 우선·기본 deny·승인 변조/만료/재사용·approval-disabled 거부 | Phase 2 |
| FS-01 | junction/symlink 경계·overwrite/precondition·중간 실패·한글/BOM 파일 원형 보존 | Phase 3 |
| PROC-01 | PID identity 변경 시 종료 거부, wait timeout이 프로세스를 kill하지 않음 | Phase 3 |
| ART-01 | 100 MiB 전송 중 disconnect 후 resume, 최종 SHA-256 일치, 잘못된 scope/hash 거부 | Phase 3–5 |
| ART-02 | quota/disk-full/GC-download 경합에서 READY 손상 0, incomplete cleanup 확인 | Phase 5 |
| PTY-01 | REPL 유지·resize·독립 cursor·ring overflow gap·Unicode 경계·재연결 처리 | Phase 4 |
| STATE-01 | Gateway restart 복구, Agent restart 만료, 늦은 취소/결과가 terminal state를 덮지 않음 | Phase 5 |
| UI-01 | SSE 재접속/gap refresh, UNKNOWN/승인 만료/취소 진행 UI, escape sequence 부작용 차단 | Phase 6 |
| BROWSER-01 | 2 profiles/2 pages 분리, navigation 후 stale ref 거부, dialog/download, evaluate timeout cleanup | Phase 7 |
| DESK-01 | 100/125/150% DPI, 음수 원점 2모니터, preview 좌표 변환, locked/UAC/RDP 상태 거부 | Phase 8 |
| DESK-02 | 잘못된 SID의 IPC 거부, foreground 변경 시 입력 중단, modifier 해제 | Phase 8 |
| RE-01 | 실제 static 1개+debugger 1개 fixture 성공, plugin crash 시 core 생존 | Phase 9 |
| REL-01 | Windows/Linux clean install, credential 보존 upgrade, 실패 rollback, 백업 복원 및 hash 일치 | Phase 11 |
| PERF-01 | §96 부하·soak 목표 충족, RSS/queue/Artifact 사용량 제한 확인 | Phase 12 |
| HOST-01 | 선택 Codex 및 ChatGPT 연결경로에서 인증·tool 결과·Job polling·screenshot preview 확인 | 해당 통합 지원 선언 전 |

핵심 테스트는 실제 fixture로 수행한다. fake backend 통과만으로 Windows GUI/RE/host 호환을 PASS로 기록하지 않는다. GUI E2E는 interactive user runner가 필요하며 headless CI skip은 성공 판정이 아니다. 매 테스트는 임시 계정/디렉터리/VM과 자기 소유 process만 사용하고 운영 Device를 대상으로 실행하지 않는다.

## E16 마일스톤과 작업 산출물

| Milestone | 포함 Phase | 진입 조건 | 검토 가능한 산출물과 종료 조건 |
|---|---|---|---|
| M0 설계 검증 | 0 | 이 기준서 | 고정 compatibility matrix, schema registry 초안, ADR, broker/packaging spike 결과 |
| M1 인증된 실행 | 1–2 | M0 gate PASS | 실제 MCP→WSS→shell, owner/device auth, approval CLI/API, journal, AUTH/RPC/SHELL/LIFE/POLICY 테스트 |
| M2 원격 작업 MVP | 3–5 | M1 gate PASS | fs/process/PTY/Job/Artifact와 CLI, binary resume·복구·quota 검증 |
| M3 Console과 자동화 | 6–8 | M2 gate PASS | realtime Console, browser, Windows Broker/desktop와 이미지 preview |
| M4 분석 확장 | 9, 선택 10 | M3 기반 수명·Artifact 계약 | 실제 static/debugger 각 1개, 지원 backend 목록·라이선스 기록 |
| M5 v1 릴리스 | 11–12 | 필수 feature gate PASS | 설치 패키지/image/Console/CLI, 복구·보안·성능 결과, release checksums/SBOM |

M1 이후 독립적인 구현 workstream을 운영할 수 있지만 protocol/registry 변경을 먼저 병합한 뒤 각 provider에 적용한다.

역할은 구현 담당(코드/schema), 검증 담당(실제 OS·장애 주입 증거), 제품 owner(범위·배포환경 결정)로 정의한다. 1인이 겸임할 수 있지만 phase 보고서에는 수행자와 evidence path를 구분한다. 업무 티켓에는 requirement ID, dependency, 변경 파일 범위, 테스트 command, 완료 artifact를 포함한다.

기간은 인원·경험·RE 라이선스·GUI runner가 확인되지 않아 확정하지 않는다. M0에서 각 Phase의 낙관/기준/비관 person-day와 critical path를 산정하고, M1 실측 속도로 재예측한다. 추정 일정을 테스트 생략의 근거로 사용하지 않는다.

## E17 착수 시 확정할 결정

아래 항목은 문서 누락으로 방치하지 않고 책임·기한·기본안을 기록한다. 기본안으로 독립 작업을 시작할 수 있지만 해당 milestone gate 이전에는 실제 검증 결과가 필요하다.

| 결정 | 기본안 | 확정 시점 | 담당 역할 |
|---|---|---|---|
| OS/runtime/SDK patch | Windows 11 x64, Ubuntu 24.04 x64, Python 3.12.x, Node 22.x, MCP v2 | M0 | 구현+검증 |
| 실제 host와 연결경로 | local Codex 우선, ChatGPT는 인증된 HTTPS/tunnel 경로 별도 검증 | M0 범위 선정, 출시 전 HOST-01 | owner+검증 |
| OAuth provider 및 외부 노출 | 기존 검증된 provider, loopback 기본 | 외부 ingress 구현 전 | owner+구현 |
| Windows service identity와 폴더 ACL | 비관리자 전용 계정, 명시적 workspace root | M0 spike | 구현+검증 |
| RE backend와 라이선스 | Ghidra+GDB, IDA/x64dbg는 환경 확보 시 선택 | M0 범위 확정, M4 검증 | owner+검증 |
| interactive Windows CI | 전용 테스트 VM 사용자 session | M0 환경 확보, M3 gate | 검증 |
| Artifact quota·TTL·backup 위치 | 1 GiB/10 GiB, 7일, 일일 backup | M2 gate | owner+구현 |
| 설치 서명·배포채널 | 개인용 unsigned 허용 표시, 관리된 수동 upgrade | M5 gate | owner+구현 |

추가 ADR 후보는 execution journal과 UNKNOWN, 인증 identity 분리, Windows 실행 계정, Artifact transfer/image adapter, MVP와 v1 지원 범위, lifecycle lease/cleanup이다. 한 문서에서 정한 결정을 여러 ADR에 모순되게 복제하지 않는다.

---

**End of specification**
