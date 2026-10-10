# ADR-0023: 공통 데스크톱 클라이언트와 OS별 Agent 패키지

2026-10-05 · Superseded by [ADR-0030](ADR-0030-rust-tauri-client-and-native-agent.md) · 사용자 추가 요구: Windows·macOS·Linux 배포

> [!NOTE]
> 아래 내용은 이전 Electron/Python 배포 결정의 역사적 기록이다. v0.1.21의 구현·빌드는 ADR-0030을 따른다.

Electron/React는 PC 등록, 로컬 폴더 선택, 연결 상태와 Agent 시작/종료를 제공한다.
원격 파일·명령·터미널 실행은 기존 Python Agent와 Gateway의 RACP 계약을 유지한다.
클라이언트 UI를 닫아도 사용자 background Agent는 계속 실행한다.

Node/npm/pnpm은 개발과 빌드에 사용한다. 배포 패키지는 Electron, Python 3.12.11,
frozen Agent 의존성, 고정 Chromium을 포함하여 사용자 PC의 Node/Python 설치에 의존하지 않는다.
OS별 CPython/native dependency/browser를 해당 OS에서 staging하고, beforePack에서
대상 OS/architecture와 manifest가 다르면 빌드를 거부한다. Windows runtime을 macOS/Linux
패키지에 복사한 결과를 지원 증거로 간주하지 않는다.

Windows는 사용자 설치 NSIS EXE와 ZIP, macOS는 DMG/ZIP, Linux는 AppImage/deb를 구성한다.
현재 실제 검증은 Windows 10 x64다. native CI matrix 작성은 macOS/Ubuntu 실행 성공과 다르다.
서명/notarization, clean install, 자동 시작, 업데이트/rollback/제거는 별도 release gate다.

UI는 packaged local custom protocol을 사용한다. renderer의 Node integration을 끄고
context isolation/sandbox/CSP를 적용한다. preload는 등록/상태/시작/종료/선택 기능만 노출하며
main frame과 sender를 검증한다. Python stdin/stdout bridge는 strict schema와 길이 제한을
적용하고 token을 argv/log/error에 전달하지 않는다. 자격 증명은 기존 SecretStore로 보호한다.

이는 기존 개발정의서의 Python 중심 Agent/Gateway를 교체하지 않는다. macOS 배포 목표는
사용자의 추가 지시이며 원문 지원 검증표는 수정하지 않는다. GUI/Broker/RE 기능은
OS별 capability와 기존 권한 검사를 따르며 shell은 해당 OS 계정 권한으로 실행한다.

공식 근거: [Electron security](https://www.electronjs.org/docs/latest/tutorial/security),
[native platform builds](https://www.electron.build/v26/docs/features/multi-platform-build/).
