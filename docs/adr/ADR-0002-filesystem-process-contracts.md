# ADR-0002: 파일 경로 고정과 managed process 소유권

상태: 채택 · 2026-10-01

Phase 3은 canonical filesystem/process operation을 같은 journal과 policy 경로에 추가한다.
read_only는 파일 조회·프로세스 조회/대기만 허용한다. 모든 mutation은 기존 key 계약을
사용하고 standard는 owner 일회 승인을 요구한다.

파일 API는 설정한 workspace에 한정된다. reparse/symlink/UNC/ADS/device namespace는
기본 거부한다. Windows는 ancestor 디렉터리를 OPEN_REPARSE_POINT로 열어 검사하고
FILE_SHARE_DELETE를 허용하지 않아 검사 중 디렉터리 교체를 막는다. POSIX는 root부터
O_NOFOLLOW dirfd를 사용한다. 모든 경로 작업은 열린 parent를 기준으로 수행한다.
같은 RACP provider의 writer는 serialize한다. 외부 writer와 원자적인 compare-and-swap을
보장하지 않으며 출력에 그 한계를 표시한다. shell 권한을 OS sandbox로 제한한다는 뜻이 아니다.

replace는 같은 디렉터리 임시 파일 flush/fsync → precondition 재확인 → replace 순서다.
Windows DACL과 POSIX mode를 보존한다. cancellation을 Python thread 취소로 가장하지 않는다.
worker에 취소/monotonic deadline을 전달하고 정리 확인까지 기다린다. 이미 commit한
mutation은 성공을 유지한다.

process.spawn은 Gate/Job Object 또는 POSIX process group에 소유권을 연결한다.
process Handle은 owner/boot/provider identity와 TTL을 가진다. 외부 PID 종료는 관측한
(pid, create_time, agent_boot_id)를 요구하고 자기 Agent/부모/system process는 보호한다.
wait timeout은 target을 kill하지 않는다. lease 만료/Agent 종료는 소유 tree만 정리한다.
현재 spawn의 stdout/stderr는 명시적인 discard 모드다. persistent PTY는 Phase 4에서
구현하며 spawn을 terminal로 표시하지 않는다.

API 근거: [Win32 CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
[Python dirfd 파일 API](https://docs.python.org/3.12/library/os.html#files-and-directories).
속성 조회 전용 핸들로는 실제 rename 차단이 되지 않아 GENERIC_READ로 열도록 검증 후 수정했다.
