# ADR-0021: PC가 승인한 이름 있는 작업 폴더

2026-10-05 KST · 채택 · CORE-WORKSPACE-04

AI는 Device ID와 `workspace_id`를 명시하여 원격 자료를 사용한다. 기존 단일 폴더는
`default`이며 Agent 로컬 설정에서 최대 15개 추가 폴더를 `ID=PATH`로 승인한다.
등록 설정, Agent 명령 인자와 ServiceConfig가 같은 WorkspaceSpec을 사용한다.
Gateway 요청으로 허용 폴더를 추가하거나 바꿀 수 없다.

OperationInput/Request.context에 선택을 전달하고 Agent는 실행 전에 로컬 catalog를 확인한다.
상대 경로와 shell/process/terminal cwd는 선택한 폴더를 기준으로 해석한다.
filesystem copy/move의 목적지는 별도 `destination_workspace_id`로 선택한다.
다른 허용 폴더도 선택 없이 절대 경로나 `..`로 접근하면 거부한다.
동시 filesystem 호출은 ContextVar로 선택을 분리하고 mutation은 한 provider의 lock을 공유한다.
경로는 기존 PathGuard의 링크/reparse·root identity 확인을 사용하며 중첩 경계는 함께 pin한다.
등록한 root와 그 root를 포함한 상위 폴더를 삭제·이동하기 전에 거부한다.

일반 기본 폴더 요청의 canonical digest는 기존 형식을 유지한다. 추가 폴더와 복사·이동 목적지
선택은 digest에 포함되어 같은 idempotency key를 다른 폴더에 재사용하면 충돌한다.
승인 목록에 두 폴더 ID를 표시하고 Console의 승인 실행도 원래 선택을 유지한다.
지속 terminal/process Handle에는 시작한 workspace ID를 남긴다. 후속 Handle 조회·입력은
이미 시작한 세션의 cwd를 바꾸지 않는다.

파일 provider의 경계와 OS 계정의 실행 권한은 구분한다. 임의 shell/script는 OS 계정 권한으로
실행하므로 `workspace_id`는 shell의 filesystem sandbox가 아니다. 별도 격리 계정/OS sandbox는
현재 제공하지 않는다. 폴더별 profile도 제공하지 않으며 PC의 로컬 실행 profile을 적용한다.

실제 Windows 10 로컬 Gateway/Agent/MCP/CLI 및 별도 HTTPS 등록 Agent로 검증한다.
실제 두 PC, 실제 AI host, 다른 드라이브의 cross-volume move와 Ubuntu runtime 검증은 별도 gate다.
