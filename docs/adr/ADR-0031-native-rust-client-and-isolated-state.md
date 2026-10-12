# ADR-0031: Rust native Client와 독립 상태·선택 runtime

> **Document ID**: `DOC-ADR-0031`\
> **Status**: Accepted for implementation · native acceptance deferred · **Target Version**: v0.1.23\
> **Last Updated**: 2026-10-11 · **Classification**: Architecture Decision Record

## 배경

사용자는 Client 영역 전체 Rust 전환, 이전 Client 실행 경로의 제거, 테스트 전 단계 구현을 요청했다. React 화면 재사용을 선택했던 [ADR-0030](ADR-0030-rust-tauri-client-and-native-agent.md)은 이 요구와 맞지 않는다. 이후 사용자가 핵심 원격 제어·승인·작업 추적을 우선하고 OS별 전용 API 확장은 후순위로 정했다.

## 결정

GUI는 `apps/client-native`의 Iced/Rust로 구현하고 기본 renderer는 tiny-skia, GPU renderer는 별도 feature로 둔다. tray·single instance·Windows 오류 창은 native OS API를 사용한다. Client 빌드와 개발 staging은 Rust xtask로 전환한다. Tauri/React·Node Client packaging source를 active tree에서 제거하고, 서버의 Python Gateway/CLI/SDK와 React Console은 유지한다.

새 user/portable 상태는 이전 설정·credential과 분리한다. 이전 상태 탐색·자동 import를 새 시작 경로에서 호출하지 않고 새 등록을 명시적으로 진행한다. 기존 설치·등록·Device·credential·사용자 데이터와 과거 dist는 보존한다. 정책·설정 revision CAS, 별도 DPAPI secret, private directory와 인증된 Named Pipe를 사용한다. GUI가 remote RPC payload나 credential을 읽어 이벤트로 전달하는 경로를 만들지 않는다.

Chromium·CDB·GDB·native 분석 host는 선택 bundle이다. 기본 Client payload와 독립된 manifest hash/size/OS/arch admission을 거쳐 선택하며 실행 수명 동안 파일을 pin한다. 프로젝트 Java bridge와 Ghidra 실행 코드는 독립 `apps/analysis-host` workspace에만 둔다. 로컬 hash 동의는 공식 서명 trust catalog의 검증을 뜻하지 않는다.

원격 터미널은 핵심 실행 수단으로 유지한다. 작업 단위 계약은 요청별 권한·승인·deadline·취소·재시도·결과 추적을 제공하는 경계로 사용한다. 서비스·레지스트리·스케줄러 등 모든 OS 기능을 미리 객체화하지 않는다. 전용 API는 대상별 권한과 구조화된 결과가 필요한 기능부터 후속으로 추가한다. `shell.exec` 또는 터미널에서 허용한 interpreter 내부 명령을 개별 OS 객체 권한으로 통제할 수 있다고 주장하지 않는다.

## 구현과 최종 인수의 구분

> [!IMPORTANT]
> 이 결정은 구현 source의 방향을 채택한 기록이다. Iced의 한글/IME·키보드·Narrator·DPI·다중 모니터·software/GPU 적합성은 실기 gate를 통과하지 않았다. gate 실패 시 Rust GUI 선택을 재검토한다. service scope, native maintenance/update, 잔여 Rust TCP/legacy state 실행 경로 제거와 최종 배포/제거 인수도 남아 있다.

자동 테스트 추가·실행, GUI smoke, native acceptance, 실제 등록/OS mutation·installer/portable product artifact 생성은 현재 사용자 실행 정책에 따라 보류한다. Windows x64 production 컴파일·정적 계약/consumer/manifest 검사와 개발 staging을 진행하며 macOS/Linux native 빌드는 명시 요청 이후로 둔다. 과거 제품 성공을 새 제품의 동등성 증거로 사용하지 않는다.

ADR-0030은 v0.1.21의 구현·배포 이력으로 보존하며, 새 Client source의 GUI·상태·runtime·build 결정은 이 ADR이 대체한다. 진행 수치와 실제 실행 증거는 현황 SSOT에만 기록한다.

## 관련 문서

- [전체 Rust 전환 계획](../spec/rust-client-migration-plan.md)
- [Native Client 운용 가이드](../guides/desktop-client-guide.md)
- [구현 현황 SSOT](../quality/implementation-status.md)
- [ADR 목록](README.md)
