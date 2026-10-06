# ADR-0026: Windows 설치·제거의 Agent 정리와 상태 백업

2026-10-05 · 채택 · Windows current-user client 0.1.3

NSIS의 기본 process-name 강제 종료 대신 설치된 bundle의 Python으로 maintenance helper를
실행한다. 같은 설치 경로의 GUI가 열려 있으면 완전 종료를 안내하고 중단한다. background Agent는
private stop으로 정리하고 STOPPED와 cleanup 결과를 확인한다. 확인 실패 시 파일 교체를 중단한다.
다른 설치 경로의 같은 이름 process를 종료하지 않는다.

닫힌 Agent state를 Device lock 아래에서 `agent/backups/before-maintenance-<UUID>`로 복사한다.
보호된 credential, journal, log와 data 파일을 보존하고 각 파일 SHA-256를 manifest에 기록한다.
기존 backups와 ephemeral lock 파일은 제외한다. 백업은 같은 Windows 사용자용이며
다른 계정이나 PC로 credential을 이전하는 기능은 아니다.

제거에서는 HKCU Run의 해당 app ID 값이 현재 설치 EXE와 고정 launcher 인자를 정확히 가리킬 때만
삭제한다. 다른 설치를 가리키면 중단한다. 대응 StartupApproved 값도 정리한다.
등록 정보와 Agent data는 기본적으로 보존한다.

E: 설치 폴더의 최초 native upgrade 시험은 기존 제거 단계에서 실패했고 이전 설치가 보존됐다.
electron-builder의 기본 업데이트 제거는 설치 파일들을 plugin 임시 폴더로 Rename한다.
서로 다른 volume의 Rename을 피하도록 현재 제거 프로그램은 설치 폴더의 부모에 고유 임시 폴더를
예약하고, 종료된 설치 폴더 전체를 같은 volume에서 이동한 뒤 제거한다. 이동 실패 시 기존 설치를
그대로 두고 중단한다. 설치 프로그램 자신도 이전 설치 폴더의 current-directory handle을
해제한 뒤 제거 프로그램을 실행한다. 이 제거 로직은 과거에 배포한 제거 프로그램에 소급 적용되지 않는다.

## 검증

`scripts/build_installer_fixture.py`는 공개 앱과 구분되는 package/app ID/product로 두 버전을 만든다.
`scripts/installer_acceptance.py`는 그 fixture의 설치 경로, AppData, HKCU 값만 다룬다.
원격 141 PC나 사용자의 실제 등록 정보는 사용하지 않는다. 실패한 fixture는 보존하며 `--resume`은
그 fixture에 대한 복구용이다. 정상 제거 후 등록 정보가 남는 것도 확인했다.

maintenance unit 4개, 전체 Python **302 passed / 18 skipped**, Node **9 passed**와
mypy **129 source files**를 통과했다. native upgrade/reconnect/uninstall 시험은 통과했다.
설치된 실행 파일 0.1.3 → fixture 0.1.4 전환, Device 연결 epoch 1 → 2와 새 boot ID,
기존 SUCCEEDED journal row, 동일 key의 counter=1, credential hash와 모든 backup hash를 확인했다.
제거 후 실행 EXE/runtime와 fixture startup 항목이 사라지고 credential은 보존됐다.
결과는 `dist/installer-acceptance-result.json`에 있다. 최초 빈 설치는 별도 앞선 단계에서 확인했고,
최종 결과는 보존된 fixture의 복구 실행이다. clean VM 검증을 대신하지 않는다.
후속 검사에서 260자보다 긴 browser metadata 파일 두 개가 제거 후 남는 것을 발견했다.
Unicode NSIS의 extended-length 경로로 제거하도록 보완했고 실제 잔여 파일 제거 probe와
최종 설치 → 업그레이드 → 재접속 → 제거를 다시 통과했다. 최종 시험은 이전 설치 staging 폴더와
모든 bundle 파일(긴 경로 포함)이 남지 않는 것을 검사한다. 시험은 동기 대기를 위해 `_?=`를
사용하므로 자기 자신인 fixture 제거 EXE 하나만 예외로 남는다. 실제 UI 제거와 clean VM은 별도다.
기존 0.1.0 bundle Python에서 현재 helper의 빈 state 실행도 통과했다.

## 범위

Agent와 GUI를 정리한 current-user 수동 설치 경로다. clean VM, Windows 11, 실제 OS reboot,
SCM/다른 계정 Broker, 완전한 migration/rollback/backup restore/readiness gate와 서명 배포는 남는다.
Device lock은 백업 중에 유지하며 전체 NSIS 파일 교체 동안의 admission lock은 아직 아니다.
자동 remote self-update는 제공하지 않는다.

설치 EXE/소스 SHA-256는 `dist/client-desktop-0.1.3/build-manifest.json`에 있다.
이 시험 당시 공개 버전은 0.1.3이고 fixture의 0.1.4는 별도 product/app ID의 업데이트 시험용 버전이다.
후속 공개 0.1.4의 등록·진단 변경은 ADR-0027에 기록한다.

근거: 로컬 electron-builder 26.15.3 NSIS templates와
[NSIS 실행 옵션](https://nsis.sourceforge.io/Docs/Chapter3.html)과
[Rename/작업 디렉터리 계약](https://nsis.sourceforge.io/Docs/Chapter4.html).
