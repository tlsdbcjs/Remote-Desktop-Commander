# ADR-0003: 재개 가능한 Artifact 전송과 binary write

상태: 채택 · 2026-10-02

부록 E9의 transfer API를 구현한다. 업로드는 선언한 total size를 quota에 먼저 예약하고,
4 MiB 이하 chunk의 offset/Content-Range/SHA-256을 검증한다. 같은 offset/hash 재전송은
같은 접수 결과를 반환한다. fsync 뒤 committed offset을 SQLite에 commit하며 재시작 시
DB에 기록되지 않은 파일 tail을 제거한다. complete는 전체 size/hash 검증 후 READY를
게시한다. hash 오류와 disk-full은 기존 READY blob을 변경하지 않는다.

transfer credential은 256-bit 난수이며 digest만 저장한다. transfer/device/direction/size에
결합하고 기본 TTL은 10분이다. owner 또는 할당된 Device identity만 같은 scope를 재인가할
수 있다. credential과 Token은 URL/일반 로그/기본 CLI 출력에 넣지 않는다.

Agent download는 실행 중인 filesystem.write의 artifact_id에 결합한다. Gateway는
인증된 owner의 Artifact 소유권을 dispatch 전에 확인한다. Agent는 다운로드한 전체
SHA-256을 확인한 뒤 workspace writer에 내부 cache 경로를 전달한다. 원격 payload로
cache 경로를 지정할 수 없다. binary write는 content와 artifact_id 중 하나만 받는다.

download는 ETag와 단일 byte Range를 제공한다. active reader와 유효 download transfer,
진행 중 operation이 참조한 Artifact는 GC에서 pin한다. 기본 retention은 7일이며 미완료
upload는 1시간 뒤 정리한다. quota 기본값은 Artifact 1 GiB, 전체 10 GiB다.

범위 응답의 HTTP 근거는 [RFC 9110 Range Requests](https://www.rfc-editor.org/rfc/rfc9110.html#name-range-requests)다.
직접 binary JSON/base64 경로를 새로 만들지 않는다.
