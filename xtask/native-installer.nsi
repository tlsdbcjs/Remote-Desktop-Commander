# -*- coding: utf-8 -*-
; Per-user/versioned native install; user state is outside the install directory.
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!ifndef PRODUCT_VERSION
  !error "PRODUCT_VERSION must be supplied by xtask"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD must be supplied by xtask"
!endif
!ifndef OUTPUT
  !error "OUTPUT must be supplied by xtask"
!endif
Name "RACP Native Client ${PRODUCT_VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\RACP-Native\${PRODUCT_VERSION}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "${PRODUCT_VERSION}.0"
VIAddVersionKey /LANG=1042 "ProductName" "RACP Native Client"
VIAddVersionKey /LANG=1042 "ProductVersion" "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=1042 "FileVersion" "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=1042 "FileDescription" "RACP Client and Agent Setup"
VIAddVersionKey /LANG=1042 "LegalCopyright" "RACP contributors"
!define MUI_WELCOMEPAGE_TEXT "이 PC에서 원격 작업의 권한을 설정하고 요청을 승인하는 RACP를 설치합니다.$\r$\n$\r$\nClient와 Agent가 함께 설치됩니다. 설치 후 시작 메뉴에서 RACP를 실행해 Gateway에 등록하세요."
!define MUI_FINISHPAGE_RUN "$INSTDIR\payload\racp-client.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "--require-package"
!define MUI_FINISHPAGE_RUN_TEXT "RACP 실행"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${PAYLOAD}\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Korean"
Function .onInit
  SetShellVarContext current
  SetRegView 64
  ; Never overwrite an installed native version or a partially created install.
  IfFileExists "$INSTDIR\*.*" existing clean
  existing:
    SetErrorLevel 2
    MessageBox MB_OK|MB_ICONSTOP "이 설치 폴더가 이미 존재합니다. RACP를 종료하고 해당 버전을 제거한 뒤 다시 설치하세요. 등록 정보와 사용자 데이터는 보존됩니다." /SD IDOK
    Abort
  clean:
FunctionEnd
Section "Native Client"
  ; Check again after the directory page, including an explicit /D target.
  IfFileExists "$INSTDIR\*.*" 0 install
    SetErrorLevel 2
    MessageBox MB_OK|MB_ICONSTOP "설치 폴더가 비어 있지 않습니다. 다른 폴더를 선택하거나 기존 버전을 제거하세요." /SD IDOK
    Abort
  install:
  ClearErrors
  SetOutPath "$INSTDIR\payload"
  File "${PAYLOAD}\racp-client.exe"
  File "${PAYLOAD}\racp-agent.exe"
  File "${PAYLOAD}\LICENSE"
  File "${PAYLOAD}\sbom.json"
  File "${PAYLOAD}\payload-manifest.json"
  IfErrors install_failed
  WriteUninstaller "$INSTDIR\uninstall.exe"
  IfErrors install_failed
  CreateShortcut "$SMPROGRAMS\RACP Native ${PRODUCT_VERSION}.lnk" "$INSTDIR\payload\racp-client.exe" "--require-package"
  IfErrors install_failed
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "DisplayName" "RACP Native Client ${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "UninstallString" '$\"$INSTDIR\uninstall.exe$\"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "QuietUninstallString" '$\"$INSTDIR\uninstall.exe$\" /S'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "DisplayIcon" "$INSTDIR\payload\racp-client.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "Publisher" "RACP contributors"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}" "NoRepair" 1
  IfErrors install_failed
  Goto install_done
  install_failed:
    SetErrorLevel 3
    MessageBox MB_OK|MB_ICONSTOP "설치를 완료하지 못했습니다. 설치 폴더의 쓰기 권한과 남아 있는 파일을 확인하세요. 등록 정보와 사용자 데이터는 변경하지 않았습니다." /SD IDOK
    Abort
  install_done:
SectionEnd
Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  ; Only a complete, admitted payload may prepare removal. Do not resume an unknown partial batch.
  IfFileExists "$INSTDIR\payload.removing\*.*" blocked 0
  IfFileExists "$INSTDIR\payload\racp-agent.exe" 0 blocked
  ClearErrors
  ExecWait '$\"$INSTDIR\payload\racp-agent.exe$\" native-maintenance-status --state-dir $\"$LOCALAPPDATA\RACP\native-client$\"' $0
  IfErrors blocked
  StrCmp $0 0 0 blocked
  ; Move the whole directory first. GUI/Agent/helper directory pins prevent the rename.
  ; This avoids deleting Agent first and discovering a still-running GUI afterwards.
  ClearErrors
  Rename "$INSTDIR\payload" "$INSTDIR\payload.removing"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\racp-agent.exe"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\racp-client.exe"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\LICENSE"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\sbom.json"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\payload-manifest.json"
  IfErrors blocked
  RMDir "$INSTDIR\payload.removing"
  IfErrors blocked
  Delete "$SMPROGRAMS\RACP Native ${PRODUCT_VERSION}.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-${PRODUCT_VERSION}"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  Goto done
  blocked:
    SetErrorLevel 2
    MessageBox MB_OK|MB_ICONSTOP "로그인 자동 시작을 해제하고 Agent를 정지한 뒤 RACP를 종료하세요. payload.removing 폴더가 남아 있다면 직접 확인이 필요합니다. 등록 정보와 사용자 데이터는 보존됩니다." /SD IDOK
    Abort
  done:
SectionEnd
