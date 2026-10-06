# Phase 0–2 기반 구현 결과

일자: 2026-10-01 KST · 수행자: Codex · 전체 goal: 계속 진행 중

## Implemented

실행 가능한 uv workspace 8개 package와 single-worker Gateway/foreground Agent/CLI를
구현했다. owner bootstrap과 DPAPI credential 저장, 1회 Device enrollment, outbound
WebSocket hello/welcome/reconcile/heartbeat/reconnect/epoch fencing/revoke가 동작한다.

read_only 기본 거부, trusted_personal opt-in, standard의 owner 일회 승인과 동일
operation_id 진행을 구현했다. Gateway/Agent SQLite journal이 실행 전 commit되고
동일 mutation key의 payload 변조와 새 실행 ID 재사용을 거절한다. 재시작 시 미확정
Agent 기록은 UNKNOWN으로 남고 자동 재실행하지 않는다.

셸 argv/명시적 shell/cwd/env/encoding, stdout/stderr 분리, nonzero exit 보존,
timeout/cancel, 최소 Job 조회와 Artifact spill을 구현했다. Windows 가상환경 launcher
race를 실제 실패 테스트로 확인하여 base interpreter gate와 Job Object containment로
수정했다. 완료된 결과는 늦은 취소로 덮어쓰지 않는다.

## Changed files

- `apps/gateway/src/racp_gateway`: HTTP/MCP/WS adapter, control plane, 인증·승인 저장소, 출력 Artifact.
- `apps/agent/src/racp_agent`: runtime, execution journal, lease watchdog, shell provider와 Windows gate.
- `apps/cli/src/racp_cli`: 보호된 credential 사용, enrollment/승인/조회/실행 CLI와 UTF-8 JSON.
- `packages/*`: domain, strict transport schema/registry, policy, logging, shared SQLite/security adapters.
- `scripts/*`, `.github/workflows/quality.yml`, `tests/*`: frozen 품질 gate/build/schema 생성과 실제 실행 검증.
- `README.md`, `docs/adr`, `docs/protocol`, `docs/compatibility.md`: 실행 방법과 구현/검증 기준.

기존 개발정의서/검토결과는 수정하지 않았다.

## Tests

실제 Windows 10 Pro x64 10.0.19045에서 `uv run python scripts/check.py` 실행:

- frozen dependency sync PASS.
- Ruff format/check PASS.
- mypy strict: 26 source files PASS.
- pytest: **20 passed, 0 failed, 0 skipped**.
- 실제 MCP SDK HTTP → Gateway → Agent → Python --version PASS.
- 실제 CLI 인증/장치 조회/셸 실행/정책 거부/credential 비노출 PASS.
- 동일 key 100회 동시 호출 side-effect=1, payload 변경 conflict PASS.
- timeout child+grandchild, Job cancel, revoke, lease watchdog cleanup PASS.
- lost-result 재연결 복구와 Agent journal restart UNKNOWN 보존 PASS.
- Artifact SHA-256/owner scope, schema drift, UTF-8/한글·공백 처리 PASS.

JUnit evidence: `dist/test-results.xml`. 테스트는 자기 소유 임시 디렉터리/프로세스만
사용했다. lease 시험은 실제 watchdog에 만료 deadline을 주입했으며 60초 wall-clock
단절 시험을 완료한 것으로 해석하지 않는다.

## Build

`uv run python scripts/build.py`: workspace 8개 wheel 생성 PASS.
`dist/build-manifest.json`에 wheel size/SHA-256와 lockfile digest를 기록했다.
개발용 unsigned wheel이며 서비스 installer/clean-install release artifact는 아니다.

## Known limitations

M0와 M1의 모든 gate를 PASS로 표시하지 않는다. Windows 11/Ubuntu 참조 runner,
Windows service identity/ACL/Broker spike, 실제 Codex/ChatGPT host, remote WSS/OAuth가
남아 있다. broker/Console/browser/RE/PTY/fs/process는 아직 제공하지 않는다.

SQLite stdlib adapter 결정은 ADR-0001에 기록했고 full migration/backup/rollback은
Phase 11 작업이다. Artifact resume/GC/full quota, inventory 복구 확대, approval
TTL/disabled와 실제 crash 부정 테스트도 추가해야 한다.

## Next phase

Phase 0–2 남은 부정/실패 테스트와 호스트 검증을 확장하고 Phase 3 filesystem/process,
Phase 4 persistent PTY, Phase 5 full Job/Artifact/Handle을 구현한다. 전체 요구 범위를
줄이거나 현재 기반 구현을 MVP/v1 완료로 표시하지 않는다.
