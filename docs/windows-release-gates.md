# Windows 배포 인수 gate

2026-10-06 · 진행 중 · 개발정의서 v1.1 부록 E14–E16 기준

사용자의 Windows 우선 지시를 적용한다. Linux/macOS native 배포는 후순위이며, 아래
부분 검증을 전체 제품 인수 완료나 서명된 release로 간주하지 않는다. 원문 정의서는
수정하지 않는다. `dist/client-desktop-0.1.8-final/build-manifest.json`은 현재 배포 파일과
확인된 검증을 기록하며 pending 항목을 통과로 변경하지 않는다.

| 요구 ID | 필요한 증거 | 현재 판단 |
|---|---|---|
| AUTH-01 | enrollment 만료/재사용과 credential 종류 혼용 거부 | 기존 인증 테스트와 실제 등록 재시도 증거 있음. 최신 전체 gate 재검증 중 |
| AUTH-02 | issuer/audience/Origin/CSRF/객체 소유권 변조의 실행 차단 | 기존 unit/Console/Keycloak 증거 있음. 실제 AI host ingress와 최종 재검증 남음 |
| RPC-01 | mutation key 100개 동시 요청의 counter=1과 payload conflict | 기존 동시 호출 시험과 실제 두 PC counter 증거 있음. 전체 release gate 미확정 |
| RPC-02 | ACK/result 유실·Agent crash 후 중복 0과 UNKNOWN | 기존 fault fixture/Windows crash 증거 있음. 전체 release gate 미확정 |
| RPC-03 | protocol/epoch/schema/frame 제한 거부와 Agent 생존 | 기존 contract/integration 증거 있음. 전체 release gate 미확정 |
| AUTH-03 | revoke 후 접수 차단·lease 60초+cleanup 5초 내 소유 process 종료 | 기존 lease/revoke 시험 있음. 실제 환경의 최종 시각 측정 재검증 남음 |
| SHELL-01 | Unicode/공백 argv·두 출력 stream·nonzero exit·환경 삭제 | Windows 실제 원격 shell 증거 있음. 전체 조합과 최종 재검증 남음 |
| LIFE-01 | child/grandchild timeout/cancel 후 소유 process/port 누수 0 | 기존 lifecycle 시험과 실제 두 PC Job cleanup 증거 있음. 최종 전체 측정 남음 |
| POLICY-01 | deny 우선·기본 deny·승인 변조/만료/재사용·approval-disabled 거부 | 기존 policy/approval 시험 있음. 최종 release 재검증 남음 |
| FS-01 | junction/symlink·overwrite/precondition·중간 실패·Unicode/BOM 원형 보존 | 기존 filesystem 시험과 실제 두 PC hash 증거 있음. 전체 release gate 미확정 |
| PROC-01 | PID identity 변경 시 거부·wait timeout의 비종료 | 기존 process identity 시험 있음. 전체 release gate 미확정 |
| ART-01 | 100 MiB 중단/resume·최종 SHA·scope/hash 변조 거부 | 기존 Artifact fixture 및 실제 다운로드 증거 있음. 실제 AI host와 최종 release 재검증 남음 |
| ART-02 | quota/disk-full/GC 경합·READY 무손상·incomplete cleanup | 기존 fault fixture 있음. 전체 운영/복원/GC gate 남음 |
| PTY-01 | REPL/resize/cursor/ring gap/Unicode/재연결 | Windows ConPTY 및 실제 두 PC 시험 있음. 전체 release 재검증 남음 |
| STATE-01 | 재시작 복구와 늦은 취소/결과의 terminal state 불변 | 기존 restart/journal 시험 있음. 전체 migration/복원/rollback은 별도 미완료 |
| UI-01 | SSE gap refresh·UNKNOWN/승인 만료/취소 진행·escape 부작용 차단 | Console 및 Client E2E 증거 있음. 모든 사용자 흐름과 keyboard 인수 미완료 |
| BROWSER-01 | profile/page 격리·stale ref·dialog/download·evaluate cleanup | 실제 browser fixture 있음. 전체 release 재검증 남음 |
| DESK-01 | 100/125/150% DPI·음수 원점 2모니터·locked/UAC/RDP | 일부 native fixture 있음. 실제 display/session 조합 미완료 |
| DESK-02 | 잘못된 SID IPC 거부·foreground 변경 중단·modifier 해제 | 일부 native Broker/Guardian 시험 있음. 다른 SID/SCM/전체 session gate 미완료 |
| RE-01 | 실제 static/debugger 각각 하나와 plugin crash 격리 | Ghidra/GDB 증거 있음. 전체 native 범위·license·containment gate 남음 |
| REL-01 | clean install·credential 보존 upgrade·실패 rollback·백업 복원/hash | 별도 Windows installer fixture의 upgrade/uninstall/보존 증거 있음. clean VM/rollback/복원은 미완료 |
| PERF-01 | 정의서 §96 부하/soak와 RSS/queue/Artifact 제한 | 전체 요구 부하·8시간 soak 미완료 |
| HOST-01 | 지원을 선언할 Codex/ChatGPT 경로의 인증·도구·Job polling·screenshot preview | 현재 Codex 앱→실제 141의 OAuth MCP Device/파일/명령/Job polling·cancel/ConPTY/browser inline PNG와 Windows desktop session/window/monitor/full screenshot 통과. 실제 desktop input, ChatGPT 경로와 public/운영 provider는 미검증. 전체 gate PASS로 확대하지 않음 |

E14의 별도 배포 조건도 유지한다. frozen lockfile과 SHA-256, browser 포함 Windows
EXE/ZIP은 생성했다. 전체 dependency license 목록/SBOM/provenance는 미완료다. 현재
코드 서명 키가 없으므로 배포 파일은 unsigned 개발 빌드로 명시한다. 일일 backup의
RPO 24시간·clean environment의 RTO 30분, 일관된 DB/Artifact/config/policy/secret 복원은
실제 측정과 hash 증거가 필요하며 아직 통과로 기록하지 않는다.

0.1.6 이후의 설정 백업은 보호된 Device 설정의 변경 전 백업이다. Gateway 전체 운영 backup,
DB migration rollback 또는 clean environment 복원 완료의 근거로 확장하지 않는다.

0.1.8은 현재 Windows 로그인 세션의 화면·입력 허용 설정과 일반 프로세스 메모리 관측을 추가한다.
자체 창/프로세스, MCP Artifact 회수, Electron HTTPS/WSS와 패키지 bridge를 확인했다.
실제 141은 새 memory capability를 보고하며 Gateway 갱신 뒤 같은 boot ID로 epoch 6에 재접속했고,
새 native Codex 시작에서 86개 RACP 도구와 두 memory MCP 도구 노출을 확인했다. 화면 허용 설정 후
Agent를 다시 시작한 epoch 7에서는 desktop이 enabled/healthy이며 실제 session/window/monitor와
3840x2160 화면 캡처까지 통과했다. desktop input은 호스트 안전 검토가 실행 전 차단해 미검증이고,
실제 remote memory-read 실행도 미검증이므로 해당 두 항목은 여전히 release PASS가 아니다.
[상세 검증 범위](remote-pc-windows-0.1.8.md).
