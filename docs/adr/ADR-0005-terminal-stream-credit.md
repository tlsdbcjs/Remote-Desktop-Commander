# ADR-0005 — terminal stream 소비 ACK와 control 전송

상태: 구현 진행 · 2026-10-02

Agent PTY ring을 원본 byte history로 사용한다. owner가 인증된 Gateway WebSocket에
subscribe하면 Gateway가 owner/device/handle/boot/epoch를 묶은 내부 subscription을
전달한다. ID는 권한 증명이 아니다. query token은 거부하고 HTTP와 같은 owner bearer,
Host, Origin 경계를 적용한다. Device credential로 owner stream을 열 수 없다.

각 소비자는 독립 cursor를 가진다. stream_data는 원본 byte_offset/next_cursor와 UTF-8
text를 제공한다. max chunk는 원본 64 KiB, 미확인 구간은 최대 256 KiB 및 4개 frame이다.
invalid byte replacement로 text 표현의 크기가 늘어날 수 있으므로 원본 offset으로
credit를 계산하고 기존 JSON message/string 상한도 적용한다.

stream_ack는 소비 완료한 chunk 경계까지의 누적 offset이다. Gateway는 실제 전송을
시작한 해당 stream의 경계만 ACK하며 Agent도 pending 경계를 재검사한다. 과거 ACK는
추가 side effect 없이 무시하고 미래/중간/다른 stream ACK는 해당 owner stream을
거절한다. ACK 없이는 ring 수집만 계속되고 새 전송은 멈춘다. ACK 이후 cursor가
evict되었으면 stream_gap과 CURSOR_EXPIRED 종료를 보낸다. 새 위치 선택은 명시적
재접속으로 한다. 이미 전송한 데이터를 조용히 버리거나 자동 입력 재실행하지 않는다.

Gateway relay는 frame 8개로 제한하고 종료 통지를 위한 slot을 예약한다. Agent/Gateway
core WS에는 control 128개와 data 8개의 별도 queue, 단일 transport writer를 둔다.
control을 먼저 처리한다. 현재 전송 중인 하나의 frame은 중간에 끊지 않고 send를 최대
5초로 제한한다. control queue full은 RESOURCE_EXHAUSTED와 retry_after_ms를 보고한다.
data 전송은 byte credit와 bounded queue의 소비를 기다린다.
Device별 stream은 16개, Gateway 전체는 64개로 제한한다.

구독은 현재 연결 epoch의 휘발성 자원이다. owner disconnect/unsubscribe와 Agent 연결
종료에서 구독을 정리한다. terminal Handle은 짧은 연결 단절에 유지되며 runtime lease와
Handle TTL에 따라 정리된다. 재접속은 소비자가 저장한 cursor와 새 owner 인증으로 한다.
Gateway restart 이후 살아 있는 Handle에 새로운 stream을 연결한다.

Python SDK는 async generator의 다음 item 요청을 직전 data 소비 완료로 보고 ACK한다.
CLI는 NDJSON을 flush한 뒤 ACK하며 종료/실패에서 cursor 또는 gap 정보를 제공한다.
Console cookie/CSRF 인증과 SSE는 Phase 6에서 추가한다. Windows 10 실연결 검증은
reference OS/실제 host/8시간 soak 전체 gate를 대신하지 않는다.
