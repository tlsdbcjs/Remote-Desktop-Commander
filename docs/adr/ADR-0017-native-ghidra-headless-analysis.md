# ADR-0017: 실제 Ghidra headless adapter와 private 분석 프로젝트

- 날짜: 2026-10-04 KST
- 상태: Windows 10 개발 구현·실제 fixture 검증, Phase 9 전체 gate 미완료
- 수행: Codex 구현/로컬 검증
- 요구사항: RE-01, E5 process cleanup, E11 정적 분석 Handle/주소/pagination
- 의존: ADR-0014 plugin supervisor, ADR-0015 application Handle/정책

## 결정과 구현

공개 API는 기존 fixed re.open/query/command/close/keepalive다. 원격 요청으로 Ghidra script,
Java class, shell command를 선택하지 않는다. `RACPStaticBridge.java`가 info/functions/strings/xrefs/
disassemble/decompile/rename/comment만 실행하고 plugin은 typed payload를 검증한다.
keepalive와 public Handle/owner/Device/boot/instance scope는 Agent application에서 처리한다.
decompile은 실제 Ghidra DecompInterface를 사용한다.

`scripts/bootstrap_ghidra.py`가 공식 Ghidra 12.1.4 release를 workspace에 준비한다.
ZIP SHA-256은 `ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db`다.
digest·ZIP member 경로/링크·추출 크기를 검사하며 기존 installation 위에 추출하지 않는다.
[공식 release](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.1.4_build).
이 release의 application.properties는 Java 최소 21, 최대 제한 없음이며 실제 Oracle Java/javac
22.0.2 x64로 분석을 검증했다. master branch의 설치 요구사항으로 release 조건을 대신하지 않는다.
[고정 release 설치 안내](https://raw.githubusercontent.com/NationalSecurityAgency/ghidra/Ghidra_12.1.4_build/GhidraDocs/GettingStarted.md).

로컬 approval helper는 manifest/config와 runtime catalog를 생성한다. docs 폴더를 제외한
실제 runtime 3,299개 파일과 Java executable의 SHA-256을 기록한다. 승인 manifest가 catalog hash를
고정하고 plugin이 초기 health와 파일 inventory/metadata 변경을 검사한다. 매 분석 실행 전에는
metadata cache를 사용하지 않고 모든 파일 내용을 재해시한다. metadata를 복원한 교체도 거부한다.
실제 로컬 측정은 전체 재해시 약 2.5초, metadata/inventory 검사 약 1.4초였으며 성능 인수 gate가 아니다.
runtime catalog 4 MiB/20,000 file limit과 local path/link 거부를 적용한다.
이 검증은 다른 사용자와 OS 권한을 격리하는 sandbox가 아니다.

Java를 직접 argv로 실행하여 batch/shell quoting을 사용하지 않는다. 승인된 launch.properties의
VM 설정을 읽고 heap 512 MiB, CPU/GC/JIT thread 제한과 headless mode를 적용한다.
Ghidra home/settings/cache/temp는 target의 Agent private directory 아래로 지정한다.
Java/decompiler 자식은 plugin의 Windows Job과 별도 내부 Job에 포함되며 내부 종료 뒤에도
native process-tree 종료 signal과 accounting을 확인한다. 상위 plugin Job crash는 자식까지 정리한다.
POSIX에서 내부 신규 process group이 상위 group cleanup을 벗어나는 문제가 있으므로 현재
Ghidra health는 Windows Job Object가 없는 환경에서 capability를 제공하지 않는다.
Linux에서의 상위 수명에 묶인 containment 구현과 실제 Ubuntu 검증은 후속 필수 작업이다.

Agent의 입력 snapshot/hash와 분리된 Ghidra database/analysis.gpr 및 analysis.rep를 사용한다.
open은 import/analysis를 실행하고 timeout이 발생한 분석을 READY로 반환하지 않는다.
query는 기존 project의 정확한 target을 -noanalysis/-readOnly로 처리한다.
rename/comment는 transaction을 적용하고 headless process의 저장/종료 후 결과를 반환한다.
native 시험에서 새 Java process의 후속 조회로 Unicode 변경의 실제 저장을 확인했다.
close는 volatile session을 닫고 database는 유지한다. durable catalog/reuse/retention/backup은
아직 구현되지 않았으며 보존된 파일만으로 기존 Handle을 부활시키지 않는다.

주소는 absolute hex 또는 program 이름과 module RVA다. 함수·문자열·xref·disassembly pagination은
backend cursor를 Agent의 owner/device/resource/query/revision cursor로 감싼다.
rename/comment 후 이전 cursor는 만료된다. list는 최대 500개, string 값은 8,192자까지와 truncated flag,
decompile은 131,072자, Java JSON 결과는 512 KiB다. stdout/stderr 합계는 8 MiB, 마지막 log tail은 64 KiB다.
오류·deadline·취소 후 종료가 불확실하면 generation을 종료하고 UNKNOWN 경로를 사용한다.
표준 입력 cancel의 active 처리 대신 supervisor의 취소 budget/Job 종료를 사용한다.

backend executable은 wheel에 포함하지 않는다. provenance는 Ghidra version, runtime/Java hash,
upstream LICENSE와 CycloneDX bom.json hash 및 licenses 경로를 기록한다.
Ghidra 본체 LICENSE는 Apache-2.0이며 bundled dependency는 별도 upstream license 목록을 사용한다.
전체 RACP 배포의 재배포 조건·SBOM·signed release gate는 별도 후속이다.

## 실제 증거

`tests/integration/test_ghidra_native.py`는 실제 Gateway/Agent HTTP와 별도 plugin/Java를 실행한다.
known-source DWARF 4/O0 PE fixture의 source/target SHA-256을 비교하고 자기 소유 임시 target만 사용한다.

1. open READY와 실제 database 파일 → info/functions/strings/xrefs/disassemble/decompile →
   pagination → 한글 function rename/comment 저장 → 이전 cursor 거부 → close/CLOSED와 database 유지.
2. 정상 open 후 query 중 plugin Job 종료 → Java가 상위 Job에 속함 및 native kernel handle 종료 확인 →
   operation UNKNOWN/Handle EXPIRED → Agent core 유지/filesystem.stat 성공.

실제 native 2개와 runtime/containment 승인 경계 unit 6개는 `dist/ghidra-native-results.xml`에 **8 passed**다.
별도 application 회귀와 함께 실행한 `dist/ghidra-contract-results.xml`은 **9 passed**다.
Agent wheel의 Java bridge/Python module 포함 및 source byte 일치도 확인했다.
일반 전체 검사에서 native opt-in 2개는 skip하며 PASS로 합산하지 않는다.

## 남은 gate

현재 증거는 Windows 10 x64/Ghidra 12.1.4/JDK 22.0.2/known-source PE fixture다.
다른 OS·architecture/format·큰 project, backend storage 재사용/GC/복구, durable event/ACK,
External MCP mapping, GDB attach/detach·TTL/revoke/cancel 확대, 실제 host, memory/8시간 soak,
설치/upgrade/backup/release gate는 남아 있다. 실제 static/debugger fixture 둘의 확인만으로
Phase 9 또는 v1 전체 완료로 표시하지 않는다.
