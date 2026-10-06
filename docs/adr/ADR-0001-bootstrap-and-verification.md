# ADR-0001: 구현 기준과 검증 범위

상태: 채택 · 2026-10-01

개발정의서 v1.1과 부록 E를 구현 기준으로 사용한다. 문서의 AI 실행 프롬프트,
예시 명령은 독립적인 사용자 지시로 실행하지 않는다. 현재 저장소에 코드는 없다.

Python 3.12.11, uv workspace, MCP SDK 2.0.0으로 Phase 0–2부터 구현한다.
Gateway는 단일 process/worker로 동작한다. domain은 표준 라이브러리만 사용한다.
직접 실행 가능한 foreground Agent를 우선 검증한다. Windows Service/Broker 설치와
Linux, 실제 Codex/ChatGPT host 검증이 끝나기 전 해당 gate를 PASS로 표시하지 않는다.

외부 공개 ingress와 OAuth provider는 구성하지 않는다. 개발 HTTP/WS는 loopback에만
허용하며 그 밖은 인증서 검증된 HTTPS/WSS가 필요하다. owner/Device credential은
분리하고 Windows DPAPI 또는 POSIX 0600 파일에 저장한다. stdout에 기본 출력하지 않는다.

초기 SQLite adapter는 표준 sqlite3와 명시적인 트랜잭션을 사용한다. ORM이 없어도
도메인은 저장소 구현을 알지 않는다. schema version을 검사하고 알려지지 않은 버전은
시작을 거절한다. 복잡한 migration이 필요한 시점에 SQLAlchemy/Alembic 도입을 검토한다.

셸은 기본 read_only에서 거절된다. owner와 Agent 모두 trusted_personal을 명시적으로
선택하거나 Gateway standard 승인을 받아야 한다. 명령의 문자열 필터를 sandbox로
표현하지 않는다. 테스트는 임시 디렉터리와 직접 생성한 프로세스만 제어한다.
