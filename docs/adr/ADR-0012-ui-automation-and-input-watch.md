# ADR-0012 — UI Automation 관측과 입력 중단 hook

상태: Phase 8 부분 개발 구현 · 2026-10-03

## 관측과 구조 입력

`desktop.inspect`는 명시한 window ID의 UI Automation control tree를 반환한다.
한 번에 최대 64개, 깊이 8, node JSON 합계 32 KiB, 순회 budget 1초다.
한도를 만나면 `truncated`를 반환한다. 제목/이름/Automation ID는 제한된 길이로 표시하고
password 컨트롤의 이름과 pattern은 노출하지 않는다. Value/Text의 내용은 관측 응답에 넣지 않는다.

`desktop.invoke`와 `desktop.set_value`는 관측에서 발급한 `element_ref`를 사용한다.
원래 COM element/root를 보관하며 selector나 이름으로 다른 컨트롤을 다시 찾지 않는다.
reference는 observation/window 범위와 고정 5초 TTL, 총 256개 제한을 가진다.
owner/device/session/lease/layout/foreground 경계에 더해 runtime ID, process birth,
geometry, name, Automation ID, enabled/offscreen/password와 원래 root의 조상 관계를 검사한다.
관측 후 변경·분리·다른 계정/세션·상위 integrity·읽을 수 없는 identity는 mutation을 거부한다.
성공 또는 실패한 입력 뒤 기존 관측/ref를 폐기한다.

원격 호출을 직렬 처리하는 Broker thread는 HWND를 소유하지 않는 COM MTA다.
`comtypes 1.4.17`과 OS System32 UIAutomationCore typelib을 사용하고 생성 wrapper는 메모리에만 둔다.
IUIAutomation2 connection/transaction timeout은 각 500ms이며 AutoSetFocus는 false다.
전체 native 호출이 멈추면 기존 Agent RPC deadline/Job 정리가 적용된다.
Invoke/Value pattern 실행 결과도 `os_dispatch_only`이며 앱 내용 변경 성공을 단정하지 않는다.
지원 pattern이 없으면 명시적인 기존 Win32 입력 요청을 사용할 수 있다.

## 사용자 입력과 창 변화

독립적인 message-pump thread에서 WH_KEYBOARD_LL/WH_MOUSE_LL과 out-of-context WinEvent hook을 유지한다.
외부 입력은 interruption sequence만 증가시키고 키·문자·포인터 이력을 저장하지 않는다.
Broker의 무작위 양수 31-bit marker와 injected flag가 모두 맞는 자체 SendInput만 외부 입력에서 제외한다.
실제 mouse 경로에서 상위 비트 불일치를 관측해 driver 경로와 sign extension을 고려한 폭을 사용한다.
다른 프로그램의 injected input도 중단 사유다. key/button/wheel은 SendInput 후 자체 hook echo를 확인한다.
mouse delivery를 위한 최대 100ms 확인 budget을 둔다. zero-distance move는 hook 전에 생략될 수 있어
movement만으로 hook liveness를 판정하지 않는다. 진단은 event counter만 기록한다.
hook callback은 관측 counter 갱신과 CallNextHookEx만 수행하며 입력을 차단하지 않는다.

foreground event counter는 잠깐 다른 창으로 갔다 돌아온 변화도 lease를 무효화한다.
관측한 HWND의 create/destroy generation은 handle 재사용을 기존 window ID로 받아들이지 않는다.
이 map은 4096개로 제한하고 eviction 후에는 기존 token이 다시 유효해지지 않는다.
message-pump heartbeat/fence 장애는 데스크톱 실행을 거부한다.
OS의 hook silent removal·driver/desktop 경계까지 완전한 감지를 보장하지 않으므로
`user_input_detection_complete=false`를 유지한다.

입력 전후의 interruption/foreground를 비교한다. 실행 중 변화가 있으면 결과를 새 baseline으로
승인하지 않고 lease/ref를 해제하며 side effect는 unknown으로 표시한다.
Windows named mutex는 같은 OS 세션의 별도 Broker/Device 사이에서도 exclusive lease를 적용한다.
mutex ACL은 해당 로그온 사용자와 SYSTEM에 한정하고 Broker 종료 시 OS가 abandoned 상태를 처리한다.
modifier/button 해제 실패 시 아직 해제되지 않은 항목을 보존해 후속 정리에서 다시 시도한다.

## Drag와 검증 경계

Broker 내부 drag는 10ms 단위로 foreground/관측/input interruption을 검사하며 finally 경로에서
자신의 버튼을 해제한다. 그러나 Agent가 Broker Job을 강제 종료하면 Broker finally가 실행되지 않는다.
[ADR-0013](ADR-0013-independent-input-release-guardian.md)의 독립 Guardian/release gate를 연결했다.
정상 HTTP drag와 실제 Job kill/Agent crash/cancel/revoke 후 해제를 검증하여 Guardian이 준비된
세션에 한해 공개 drag를 제공한다. 전체 Desktop 인수 gate가 완료됐다는 의미는 아니다.

일반 품질 gate의 경계 주입 검증과 별개로 `RACP_TEST_GUI=1`의 opt-in 시험은 임시 자체 창만 사용한다.
현재 Windows 10에서 실제 UIA Edit/Button/password tree와 Value/Invoke, 자체 창의 Unicode/chord,
정상 drag/버튼 해제, physical crop/preview와 외부 marker 주입 거부를 검증했다.
실제 Broker/Agent/HTTP/MCP의 UIA 관측, 창 destroy hook과 별도 프로세스 mutex 경쟁도 검증했다.
초기 foreground 거부는 skip했고 이후 OS가 허용한 자체 창 show/activation에서 초기 4개와
Guardian/취소/HTTP drag 후속 시험을 포함한 11개를 통과했다. 최신 회귀에서는 Job termination의
OS 활성화 거부 skip이 있어 현재 고유 10개 확인과 구분한다([Phase 8 결과](../phase-8-broker-result.md)).
현재 단일 4K display의 150% scale을 확인했다. foreground 제한을 우회하는 입력은 사용하지 않는다.
UIA focus의 자체 provider 재진입 진단이 발생한 경로는 제거했다.
Notepad/Calculator, SCM/다른 계정, 나머지 DPI/RDP/UAC/locked/참조 OS gate는 계속 남아 있다.

## 공식 기술 근거

- [UI Automation MTA/thread 규칙](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)
- [IUIAutomation2 timeout/AutoSetFocus](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomation2)
- [Runtime ID는 재사용 가능](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getruntimeid)
- [SetWindowsHookEx](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowshookexw)
- [LowLevelMouseProc thread/timeout 한계](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc)
- [SetWinEventHook message loop/수명](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook)
- [comtypes 1.4.17](https://pypi.org/project/comtypes/1.4.17/)
