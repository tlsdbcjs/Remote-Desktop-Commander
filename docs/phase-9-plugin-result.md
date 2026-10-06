# Phase 9 plugin 기반 부분 구현 결과

2026-10-04 KST · Windows 10 Pro x64 · Python 3.12.11 · jsonschema 4.26.0 · referencing 0.37.0

allowlist manifest와 versioned JSON-line protocol, 독립 subprocess supervisor 기반을 추가했다.
Agent application의 고정 registry/정책/Handle/health/출력 경로까지 후속 연결했다.
실제 GDB/MI와 Ghidra headless adapter의 후속 결과는 아래에 기록한다. Phase 9 전체 gate는 미완료다.
설계와 제한은 [ADR-0014](adr/ADR-0014-allowlisted-plugin-supervisor.md)에 기록한다.

## 검증 범위

`tests/unit/test_plugin_contract.py`와 `tests/integration/test_plugin_supervisor.py`가 다음을 확인한다.

- 승인 manifest SHA-256/permission/env 경계와 remote schema retrieval 차단.
- UTF-8/한도, duplicate field/non-finite number/result·error 구분과 정확한 integer version.
- 실제 별도 worker의 health hash/version, 정상 응답과 output schema 검증.
- 다른 request/instance, oversized stdout와 event flood 때 DEGRADED와 process 정리.
- crash 전 side-effect counter가 같은 요청의 자동 재실행으로 증가하지 않음.
- 초기 실행 뒤 60초당 최대 3회 restart, instance 변경과 기존 ref의 HANDLE_EXPIRED.
- stderr 90,000 byte 수집 뒤 tail이 65,536 byte로 제한됨.
- 무응답/시작 취소/operation timeout/cancel 뒤 소유 child+grandchild 종료.
- 64개 admission 한도와 queued request 취소가 현재 실행을 종료하지 않음.
- native startup cleanup의 unverified 상태를 보존하는 경계 주입.

최신 전체 검사에서 plugin 계약/수명 **27개 통과**를 확인했다(unit 13개, integration 14개).
integration에는 실제 별도 worker fixture와 native cleanup 실패 경계 주입을 함께 포함한다.
최신 전체 결과와 source/build 수치는 [구현 현황](implementation-status.md)에 유지한다.
초기 standalone report는 `dist/plugin-contract-results.xml`의 23개이며, 정확한 version 타입 거부
4개까지 포함한 최신 증거는 `dist/test-results.xml`이다.
최초 large byte parameter의 pytest ID가 Windows 환경변수 길이 상한을 넘긴 시험 오류는 짧은 ID로 수정했다.
최초 실제 취소 시험의 kernel handle 종료 확인 실패는 Job member handle pin/종료 signal 검사로 보강했다.
이를 8시간 soak나 전체 Agent plugin crash 격리 gate의 PASS로 확대하지 않는다.

## 후속 범위

ExternalMcpProvider와 승인된 외부 MCP mapping/schema hash 변경 감지가 남아 있다. static/debugger typed protocol과
Agent journal/Handle/owner scope·capability 연결의 후속 결과는 아래에 기록한다.
실제 static 1개와 debugger 1개, 소스/symbols fixture, analysis database와 dump Artifact,
launch/attach 수명, stop event/Job 분리와 backend 라이선스 기록을 구현·검증해야 RE-01을 통과한다.
실제 Ubuntu·Windows 11, Agent core의 crash/lease/revoke 통합과 memory/soak gate도 남아 있다.

로컬 도구 조사에서 설치된 GNU GDB 17.1과 Oracle Java runtime 22.0.2의 version 출력을 확인했다.
이는 실제 RACP 연동이나 source/symbol fixture, Ghidra Java 호환 및 재배포 조건의 PASS가 아니다.
다음 구현에서 고정된 실행 경로/permission과 실제 backend fixture를 연결한다.

## Application과 typed domain 후속

[ADR-0015](adr/ADR-0015-re-application-handles-and-policy.md)의 registry/정책과 Agent 설정,
owner/Device/boot/instance에 묶인 Handle, private target snapshot·hash,
pagination, debugger stop wait·memory Artifact를 연결했다.
`tests/fixtures/re_plugin.py`는 명시적으로 설치한 synthetic domain contract server다.
이를 실제 static/debugger backend의 PASS로 기록하지 않는다.

후속 시험은 실제 HTTP에서 open key 재사용, Handle catalog, cursor scope·mutation invalidation,
cross-owner 거부, decompile 미지원, close/restart 만료, stop event 분리·memory 64 KiB Artifact,
plugin crash 뒤 Agent ONLINE/filesystem 유지와 임의 파일 업로드 descriptor 거부를 확인했다.
실제 MCP SDK의 tagged payload와 CLI `--payload-json`도 통과했다.
정확한 schema/action/주소 범위와 read/mutation policy를 포함한 최신 수치는 전체 검증 기록에 유지한다.
application 후속 시험 **13개 통과**(schema/policy 8개, 실제 Agent/HTTP domain 4개, MCP/CLI 1개),
당시 전체 **219 passed/12 skipped**, Windows/Linux 타입 target **111개 source**, Console E2E **11 passed**,
Console build와 8개 개발 wheel/SHA-256 검증을 확인했다. Linux 타입 검사는 실제 Ubuntu 실행을 대신하지 않는다.
초기 Agent restart 직후에는 backend capability가 아직 준비되지 않아 operation을 거부하는 것이 맞으며,
시험은 readiness 확인 뒤 이전 Handle의 만료를 검증하도록 수정했다. MCP 시험의 trailing slash와
SDK v2 `is_error` 접근도 실제 설치 API에 맞게 수정했다.

## 실제 GDB 도구 fixture 준비

기존 GNU GDB 17.1과 portable LLVM-MinGW 20260908 / LLVM 23.1.1을 사용했다.
compiler archive의 release SHA-256은
`1bcf74d06b724aeecaa6412ca85f5b26fb1da770e7cdcefa9263c9c5c3ad34b6`이다.
[공식 release](https://github.com/mstorsjo/llvm-mingw/releases/tag/20260908)의 asset digest를 확인했다.
설치는 workspace `.tools`에만 있으며 system PATH나 Windows 설치 등록을 변경하지 않는다.
`scripts/bootstrap_re_compiler.py`, `scripts/build_re_fixture.py`가 도구와 소스/대상 hash·컴파일 argv를 기록한다.

`tests/fixtures/re_program.c`를 DWARF 4/O0로 컴파일했다. 실제 GDB의
file → main breakpoint → run → breakpoint stop → RIP register 조회 → detach를 확인했다.
실행 target은 자체 fixture뿐이며 GDB auto-load와 init file을 끈 경로다.
GDB binary의 XML 미지원/host charset warning도 관측했으므로 모든 모듈/Unicode 호환을 주장하지 않는다.
이는 실제 도구/fixture 준비 증거다. 이후 typed RACP API 연동 결과는 다음 절에 기록한다.

실제 attach·TTL/revoke/cancel integration 확대,
durable backend event replay·analysis database catalog/retention·외부 MCP bridge,
참조 OS/라이선스·SBOM·전체 memory/soak 인수검증은 계속 남아 있다.

## 실제 GDB RACP adapter

[ADR-0016](adr/ADR-0016-native-gdb-mi-launch-and-owned-target.md)의 MI parser/driver와 고정 debugger API를
연결했다. approved executable hash, backend health/version, native target identity/소유 Job,
launch 초기 event fence와 후속 stop/exited event를 구현했다. scalar register와 STOPPED memory/backtrace,
launch target close를 제공한다. 현재 main symbol이 있는 binary를 요구하고 attach는 노출하지 않는다.

실제 Gateway/Agent HTTP native 시험 **2 passed**와 parser/hash unit **8 passed**를 확인했다.
breakpoint/continue ACK/후속 stop/RIP/code bytes/backtrace/close 및 plugin Job crash 뒤 target 종료,
Handle EXPIRED와 Agent core/filesystem 유지를 확인했다. 증거는 `dist/gdb-native-results.xml`이다.
이 검증은 fake backend나 GDB CLI 직접 호출과 구분된다. 실행 명령은 다음과 같다.

```powershell
$env:RACP_TEST_GDB = 'C:/Users/GhostShell/scoop/apps/gdb/17.1/bin/gdb.exe'
uv run pytest -q tests/integration/test_gdb_native.py tests/unit/test_gdb_mi.py --junitxml=dist/gdb-native-results.xml
```

현재 GDB build의 UTF-8 charset 설정 실패는 ASCII만 허용하고 비ASCII argv를 명시적으로 거부하도록
처리했다. launch 응답 전 초기 event의 늦은 적용으로 Handle이 만료된 경합도 수정하고 같은 native 시험을
통과했다. binary hash 교체 거부는 별도 unit evidence다. 로컬 approval helper는 manifest/config/provenance를
생성하며 GDB를 wheel에 포함하지 않는다. 전체 재배포/라이선스/SBOM 검증은 계속 남아 있다.

## 실제 Ghidra RACP adapter

Ghidra 12.1.4 공식 ZIP digest를 확인하여 workspace에 준비하고 Oracle JDK 22.0.2로 실제 분석했다.
고정 Java bridge, 로컬 runtime/Java/catalog hash 승인, private project, 직접 Java argv와 Windows 소유 Job을 연결했다.
실제 HTTP native 2개와 runtime/containment unit 6개는 **8 passed**이며 `dist/ghidra-native-results.xml`에 남긴다.
functions/strings/xrefs/disassemble/decompile, pagination, 한글 rename/comment의 실제 저장,
rollback된 잘못된 rename의 FAILED와 기존 Handle 유지, cursor 만료, close와 database 유지,
plugin Job crash 뒤 Java 종료/UNKNOWN/Handle EXPIRED/Agent core 유지가 포함된다.
불확실한 mutation/open 또는 저장 확인 실패는 generation 종료/UNKNOWN이며 자동 재실행하지 않는다.
[ADR-0017](adr/ADR-0017-native-ghidra-headless-analysis.md)에 계약과 한계를 기록한다.

```powershell
uv run python scripts/bootstrap_ghidra.py
$env:RACP_TEST_GHIDRA = "$pwd/.tools/ghidra_12.1.4_PUBLIC"
$env:RACP_TEST_JAVA = 'C:/Program Files/Common Files/Oracle/Java/javapath/java.exe'
uv run pytest -q tests/integration/test_ghidra_native.py tests/unit/test_ghidra_runtime.py --junitxml=dist/ghidra-native-results.xml
```

Linux의 상위 수명에 묶인 containment는 미구현이므로 Ghidra health에서 capability를 거부한다.
database의 durable catalog/reuse/retention/backup과 event/ACK, External MCP와 release gate는 남아 있다.
사용자의 추가 지시에 따라 이후 우선순위는 [원격 PC 핵심 흐름](remote-pc-core-plan.md)으로 옮긴다.
