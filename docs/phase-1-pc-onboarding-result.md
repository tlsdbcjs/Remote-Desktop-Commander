# PC 등록·저장 설정·재연결 결과

2026-10-05 KST · CORE-ONBOARD-03 기반 · 전체 목표 진행 중

`racp-connect`는 경로와 TLS/저장 크기를 검증하고 토큰을 숨김 입력 또는 stdin으로 받는다.
초기 read_only profile과 Gateway/Device/workspace/data/CA 설정을 같은 보호된 credential 파일에
원자적으로 저장한다. 기존 파일 덮어쓰기와 중복 setup은 거부한다. 토큰은 저장하거나 출력하지 않는다.
이후 `racp-agent`는 설정을 읽어 시작하며 기존 --credentials/--workspace 실행도 유지한다.

Console에는 owner cookie/CSRF 기반 토큰 발급 화면을 추가했다. 등록 토큰은 명령 인자나 storage에
넣지 않고 화면의 memory에서만 사용한다. 닫기/만료/로그아웃 시 제거한다. 실제 허용 폴더/profile과
개별 capability를 표시하고, 선택 기능을 사용할 수 없어도 정상 파일·명령 경로를 유지한다.
관련 결정은 [ADR-0019](adr/ADR-0019-protected-one-command-pc-enrollment.md)에 있다.

## 실제 검증

별도 실제 TLS Gateway CLI와 새 Agent command process를 임시 CA로 실행했다.
등록 전용 토큰 발급 → 기본 읽기 전용 PC의 WSS 연결 → MCP의 Unicode 파일 읽기와 변경 거부 →
저장한 설정으로 다른 cwd에서 재시작 → 같은 Device ID/new boot/new epoch → 명시적 profile 선택 후
MCP 파일 저장·Python 명령 실행을 확인했다. 토큰 재사용은 거부하고 stdout/log에 secret이 없는지 검사했다.

`tests/unit/test_agent_connect.py` 9개는 설정 identity/profile, 기존 파일 보존, 중단/중복 setup,
invalid workspace/TLS의 토큰 소비 부재, 저장 실패 UNKNOWN/no retry, 동시에 생성한 credential의
덮어쓰기 부재, byte/type/DPAPI 오류와 실제 Windows junction 거부를 확인한다.
저장 설정 schema와 OAuth schema도 drift 검사에 포함했다.

등록/설정/보존 회귀의 별도 묶음은 **14 passed**, `dist/pc-onboarding-results.xml`이다.
실제 Chromium Console은 기존 흐름과 신규 2개를 포함한 **13 passed**다.
토큰의 single-use·storage 비사용·닫기/로그아웃 제거와 클라이언트 시간 주입으로 만료 표시를 검사했다.
이 시간 주입은 실제 10분 대기 또는 서버 토큰 만료 시험을 대신하지 않는다.
Native browser runtime이 없는 실제 재시작 Agent의 명령 실행도 확인했다.
화면 산출물은 `dist/console-connect.png`다.
최종 전체 기준선은 **272 passed, 17 skipped**다. frozen sync/format/lint와 Windows/Linux 타입 target
123개 source file을 확인했다. 8개 개발 wheel의 SHA-256, racp-connect entry point와 최종 Agent/SDK source
포함도 검사했다. 이 빌드는 서명된 배포 installer가 아니다.

## 재연결에서 발견한 수명 문제

Console 결과 만료 뒤 Agent를 재시작하는 흐름에서 삭제된 non-retained read의 result replay가
OPERATION_NOT_FOUND를 일으켜 연결을 반복해서 끊는 문제를 발견했다.
읽기 삭제와 같은 transaction에 안전한 correlation 감사 기록을 남기고, reconciliation 중
정확한 operation/Device/request/trace와 현재 boot/epoch가 일치할 때만 버리도록 수정했다.
삭제된 결과를 되살리거나 실행하지 않으며 위조된 correlation과 일반 result는 계속 거부한다.
실제 Agent 재시작 후 기존 404 유지·새 읽기 성공과 위조 거부를 검사했다.
[ADR-0020](adr/ADR-0020-retired-transient-read-reconciliation.md).

첫 native 파일 시험의 실패는 Windows fixture의 CRLF 차이였고, 명시적 UTF-8 byte fixture로 수정했다.
새 Console 시험은 기존 결과 만료와 재시작 경계를 드러냈다. 이 문제를 제품에서 수정한 뒤,
중복 locator/Windows short-path 비교도 실제 canonical 경로와 명확한 region으로 보완했다.

## 범위

개발본이 설치된 같은 물리 Windows 10 PC의 분리된 process 검증이다. signed installer, clean PC,
Windows 11/Ubuntu 실제 실행, 서비스 계정/자동 시작, 실제 두 PC/AI host 연결은 계속 미검증이다.
여러 허용 폴더와 백그라운드 수명/업데이트는 후속 우선순위다.
[설정 안내](pc-connect-guide.md), [전체 작업 현황](implementation-status.md)에 남은 범위를 유지한다.
