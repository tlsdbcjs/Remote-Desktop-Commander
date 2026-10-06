# CORE-WORKSPACE-04: 원격 PC의 여러 작업 폴더

2026-10-05 KST · 구현/로컬 검증 · 전체 목표는 진행 중

PC가 직접 승인한 기본 폴더와 최대 15개 추가 폴더를 지원한다. 등록/보호된 설정/ServiceConfig,
HTTP/MCP/CLI, Console에서 동일한 ID를 사용한다. AI는 PC capability의 폴더 목록을 확인하여
요청마다 workspace를 선택하고 복사·이동에는 목적지 workspace를 별도로 지정한다.

실제 MCP의 저장 → 검색 → 다른 폴더로 복사·이동 → 명령 실행 → Agent 재시작 후 읽기를 확인했다.
바이너리 Artifact를 선택한 폴더에 저장하고 실제 CLI로 조회했으며, process와 지속 terminal이
그 폴더에서 시작한 뒤 기본 workspace를 사용하는 후속 Handle 호출에도 cwd를 유지했다.
별도 HTTPS 등록 Agent는 추가 폴더를 보호된 설정에 저장하고 재시작 후 MCP 저장/실행에 사용했다.

존재하지 않는 workspace, 다른 폴더의 절대 경로/경로 탈출, 실제 Windows junction과 교체된 root를
거부했다. 중첩 폴더를 포함한 전체 root 복사와 보호 root/상위 폴더 삭제·이동의 실행 전 거부,
40개 동시 읽기의 선택 분리도 확인했다. 기본 폴더의 기존 digest 형식을 유지하며 같은 mutation
key의 source/destination workspace 변경은 충돌한다. 승인 화면과 승인 실행도 선택을 유지한다.

증거:

- `tests/unit/test_workspaces.py`: 8개 경계/동시성 시험.
- `tests/integration/test_workspace_workflow.py`: 3개 실제 Gateway/Agent/MCP/CLI/Artifact/Handle 흐름.
- `tests/integration/test_pc_onboarding.py`: 실제 별도 HTTPS 등록/저장된 추가 폴더/resume.
- 위 묶음과 schema drift: `dist/workspaces-results.xml`, **13 passed**.
- 전체 `scripts/check.py`: `dist/test-results.xml`, **283 passed, 17 skipped**.
- Windows/Linux 타입 target: **124개 source file** 통과. frozen sync/Ruff format/lint 통과.
- Node 22.23.0 Console format/type/client drift/production build 통과.
- 실제 Chromium Console 전체 **14 passed**, `dist/console-test-results.xml`.
  PC를 명시적으로 선택하여 추가 폴더에서 실행하고 한글 cwd 출력/실제 파일과 기본 폴더의
  파일 부재를 확인했다. `dist/console-workspaces.png`에 화면을 남겼다.
- `scripts/build.py`: 8개 unsigned 개발 wheel. SHA-256/frozen lock/124개 Python source 일치 확인.

검증 환경은 Windows 10의 같은 물리 PC다. 파일 도구의 경계를 shell의 OS 권한 격리로
표현하지 않는다. 임의 command/script는 OS 계정 권한으로 실행한다. 실제 AI host, 실제 두 PC,
Windows 11/Ubuntu runtime, 다른 드라이브 cross-volume move와 installer/자동 시작/soak는
미검증 gate다. 건너뛴 17개 opt-in/참조 OS 시험은 전체 현황에 유지한다.

[사용 안내](named-workspaces-guide.md), [설계 결정](adr/ADR-0021-explicit-named-workspaces.md).
