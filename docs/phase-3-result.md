# Phase 3 파일·프로세스 구현 진행 결과

일자: 2026-10-02 KST · 수행: Codex · 전체 구현: 진행 중

## Implemented

filesystem read/write(create·replace·append)/stat/list/mkdir/copy/move/delete/search/hash와
process list/inspect/spawn/terminate/wait/tree를 registry, Agent capability, policy,
durable execution journal, HTTP operation API, CLI와 MCP에 연결했다.

파일 API는 workspace 경계를 검사한다. Windows는 실제 읽기 권한의 directory handle과
no-delete share mode를 사용해 ancestor rename을 막고, leaf는 reparse point를 따라가지
않는다. POSIX backend는 O_NOFOLLOW dirfd를 사용한다. 기본 UNC/ADS/drive-relative 경로를
거부하고 system path mutation과 workspace root 삭제를 보호한다.

파일 replace는 임시 파일/fsync/precondition 재검사/atomic publish를 사용하고 ACL/mode와
UTF-8 BOM/newline을 보존한다. create는 no-overwrite publication, append는 byte offset과
mutation key를 요구한다. 재귀 작업에는 depth/count/budget를 적용하고 부분 실패 보고를
Artifact로 반환한다. 외부 writer와 원자적 CAS를 보장한다는 주장은 하지 않는다.

프로세스 spawn은 owner/boot/provider/TTL을 가진 Handle과 Windows Job Object 또는 POSIX
process group에 연결한다. lease 만료와 Agent 종료 시 managed tree를 정리한다.
PID/create_time/boot mismatch는 종료를 거절하며 wait timeout은 target을 kill하지 않는다.
Windows graceful 경로는 WM_CLOSE/console signal, force는 명시적으로 선택한다.

binary read 및 큰 JSON은 Artifact로 제공하고 CLI download는 SHA-256/size 확인 후 파일을
게시한다. Gateway control plane에서 FastAPI WebSocket 의존을 제거하고 domain channel
port로 연결했다.

## Changed files

- `apps/agent/src/racp_agent/providers/{paths,filesystem,process,spawn_gate,containment}.py`
- `apps/agent/src/racp_agent/runtime.py`
- `packages/protocol/src/racp_protocol/{provider_models,registry,models}.py`
- `packages/policy/src/racp_policy/engine.py`, `packages/sdk/src/racp_sdk/pagination.py`
- `apps/gateway/src/racp_gateway/{service,mcp,artifacts}.py`, `apps/cli/src/racp_cli/main.py`
- `tests/integration/test_filesystem_process.py`, `test_filesystem_failures.py`, `test_cli.py`
- ADR-0002、generated schema/registry、README/작업 현황

## Tests

Windows 10 Pro x64의 전체 regression은 **31 passed, 1 skipped**였다. skip은 POSIX dirfd
시험이며 Linux 지원 성공으로 표시하지 않는다. Windows/Linux 타입 target 모두 33개
source file에서 mypy를 통과했다. 최신 JUnit은 `dist/test-results.xml`에 기록한다.

실제 Windows fixture에서 확인한 항목:

- 한글·공백 파일, UTF-8 BOM/CRLF 보존, hash precondition, append replay.
- workspace 이탈/UNC/drive-relative/junction 거부, root 삭제 보호.
- directory read handle 유지 중 ancestor rename 거부.
- file/directory copy, move, hash, pagination/cursor scope, recursive delete.
- 중간 disk-full 주입 후 이미 완료한 복사와 실패 경로가 보고서 Artifact에 보존됨.
- byte-for-byte binary read/download와 media type/hash metadata.
- 실제 process spawn/inspect/tree, stale PID identity 거부, wait timeout의 target 생존.
- force/lease cleanup, 실제 CLI fs read/write/Artifact download, 실제 MCP fs_stat.

Win32 share-mode의 속성 조회 전용 handle은 rename을 막지 못한다는 실패를 실제로
확인했다. GENERIC_READ handle로 수정하고 같은 regression을 통과했다.

## Build

8개 workspace wheel 및 SHA-256 manifest 재생성을 완료했다. 개발 wheel이며 clean install,
서비스 installer 또는 signed v1 릴리스 완료를 의미하지 않는다.

## Known limitations

이 기록의 binary transfer 관련 잔여 항목은 후속
[Artifact 검증 기록](phase-3-5-artifact-result.md)에서 구현·검증했다.

Phase 3 gate 전체는 아직 PASS가 아니다. Gateway→Agent binary write, scoped/resumable
transfer와 100 MiB disconnect/resume는 구현이 남았다. cross-volume Job move와 Windows
GUI/console graceful 종료는 코드가 있지만 실제 해당 OS fixture gate는 UNVERIFIED다.

Docker Linux engine 연결은 실패했고 등록된 docker-desktop WSL은 Stopped였다. Ubuntu
runner에서 실제 fs/process를 실행한 것으로 표시하지 않는다. Windows 11과 service/broker,
Codex/ChatGPT 실제 host 검증도 여전히 남아 있다.

Handle inventory/복구/full Job·Artifact quota/GC는 Phase 5에서 확장해야 한다.
process.spawn의 출력 모드는 discard이며 PTY 기능을 대신하지 않는다.

## Next phase

Phase 3 binary write와 transfer API를 먼저 완성하고 Phase 4 persistent PTY와 Phase 5의
복구·quota·GC·Handle/Job 계약을 구현한다. Console/Browser/Desktop/RE/Packaging/Hardening
전체 범위도 계속 유지한다.
