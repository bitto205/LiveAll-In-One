; LiveAIO NSIS installer (Qt/Go DLL layout)
; Built by: .\scripts\build.ps1 -Release -Installer
; Or:
;   makensis /DSRCDIR=...\build_work\<ver> /DOUTFILE=...\LiveAIO-setup.exe /DAPP_VERSION=... /DLICENSE_FILE=...\LICENSE scripts\installer.nsi

Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"

!ifndef SRCDIR
  !define SRCDIR "..\build\build_work\custom"
!endif
!ifndef APP_NAME
  !define APP_NAME "LiveAIO"
!endif
!ifndef APP_EXE
  !define APP_EXE "LiveAIO.exe"
!endif
!ifndef APP_VERSION
  !define APP_VERSION "0.0.0"
!endif
!ifndef LICENSE_FILE
  !define LICENSE_FILE "..\LICENSE"
!endif
!ifndef APP_ICON
  !define APP_ICON "${SRCDIR}\${APP_EXE}"
!endif

Name "${APP_NAME}"
!ifdef OUTFILE
  OutFile "${OUTFILE}"
!else
  OutFile "LiveAIO-setup.exe"
!endif
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "Software\${APP_NAME}" "InstallDir"
RequestExecutionLevel admin
ShowInstDetails show
ShowUnInstDetails show

!define REG_APP "Software\${APP_NAME}"
!define REG_UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define REG_APPPATHS "Software\Microsoft\Windows\CurrentVersion\App Paths\${APP_EXE}"

!define MUI_ABORTWARNING
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"

!insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "安装完成"
!define MUI_FINISHPAGE_TEXT "${APP_NAME} 已安装。$\r$\n$\r$\n可从开始菜单或桌面快捷方式启动；也可在「应用和功能」中卸载。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即运行 ${APP_NAME}"
!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_TEXT "创建桌面快捷方式(&D)"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION CreateDesktopShortcut
!insertmacro MUI_PAGE_FINISH

!define MUI_UNABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "卸载 ${APP_NAME}"
!define MUI_WELCOMEPAGE_TEXT "即将从本机移除 ${APP_NAME}。$\r$\n$\r$\n点击「下一步」继续。"
!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "卸载完成"
!define MUI_FINISHPAGE_TEXT "${APP_NAME} 已从本机移除。"
!define MUI_FINISHPAGE_NOREBOOTSUPPORT
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function KillApp
  DetailPrint "正在结束 ${APP_EXE} …"
  nsExec::ExecToLog 'taskkill /F /IM "${APP_EXE}" /T'
  Pop $0
  Sleep 500
FunctionEnd

Function un.KillApp
  DetailPrint "正在结束 ${APP_EXE} …"
  nsExec::ExecToLog 'taskkill /F /IM "${APP_EXE}" /T'
  Pop $0
  Sleep 500
FunctionEnd

Function RunExistingUninstaller
  ReadRegStr $R0 HKLM "${REG_UNINST}" "UninstallString"
  ${If} $R0 == ""
    MessageBox MB_ICONEXCLAMATION "未找到已注册的卸载程序。"
    Abort
  ${EndIf}
  ExecWait '$R0 _?=$INSTDIR'
FunctionEnd

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "本程序仅支持 64 位 Windows。"
    Abort
  ${EndIf}
  SetRegView 64

  ReadRegStr $R1 HKLM "${REG_APP}" "InstallDir"
  ${If} $R1 != ""
  ${AndIf} ${FileExists} "$R1\${APP_EXE}"
    StrCpy $INSTDIR $R1
    MessageBox MB_YESNOCANCEL|MB_ICONQUESTION \
      "${APP_NAME} 已安装在：$\r$\n$R1$\r$\n$\r$\n是(Y) = 卸载$\r$\n否(N) = 重新安装$\r$\n取消 = 退出" \
      IDYES do_uninstall IDNO do_reinstall
    Abort
    do_uninstall:
      Call RunExistingUninstaller
      Quit
    do_reinstall:
  ${EndIf}
FunctionEnd

Function un.onInit
  SetRegView 64
FunctionEnd

Section "主程序" SecMain
  SectionIn RO
  Call KillApp

  SetOutPath "$INSTDIR"
  File /r "${SRCDIR}\*.*"

  WriteRegStr HKLM "${REG_APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${REG_APP}" "Version" "${APP_VERSION}"

  WriteRegStr HKLM "${REG_APPPATHS}" "" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKLM "${REG_APPPATHS}" "Path" "$INSTDIR"

  StrCpy $R9 "$INSTDIR\LiveAIO.ico"
  ${IfNot} ${FileExists} "$R9"
    StrCpy $R9 "$INSTDIR\${APP_EXE}"
  ${EndIf}
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$R9" 0
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe" "" "$R9" 0

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "${REG_UNINST}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${REG_UNINST}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${REG_UNINST}" "Publisher" "${APP_NAME}"
  WriteRegStr HKLM "${REG_UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${REG_UNINST}" "DisplayIcon" "$R9"
  WriteRegStr HKLM "${REG_UNINST}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${REG_UNINST}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKLM "${REG_UNINST}" "NoModify" 1
  WriteRegDWORD HKLM "${REG_UNINST}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${REG_UNINST}" "EstimatedSize" "$0"
SectionEnd

Function CreateDesktopShortcut
  StrCpy $R9 "$INSTDIR\LiveAIO.ico"
  ${IfNot} ${FileExists} "$R9"
    StrCpy $R9 "$INSTDIR\${APP_EXE}"
  ${EndIf}
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$R9" 0
FunctionEnd

Section "Uninstall"
  Call un.KillApp
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "${REG_APPPATHS}"
  DeleteRegKey HKLM "${REG_UNINST}"
  DeleteRegKey HKLM "${REG_APP}"
SectionEnd
