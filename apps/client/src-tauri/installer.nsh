!include "LogicLib.nsh"
!define RACP_MAINTENANCE_SOURCE "${__FILEDIR__}\staged\agent\racp-agent.exe"
!macro RACP_PREPARE MODE
  IfFileExists "$INSTDIR\racp-client.exe" racp_present_${MODE} 0
  IfFileExists "$INSTDIR\RACP Client.exe" racp_present_${MODE} racp_done_${MODE}
  racp_present_${MODE}:
    InitPluginsDir
    File /oname=$PLUGINSDIR\racp-maintenance.exe "${RACP_MAINTENANCE_SOURCE}"
    nsExec::ExecToStack /TIMEOUT=60000 '"$PLUGINSDIR\racp-maintenance.exe" maintenance --install-dir "$INSTDIR" --state-auto --executable-name "racp-client.exe" --login-name "app.racp.client" --mode "${MODE}"'
    Pop $R0
    Pop $R1
    ${If} $R0 != 0
      SetErrorLevel 4
      Abort "RACP Client: use Full exit, then retry. Cleanup and backup must be confirmed."
    ${EndIf}
  racp_done_${MODE}:
  SetOutPath $TEMP
!macroend
!macro NSIS_HOOK_PREINSTALL
  !insertmacro RACP_PREPARE upgrade
!macroend
!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro RACP_PREPARE uninstall
!macroend
!macro NSIS_HOOK_POSTINSTALL
  nsExec::ExecToStack /TIMEOUT=30000 '"$INSTDIR\agent\racp-agent.exe" maintenance --install-dir "$INSTDIR" --state-auto --executable-name "racp-client.exe" --login-name "app.racp.client" --mode "finalize"'
  Pop $R0
  Pop $R1
  ${If} $R0 != 0
    SetErrorLevel 4
    Abort "RACP Client startup migration failed. User data has been preserved."
  ${EndIf}
!macroend
