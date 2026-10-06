# ADR-0016: 실제 GDB/MI launch adapter와 소유 target 수명

- 날짜: 2026-10-04 KST
- 상태: 개발 구현·Windows 10 실제 fixture 검증, Phase 9 전체 gate는 미완료
- 수행: Codex 구현/로컬 검증
- 요구사항: RE-01, E5 process cleanup, E11 debugger state/stop event
- 의존: ADR-0014 plugin supervisor, ADR-0015 application Handle/정책

## 결정

`gdb_plugin.py`는 승인된 로컬 GDB executable을 MI2 subprocess로 실행한다.
설치 helper는 manifest, plugin config, backend provenance를 작성하고 executable SHA-256을
manifest argv에 고정한다. Agent의 승인 manifest SHA-256과 별도로 plugin이 health 및 새 launch에서
backend executable hash를 검사한다. hash 변경은 PLUGIN_VERSION_MISMATCH다.
GDB executable은 개발 wheel에 포함하지 않는다. provenance에 실제 경로/version/hash와
GPL-3.0-or-later를 기록한다. 전체 배포 dependency/SBOM gate는 별도 후속이다.
[공식 GDB license 문서](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Copying.html)를 참조한다.

공개 입력은 기존 fixed typed debugger API다. raw GDB command/expression은 노출하지 않는다.
MI parser는 numeric token, result/async/stream record, bounded C string/tuple/list와 반복 frame entry를
처리하며 중복 result/tuple key, 잘못된 framing, 1 MiB 초과와 depth 32 초과를 거부한다.
GDB init/auto-load를 끄고 전용 worker에서 실행한다. stdout/stderr log는 합계 8 MiB까지 수집량을 제한하며
plugin stdout에는 RACP frame만 전달한다. 이 경계는 악성 target을 격리하는 OS sandbox를 뜻하지 않는다.
[MI 출력 문법](https://sourceware.org/gdb/current/onlinedocs/gdb.html/GDB_002fMI-Output-Syntax.html)을 따른다.

launch는 Agent가 snapshot/hash를 확정한 자체 target을 사용하고 임시 main breakpoint에서 시작한다.
현재 adapter는 main symbol이 있는 PE/ELF를 요구한다. 실제 검증 architecture는 Windows x86_64 PE다.
GDB session은 최대 4개다. launch target은 plugin의 소유 Job/group에 포함되며 native PID/create_time을
Agent가 검증한다. close는 launch한 target을 종료한다. attach는 manifest에 제공하지 않는다.
기존 process의 detach/소유권 의미를 구현하기 전 attach capability를 광고하지 않는다.

continue/step/interrupt 접수와 stop event를 구분한다. stopped/exited event의 sequence와 reason을
Agent Handle/history에 반영하며 debugger.wait는 application의 별도 대기다.
launch 응답 전 초기 event는 전달하지 않고 초기 state를 descriptor로 확정한다.
이후 event만 전달하여 늦게 도착한 초기 running event가 새 Handle을 만료시키는 경합을 방지한다.
register/memory/backtrace는 STOPPED에서만 실행한다. memory chunk는 16 KiB이며
Agent의 최대 1 MiB read/Artifact 경로를 사용한다.
[MI 실행 명령](https://sourceware.org/gdb/current/onlinedocs/gdb.html/GDB_002fMI-Program-Execution.html),
[MI 데이터 조회](https://sourceware.org/gdb/current/onlinedocs/gdb.html/GDB_002fMI-Data-Manipulation.html).

현재 stdio server는 active request 중 cancel frame을 병렬 처리하지 않는다.
supervisor의 취소 budget 뒤 소유 Job/group 종료가 cancellation 경계다. 종료 확인 실패는 UNKNOWN이며
side effect를 자동 재실행하지 않는다. standalone POSIX target cleanup은 실제 Ubuntu 검증이 필요하다.

## 실제 증거

`tests/integration/test_gdb_native.py`의 opt-in 시험 2개가 실제 Gateway/Agent HTTP를 사용한다.
GNU GDB 17.1, LLVM-MinGW 20260908/LLVM 23.1.1로 만든 DWARF 4/O0 known-source fixture다.
source/target SHA-256을 비교하며 자기 소유 임시 process만 사용한다.

1. launch → main STOPPED → racp_add breakpoint → continue ACK → 후속 breakpoint stop →
   RIP register/16-byte code memory/backtrace → close/CLOSED와 실제 target 종료.
2. plugin Job 강제 종료 → native kernel target handle 종료 확인 → Handle EXPIRED →
   Agent core 유지와 filesystem.stat 성공.

두 native 시험과 parser/hash unit 8개는 `dist/gdb-native-results.xml`에 **10 passed**다.
일반 전체 검사에서는 native GDB 2개를 opt-in skip하며 PASS로 합산하지 않는다.
기본 setup은 `python -m racp_agent.plugins.gdb_installation`으로 실제 실행하여 config/manifest/provenance
생성을 확인했다. test command와 남은 gate는 [구현 및 검증 현황](../quality/implementation-status.md)에 기록한다.

## 제한과 남은 작업

설치된 GDB 17.1 build는 UTF-8 host/target charset을 제공하지 않는다.
이 build에서 ASCII target path/argv만 허용하며 비ASCII 입력은 OPERATION_NOT_SUPPORTED다.
이번 시험은 한글 workspace의 원본을 ASCII 경로의 Agent data 아래 private snapshot으로 준비했다.
이 GDB build를 사용할 때 snapshot이 위치하는 Agent data의 전체 경로도 ASCII여야 한다.
debugger.info의 utf8_arguments_supported로 build의 지원 여부를 조회한다.
GDB의 expat/XML 미지원도 관측했으므로 module metadata 전체 호환을 주장하지 않는다.

실제 Ghidra 분석, attach/detach, stripped binary/다른 architecture, TTL/revoke/cancel 장애 확대,
durable backend event replay/ACK, analysis database catalog/retention, External MCP mapping,
Windows 11/Ubuntu, 실제 host, memory/8시간 soak와 설치·release license/SBOM gate는 남아 있다.
RE-01/M4 전체 완료로 표시하지 않는다.
