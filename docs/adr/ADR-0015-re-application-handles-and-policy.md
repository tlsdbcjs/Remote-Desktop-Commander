# ADR-0015 — Agent RE operation과 Handle 경계

상태: Phase 9 application 기반 개발 구현 · 2026-10-04 KST · 실제 backend adapter는 후속

## 고정 API와 설치

공개 registry에는 `re.backends/open/query/command/close/keepalive`,
`debugger.backends/launch/attach/command/info/registers/read_memory/backtrace/wait/close/keepalive`를 등록했다.
query/command는 action별 tagged union이다. 임의 raw 문자열 명령은 이 schema에 포함하지 않는다.
query·관측은 read_only에서 허용하고 open/launch/attach/command/close/keepalive는 mutation이다.
standard는 일회 승인, trusted_personal은 Gateway와 Agent의 명시적 opt-in을 요구한다.

`--plugin-config <절대 local 경로>`와 service config의 `plugin_config`는 최대 4개 설치를 정의한다.
각 설치는 manifest 절대 경로·SHA-256·허용 permission 목록을 포함한다. manifest 검증 실패는
backend configuration 오류로 표시하며 core Device ONLINE을 막지 않는다. 동적 tool 목록을 public
registry에 직접 추가하지 않는다. 설치하지 않은 기능을 실행으로 우회하지 않는다.

RE command/query의 CLI는 `--payload-json`을 사용하고 MCP는 typed `payload` parameter를 사용한다.
내부 canonical operation의 payload는 그대로 tagged object이며 별도 policy나 DB 접근 경로를 만들지 않는다.

## Resource와 target snapshot

Handle은 analysis/debugger type, owner·Device·boot·plugin instance, revision·1시간 idle TTL을 갖는다.
최대 active 8개/history 64개다. Backend의 opaque resource ID를 public ID로 노출하거나 사용자가
backend ID로 다른 scope를 선택하게 하지 않는다. restart/lease 종료/plugin 실패 시 기존 Handle은 만료한다.

target은 workspace의 no-follow file descriptor로 읽어 private Agent directory에 snapshot한다.
source change/hash mismatch와 1 GiB 초과를 거부한다. copy는 operation budget과 취소를 확인하고
취소 때 native file thread의 종료를 기다린다. private root는 현재 Agent OS identity와 SYSTEM ACL/0700이다.
analysis database 경로는 해당 target directory 아래에 있어야 한다. closed database는 Handle을
복구하는 근거로 사용하지 않고 보존하며 durable catalog·재사용·retention/backup은 후속 storage gate다.

plugin은 `spool_path/artifact_id/output_id/handle/preview/_artifact_path` descriptor를 직접 반환할 수 없다.
임의 Agent 파일을 업로드하게 만드는 경로를 차단하고 파일/Artifact 게시 결정은 application에서 한다.
pagination cursor는 owner·Device·Handle·query·revision에 결합한 5분 서명 token이며 backend cursor는
bounded private cache 128개로 보관한다. mutation 뒤 이전 cursor는 CURSOR_EXPIRED다.

Debugger attach에는 PID/birth/boot, OS SID/session 또는 UID와 protected PID 경계가 있다.
native launch descriptor의 PID/birth와 소유 Job/process group membership을 다시 확인한다.
이 typed 계약을 등록한 사실은 실제 Windows attach/detach/crash 보존 gate의 PASS가 아니다.

## Debugger event와 출력

continue/step 접수 응답과 STOPPED event를 분리한다. plugin event는 원래 resource와 instance에만
적용하며 accepted response를 늦은 상태 덮어쓰기로 바꾸지 않는다. `debugger.wait`는 별도 stop
history를 기다리고 timeout이 target을 kill하지 않는다. history는 64개로 제한하고 gap을 명시한다.
registers/memory/backtrace는 기본 STOPPED에서만 허용한다.

memory는 최대 1 MiB, 64-bit address overflow를 거부한다. 작은 chunk로 plugin에서 회수하고
4 KiB 초과는 Agent가 만든 binary spool/기존 Artifact 경로로 게시한다. 관측의 consistency는
stopped_target_observation이며 외부 writer와의 atomic memory snapshot을 주장하지 않는다.

core heartbeat와 별도 health task가 backend 가용성을 갱신한다. backend event/실패는 기존 heartbeat의
capability와 Handle observation을 통해 Gateway/SSE의 상태 갱신으로 연결한다. lease 종료/shutdown은
owned plugin tree를 정리한다. 별도 debugger event의 durable replay/ACK와 attached target의 강제
종료 보존, 운영 storage/고부하 gate는 계속 남아 있다.

## 현재 증거

실제 Agent/Gateway/SQLite/HTTP/MCP SDK/CLI를 synthetic domain fixture와 연결해 검증했다.
known-source native C fixture와 설치 GDB의 직접 breakpoint/register/detach 시험도 별도로 수행했다.
후자는 RACP GDB adapter의 완료를 대신하지 않는다.
현재 수치와 남은 요구사항은 [구현 및 검증 현황](../quality/implementation-status.md)에 기록한다.
