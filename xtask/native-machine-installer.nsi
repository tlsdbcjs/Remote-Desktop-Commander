; Machine service package definition. Registration input is never embedded or imported.
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!ifndef PRODUCT_VERSION
  !error "PRODUCT_VERSION must be supplied by xtask"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD must be supplied by xtask"
!endif
!ifndef OUTPUT
  !error "OUTPUT must be supplied by xtask"
!endif
Name "RACP Agent Service ${PRODUCT_VERSION}"
OutFile "${OUTPUT}"
InstallDir "$PROGRAMFILES64\RACP-Native\${PRODUCT_VERSION}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${PAYLOAD}\LICENSE"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "This native package requires Windows x64."
    Abort
  ${EndIf}
  StrCpy $INSTDIR "$PROGRAMFILES64\RACP-Native\${PRODUCT_VERSION}"
  SetShellVarContext all
  SetRegView 64
  IfFileExists "$INSTDIR\*.*" 0 +3
    MessageBox MB_OK|MB_ICONSTOP "This machine version directory already exists. Resolve its existing installation before retrying. No state is imported."
    Abort
FunctionEnd
Section "Agent Service"
  SetOutPath "$INSTDIR\payload"
  File "${PAYLOAD}\racp-agent.exe"
  File "${PAYLOAD}\LICENSE"
  File "${PAYLOAD}\sbom.json"
  File "${PAYLOAD}\payload-manifest.json"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  ClearErrors
  ExecWait '$\"$INSTDIR\payload\racp-agent.exe$\" native-machine-install-protect' $0
  IfErrors blocked
  StrCmp $0 0 0 blocked
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-Service-${PRODUCT_VERSION}" "DisplayName" "RACP Agent Service ${PRODUCT_VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-Service-${PRODUCT_VERSION}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-Service-${PRODUCT_VERSION}" "UninstallString" '$\"$INSTDIR\uninstall.exe$\"'
  Goto done
  blocked:
    MessageBox MB_OK|MB_ICONSTOP "Machine payload protection failed. This partial version directory is preserved for inspection. Service registration and enrollment were not started."
    Abort
  done:
SectionEnd
Section "Uninstall"
  SetShellVarContext all
  SetRegView 64
  IfFileExists "$INSTDIR\payload.removing\*.*" blocked 0
  IfFileExists "$INSTDIR\payload\racp-agent.exe" 0 blocked
  ClearErrors
  ExecWait '$\"$INSTDIR\payload\racp-agent.exe$\" native-machine-uninstall-preflight' $0
  IfErrors blocked
  StrCmp $0 0 0 blocked
  ClearErrors
  Rename "$INSTDIR\payload" "$INSTDIR\payload.removing"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\racp-agent.exe"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\LICENSE"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\sbom.json"
  IfErrors blocked
  Delete "$INSTDIR\payload.removing\payload-manifest.json"
  IfErrors blocked
  RMDir "$INSTDIR\payload.removing"
  IfErrors blocked
  Delete "$INSTDIR\machine-install.json"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\RACP-Native-Service-${PRODUCT_VERSION}"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  Goto done
  blocked:
    MessageBox MB_OK|MB_ICONSTOP "Stop and confirm Agent cleanup, explicitly detach the owned native service, resolve the setup helper, and close all Agent payload handles before removing this machine package. State and credentials are preserved."
    Abort
  done:
SectionEnd
