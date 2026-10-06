!define RACP_MAINTENANCE_SOURCE "${PROJECT_DIR}\build\maintenance.py"

!macro customRemoveFiles
  Var /GLOBAL racpRemovalParent
  Var /GLOBAL racpRemovalStage
  ${If} ${isUpdated}
    # The stock file-by-file Rename targets $PLUGINSDIR, which may be on
    # another drive. Reserve an empty sibling and move the closed installation
    # as one directory on its own volume; a failed Rename leaves it intact.
    ${GetParent} "$INSTDIR" $racpRemovalParent
    ClearErrors
    GetTempFileName $racpRemovalStage $racpRemovalParent
    ${If} ${Errors}
      Abort "Cannot reserve upgrade staging on the installation volume."
    ${EndIf}
    Delete "$racpRemovalStage"
    CreateDirectory "$racpRemovalStage"
    ${If} ${Errors}
      Abort "Cannot create upgrade staging on the installation volume."
    ${EndIf}
    SetOutPath $TEMP
    ClearErrors
    Rename "$INSTDIR" "$racpRemovalStage\old-install"
    ${If} ${Errors}
      RMDir "$racpRemovalStage"
      Abort "Installation is busy; use full exit before upgrading."
    ${EndIf}
    RMDir /r "\\?\$racpRemovalStage"
  ${Else}
    SetOutPath $TEMP
    RMDir /r "\\?\$INSTDIR"
  ${EndIf}
!macroend

!macro customCheckAppRunning
  ${If} ${FileExists} "$INSTDIR\resources\agent\runtime\python.exe"
    InitPluginsDir
    File /oname=$PLUGINSDIR\racp-maintenance.py "${RACP_MAINTENANCE_SOURCE}"
    !ifdef BUILD_UNINSTALLER
      ${If} ${isUpdated}
        StrCpy $R8 "upgrade"
      ${Else}
        StrCpy $R8 "uninstall"
      ${EndIf}
    !else
      StrCpy $R8 "upgrade"
    !endif
    nsExec::ExecToStack /TIMEOUT=60000 '"$INSTDIR\resources\agent\runtime\python.exe" -I "$PLUGINSDIR\racp-maintenance.py" --install-dir "$INSTDIR" --state-dir "$APPDATA\${APP_PACKAGE_NAME}\agent" --executable-name "${APP_EXECUTABLE_FILENAME}" --login-name "${APP_ID}" --mode "$R8"'
    Pop $R0
    Pop $R1
    ${If} $R0 != 0
      DetailPrint "RACP maintenance is unconfirmed. Use full exit, then retry."
      ${IfNot} ${Silent}
        MessageBox MB_OK|MB_ICONEXCLAMATION "RACP Client: use Full exit from the tray or client window, then retry installation. Agent cleanup could not be confirmed."
      ${EndIf}
      SetErrorLevel 4
      Abort
    ${EndIf}
  ${EndIf}
  # The installer itself must release its current-directory handle before
  # the old uninstaller renames the installation directory.
  SetOutPath $TEMP
!macroend
