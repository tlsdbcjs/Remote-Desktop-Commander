# 원격 PC의 여러 작업 폴더 사용

2026-10-05 KST · 개발 Agent

PC에서 기존 폴더를 직접 선택한다. 기본 폴더의 이름은 `default`다.
이름은 영문자로 시작하는 영문/숫자/밑줄/하이픈 1–32자이며 추가 폴더는 최대 15개다.
설정은 절대 로컬 경로를 저장한다. 명령 인자의 상대 경로는 실행한 PC의 cwd에서 절대 경로로
바꾸며 UNC, symlink와 Windows reparse 경로는 거부한다.

```powershell
uv run racp-connect --gateway https://gateway.example:8765 --workspace E:\Projects --allow-workspace docs=D:\Documents --allow-workspace data=E:\Datasets
```

토큰은 숨김 입력창에 붙여 넣는다. 추가 폴더 목록도 보호된 등록 설정에 저장하므로
`uv run racp-agent`로 재시작할 때 다시 지정할 필요가 없다. 기존 Agent를 이번 실행에서 다른
추가 목록으로 실행하려면 `--allow-workspace ID=PATH`를 반복한다. 지정한 추가 목록 전체가
저장한 추가 목록을 대신한다. ServiceConfig JSON은 `allowed_workspaces`에
`{"id":"docs","path":"D:\\Documents"}` 같은 항목을 넣는다.

AI는 `device_list`의 filesystem capability `attributes.workspaces`에서 ID와 경로를 확인한다.
파일·명령 도구에 `workspace_id`를 전달한다. 생략하면 `default`를 사용한다.

```json
{"device_id":"dev_...","workspace_id":"docs","path":"보고서.txt"}
```

MCP `fs_read`는 위 인자를 사용한다. 파일 저장 예시는 다음과 같다.
실행 profile은 Gateway와 Agent 양쪽 정책에 따라 선택하며 기본은 `read_only`다.

```json
{"device_id":"dev_...","workspace_id":"docs","path":"memo.txt","content":"새 내용\n","execution_profile_id":"standard","idempotency_key":"save-memo-1"}
```

`fs_copy`/`fs_move`는 source의 workspace와 목적지를 각각 지정한다.

```json
{"device_id":"dev_...","workspace_id":"docs","source":"memo.txt","destination":"memo-copy.txt","destination_workspace_id":"default","execution_profile_id":"standard","idempotency_key":"copy-memo-1"}
```

디렉터리 복사는 `recursive: true`가 필요하다. 서로 다른 볼륨의 이동은
`copy_and_delete: true`와 `execution_mode: "job"`를 명시한다. 이번 실제 시험은 같은 볼륨이었다.
부분 실패 보고를 확인하고 실패한 mutation을 새 key로 자동 재실행하지 않는다.

CLI도 동일한 선택을 사용한다.

```powershell
uv run racp fs read dev_... 보고서.txt --workspace-id docs
uv run racp fs copy dev_... memo.txt memo-copy.txt --workspace-id docs --destination-workspace-id default --profile standard --key copy-memo-1
```

Console의 장비 화면은 모든 허용 폴더를 표시하며 명령 실행의 작업 폴더를 선택할 수 있다.
shell/process/terminal의 상대 cwd는 선택한 폴더를 기준으로 한다. terminal/process Handle은
시작한 폴더를 유지하므로 후속 조회·입력 때 다른 workspace를 보내도 실행 디렉터리가 바뀌지 않는다.

파일 도구는 선택한 폴더 밖의 경로를 거부한다. 임의 명령이나 script는 해당 PC의 OS 계정 권한으로
실행되므로 폴더 선택이 shell의 접근 권한을 격리하지는 않는다. PC의 계정·profile·승인 정책에 맞게
실행 권한을 부여한다. Gateway/AI에서 PC의 허용 폴더 목록을 수정하는 API는 제공하지 않는다.
