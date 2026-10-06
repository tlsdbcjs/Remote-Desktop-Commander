# RACP 개발 계획서 검토 결과

검토일은 2026년 10월 1일이다. 원문의 기능 목표와 아키텍처를 유지하면서 구현자가 다르게 해석할 수 있는 실행·복구·권한 계약을 상세화했다. 반영된 전체 문서는 [RACP 개발 정의서 v1.1](RACP-개발정의서-v1.1.md)이다.

원문은 기능 범위와 기술 방향이 충분히 정리되어 있지만, 원격 실행 실패 시 동작과 실제 합격 기준이 부족해 그대로 `Implementation Ready`로 보기는 어렵다. 개정본의 상태는 **설계 기준선 수립, Phase 0 검증 후 구현 기준선 확정**으로 조정했다. 현재 프로젝트 폴더에는 검토 대상 구현 코드가 없었으므로 이번 검토는 제공된 정의서와 공식 기술 문서를 기준으로 했다.

## 핵심 변경 사항

| 우선순위 | 원문 위치와 문제 | 반영한 개선 | 개정 위치 |
|---|---|---|---|
| P0 | §55가 destructive operation을 중복 방지 대상에서 제외 | 모든 mutation에 key/journal 적용, 결과 불명확 시 UNKNOWN, 자동 재실행 금지 | §16, §54–55, E4 |
| P0 | §52 coroutine 취소와 OS 프로세스 종료 구분 부족 | CANCEL_REQUESTED와 실제 종료 분리, process tree cleanup·경합 규칙 | §52–53, §114, E5 |
| P0 | §37 Device 인증만 있고 owner/MCP/Console 인증 계약 부족 | identity 분리, owner bootstrap, 객체 소유권, OAuth 적용 경계, credential rotation/revoke | §35–38, E7 |
| P0 | §106 approval disabled의 결과와 승인 재사용 조건 미정 | payload·대상 revision 결합, 1회 소비, 만료, disabled일 때 require-approval 거부 | §106 |
| P0 | §91에서 인증·revoke를 뒤늦게 구현할 가능성 | Phase 1–2로 기본 보호 장치 이동, 마지막 단계는 종합 검증 | §80–81, §91, §121 |
| P0 | §29·36·73 Windows Service/사용자 실행 권한이 추상적 | 비관리자 service identity, GUI Broker 사용자 SID, IPC ACL·세션 선택·UIPI 한계 | E7, E10 |
| P1 | §2·15·78·88·95 RE 필수 범위 상충 | MVP와 v1 분리, static/debugger 각 1개 실연동 유지, 개별 IDA/x64dbg/Frida 선택화 | §2, §78, §89, E16 |
| P1 | §16–19 timeout·message 대소문자·재연결 식별자 부족 | 소문자 wire, operation/request 분리, boot/epoch, ACK 의미, bounded schema | §16–19, E3 |
| P1 | §7·54·114 Device/Job 상태 불일치 | 단일 상태 정의, RECONCILING/TIMED_OUT/UNKNOWN와 terminal 규칙 | §7, §54, §114 |
| P1 | §10 tool 목록에 append/wait/tree/page/move 등 누락 | 필수 기능과 tool 매핑 및 operation registry, API/error/pagination 계약 | E2 |
| P1 | §20·65 shell=True 금지와 command string 예시 충돌 | 기본 argv, shell mode 명시, 환경/cwd/encoding/output 제한 | §20, §101, 부록 B |
| P1 | §21·56 ring buffer의 cursor/유실 의미 부족 | byte cursor, 다중 소비자, CURSOR_EXPIRED, partial write, flow control | E6 |
| P1 | §22 경로·atomic write의 구체 보장 범위 부족 | junction/UNC/ADS, precondition, copy/move/delete 부분 실패, ACL·BOM 보존 | E8 |
| P1 | §25·84 Artifact 전송 API·resume·quota·GC 미정 | scoped transfer, chunk/hash 검증, READY 게시, retention·GC 경합 | E9 |
| P1 | §11·25·103 Artifact ID만으로 AI가 화면을 관측할 수 있는지 미정 | 원본 Artifact와 MCP bounded image preview, 좌표 변환 metadata | E9–E10 |
| P1 | §26–30·112 오래된 DOM/화면 기반 입력 위험 | page/observation revision, desktop lease, 다중 DPI, foreground 변경 검사 | E10–E11 |
| P1 | §108·110 plugin schema 변경과 실패 처리 부족 | framing/limits/health/restart, 외부 MCP mapping 고정, opaque 명령 권한 | E12 |
| P1 | §66·72–76 설치 후 upgrade/restore 기준 부족 | frozen build, browser 포함 경로, DB 동반 rollback, backup/restore, uninstall | E14 |
| P1 | §96 성능 수치에 환경·백분위·측정 경계 부재 | p95 목표, reference load, 30분 부하·8시간 soak와 누수 조건 | §96–97 |
| P1 | §59·94·95 정상 시나리오 중심 | 장애·권한·중복·실제 OS 검증을 23개 검증 ID와 Phase에 연결 | E15 |
| P2 | §4·68·71 버전 하한/개발 명령이 재현 가능한 build와 불일치 | patch/runtime 고정 gate, workspace build, OS extras, frozen install | §4, §68–71 |
| P2 | §75 PostgreSQL 전환만으로 scale 가능해 보임 | v1 single process/worker, 이후 WS routing/lock/storage 추가 요구 명시 | §75, E1 |
| P2 | §79 이후 단계별 산출물·책임·일정 근거 부족 | M0–M5, 역할과 decision register, M0 추정·M1 재예측 | E16–E17 |

P0는 첫 원격 명령 실행 전에 설계·구현되어야 하는 항목, P1은 해당 기능의 완료 조건, P2는 재현성·운영·관리 개선을 의미한다.

## 범위와 기본값의 변경

RE 범위는 원문의 §88·95에 있던 실제 static/debugger 각 1개 기준을 v1 전체의 기준으로 선택했다. §78에서 IDA/x64dbg/Frida를 각각 MUST로 둔 요구는 선택 adapter로 정리했다. Windows에서 IDA와 x64dbg 모두를 반드시 제공해야 한다면 그 두 adapter를 추가 필수 gate로 지정하면 된다.

새 참조 환경은 Windows 11 x64와 Ubuntu 24.04 LTS x64다. macOS·ARM64·다른 배포판은 검증 전 지원 선언을 보류한다. MVP는 Phase 0–5 및 인증된 CLI이고, Console/Browser/Windows Desktop/RE/패키징까지 끝나야 v1 완료다.

inline text 기본 상한은 1 MiB에서 64 KiB로 조정하고 큰 결과는 Artifact로 넘긴다. screenshot 목표는 정의가 없던 typical 500ms에서 1920×1080 PNG 저장까지 포함한 p95 800ms의 **제안 기준**으로 바꿨다. 실제 성능 개선이 측정되었다는 뜻은 아니다.

초기 정책은 read-only이고 trusted_personal은 명시적 선택이다. 네트워크 단절 후 managed 실행을 유지하는 기본 상한은 execution lease 60초다. 긴 오프라인 작업이 실제 필수라면 revoke 지연과 복구 정책을 함께 검토해 별도 profile로 정의해야 한다.

## 공식 자료 확인

MCP Python SDK v2 stable이라는 원문 주장은 확인되었다. 다만 SDK patch 설치와 실제 Codex/ChatGPT 호환은 별도 검증 대상이므로 고정 버전·host compatibility gate를 추가했다. [공식 Python SDK 문서](https://py.sdk.modelcontextprotocol.io/)

2026-07-28 MCP Streamable HTTP는 POST 및 요청별 SSE를 사용하며 기존 GET stream과 protocol session이 제거되었다. 따라서 과거 MCP 구현 방식을 자동 전제하지 않도록 최신/구버전 호환을 adapter에 한정했다. [MCP Streamable HTTP 명세](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)

서비스 GUI 분리와 입력 권한 제한도 공식 설명을 기준으로 보완했다. [Windows Interactive Services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services), [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)

## 착수 전에 확인할 실제 환경

이 항목들은 계획서 부록 E17에 기본안·담당 역할·확정 시점을 기록했다.

- 사용할 Windows/Linux 버전, Python/Node/SDK patch와 패키징 호환성.
- Codex/ChatGPT의 실제 연결 방식과 OAuth provider.
- Windows 서비스 실행 계정과 GUI 테스트용 interactive VM.
- 사용할 RE backend, 라이선스와 재배포 가능 범위.
- Artifact 보관량, backup 위치, 설치 서명 여부.
- 개발 인원과 Phase별 기간 추정. 환경이 없는 상태에서 완료일을 임의 확정하지 않는다.

## 검토 범위와 검증 수준

제공된 원문 전체를 읽고 원문의 번호가 붙은 본문 128개 절과 부록 A–D를 유지했다. 충돌하는 요구와 주요 예시는 본문에서 수정하고, 부록 E에 17개 상세 계약을 추가했다. 원본 첨부파일은 수정하지 않았다.

개정본의 128개 절 번호, 17개 부록 상세 절, 코드 블록 구분, 20개 JSON 예시 구문, 23개 검증 ID의 유일성, 로컬 문서 링크 검사를 통과했다. 원본 SHA-256을 대조해 첨부파일이 변경되지 않았음을 확인했다. 실제 RACP 구현·빌드·설치·성능 테스트는 이번 요청의 범위가 아니며 수행하지 않았다. 문서에 나온 테스트 ID와 수치는 향후 구현의 검증 기준이다.
