# ADR-0004 — persistent terminal과 resource inventory

상태: 구현 진행 · 2026-10-02

terminal은 shell.exec와 별도 provider다. stdout/stderr가 합쳐진 raw terminal byte stream을
보관한다. Windows는 직접 ConPTY API를 사용하며 child를 CREATE_SUSPENDED로 생성하고
kill-on-close Job Object에 배정한 후 실행한다. POSIX는 controlling PTY와 process group을
사용한다. POSIX process group은 독립 session으로 빠져나간 프로세스의 containment를
보장하지 않으며 실제 Ubuntu fixture는 아직 미검증이다.

Windows 부모의 stdio가 redirect되어 있을 때 STARTF_USESTDHANDLES와 null std handle을
명시해야 자식이 ConPTY를 사용한다. 실제 회귀 시험에서 이 문제를 재현하고 수정했다.
참고: [Microsoft Terminal 논의](https://github.com/microsoft/terminal/discussions/15814).
ClosePseudoConsole은 별도 thread에서 호출하면서 output을 계속 drain한다.
참고: [Microsoft ConPTY 수명](https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session).

기본 ring은 4 MiB, 동시 session은 8개다. consumer별 byte cursor를 받아 읽기 때문에
다른 consumer의 데이터는 제거하지 않는다. eviction은 CURSOR_EXPIRED와 earliest/lost
byte 수로 반환하며 자동 skip하지 않는다. UTF-8의 미완성 trailing bytes는 다음 읽기에
남기고 invalid replacement 수를 별도로 보고한다. 64 KiB 이하 명시적 base64 입력은
임의 bytes를 허용하며 입력의 accepted_bytes를 기록한다. 완료된 입력은 exact=true이며
중단·OS 오류의 수치는 확인한 하한으로 표시한다(accepted_bytes_exact=false).
입력은 journal replay로만
중복 방지하며 새로운 key로 불확정 입력을 자동 재시도하지 않는다.

poll은 8시간 idle TTL을 연장하지 않는다. write/resize/keepalive만 연장한다. lease 만료와
Agent 종료에서 owned tree를 정리한다. close는 반복 호출해도 저장한 결과를 반환한다.
Gateway에 Handle 소유권/boot/provider/revision/availability를 저장하고 reconcile 및
heartbeat inventory로 갱신한다. 오래된 revision이 CLOSED를 ACTIVE로 되돌리지 않는다.
Gateway만 재시작하면 같은 boot의 live Handle을 복구한다. Agent boot가 바뀌면 이전
volatile Handle을 EXPIRED로 표시하며 과거 open 결과의 journal replay는 다시 실행하지
않는다. disconnected Handle의 lifecycle과 availability는 별도로 표시한다.

현재 API는 bounded terminal_read long poll과 WS stream을 제공한다. 후속 구현과 검증은
[ADR-0005](ADR-0005-terminal-stream-credit.md)에 기록한다. capability revision event와
Console 진단 UI는 후속 구현이다. 이 ADR과
로컬 테스트를 Phase 4 전체 gate 또는 MVP 완료로 해석하지 않는다.
