# ADR-0014 — Allowlist plugin protocol과 subprocess 수명

상태: Phase 9 기반 개발 구현 · 2026-10-04 KST · 실제 RE backend는 후속

## 설치와 신뢰 경계

`load_approved`는 로컬 관리자가 지정한 manifest 파일의 SHA-256과 허용 permission 집합을
대조한다. manifest link, 상대 executable/cwd, Windows implicit batch shell을 거부한다.
원격 payload로 plugin 경로나 command를 지정하거나 인터넷에서 plugin을 내려받지 않는다.
이 hash는 manifest 내용의 승인 증거이며 설치 binary의 서명/배포 검증을 대신하지 않는다.

manifest에는 name/version/protocol version, capability와 operation input/output schema,
실행 argv/cwd, 명시적 환경 설정, required permissions, backend version과 health timeout을 기록한다.
환경은 기존 Agent allowlist로 구성하여 RACP/OPENAI/AWS credential 변수와 loader/PATH override를 거부한다.
JSON Schema의 reference는 local fragment에 한정하고, validation registry도 외부 retrieval을 제공하지 않는다.

이는 allowlisted 코드의 crash 격리다. 같은 OS 사용자 권한의 악성 plugin을 sandbox한다고 표시하지 않는다.

## Protocol과 한도

`plugin-manifest-v1.schema.json`과 `plugin-stdio-v1.schema.json`은 runtime Pydantic model에서 생성한다.
stdout은 request ID/instance ID에 묶인 result와 순번이 있는 metadata event만 받는다.
UTF-8 JSON line은 newline을 포함해 최대 1 MiB, depth 32, string 256 KiB다.
duplicate field, non-finite number, protocol version의 boolean/float/string 대체와 다른 instance를 거부한다.
result/error는 상태에 따라 하나만 있어야 한다. 승인된 input/output schema도 실행 전후에 검사한다.
plugin의 정상적인 operation 오류와 protocol/output schema 오류는 구분한다.

동시에 접수한 request는 실행 1개를 포함해 64개로 제한하고 deadline은 queue 대기도 포함한다.
stderr는 protocol과 분리하여 tail 64 KiB만 RAM에 보관하며 수명당 총 8 MiB 초과 때 종료한다.
event는 16 KiB/64개 제한이며 sequence gap·overflow는 DEGRADED와 종료로 처리한다.
loss를 숨기거나 이전 generation의 event를 새 instance에 적용하지 않는다.

시작 health는 승인된 manifest hash와 backend version을 확인한다. response는 fresh request와
provider instance에 묶인다. health 무응답/불일치는 READY로 광고하지 않고 프로세스를 정리한다.

## 종료·재시작·불명확 실행

Windows는 base interpreter의 stdlib stdin gate를 Job에 편입한 뒤 target을 실행한다.
gate는 read-ahead를 하지 않아 protocol stdin이 유실되지 않는다. Job은 KILL_ON_CLOSE,
최대 64개 active process, 기본 total memory 2 GiB를 적용한다. manifest의 memory 범위는 64 MiB–8 GiB다.
Job process ID list로 membership을 확인한 kernel handle을 pin하고, 종료 시 accounting과 해당 handle의
종료 signal을 모두 기다린다. 실제 취소 시험에서 accounting만으로 확인하던 race를 발견하여 보강했다.
POSIX process-group 경로도 구현했으나 실제 Ubuntu 실행과 memory 제어는 아직 검증/구현 gate다.

timeout/cancel 때 cancel frame 전송을 최대 250ms 시도한 뒤 소유 tree를 종료하고 cleanup을 최대
5초 확인한다. 시작 취소도 spawn task를 버리지 않고 정리를 기다린다. 확인 실패는 unverified를
유지하며 시작 오류가 이를 complete로 덮지 않는다. 실행이 불명확한 요청은 자동 재실행하지 않는다.

새 요청으로 다시 시작할 때 instance를 교체하고 이전 instance를 지정한 요청은 HANDLE_EXPIRED다.
재시작은 60초당 최대 3회, backoff는 0.5초부터 30초까지다. 30초 이상 안정적인 실행 뒤 실패하면
backoff를 초기화한다. 실패한 operation payload를 replay하지 않으며 현재 라이브러리는 새 요청에서
재시작한다. Agent의 background health/capability 관측 연결은 ADR-0015의 application에서 추가했다.

## 현재 증거와 남은 요구사항

known-source `tests/fixtures/plugin_worker.py`는 실제 별도 프로세스의 protocol 장애 fixture다.
이는 Ghidra/GDB나 fake static/debugger domain 구현을 대신하는 backend가 아니다.
manifest/환경/schema, 정상 결과, protocol fault, event flood, crash side-effect 비재실행,
restart 제한, health 무응답/시작 취소, queue cancel, 실제 child+grandchild 종료를 검증했다.
증거와 수치는 [구현 및 검증 현황](../quality/implementation-status.md)에 유지한다.

Agent runtime/provider dispatch·소유권 Handle·capability 관측과 typed static/debugger 계약,
synthetic domain fixture를 [ADR-0015](ADR-0015-re-application-handles-and-policy.md)에서 연결했다.
ExternalMcpProvider의 고정 mapping/schema 변경 감지, 실제 Ghidra/GDB RACP adapter와 라이선스 기록,
durable event/storage·전체 인수 gate는 남아 있다. Phase 9/RE-01 완료가 아니다.

## 공식 기술 근거

- [Job state query](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject)
- [Nested Job process ID list](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_process_id_list)
