!ifndef BUILD_UNINSTALLER
!include "nsDialogs.nsh"

!ifndef RADIO_PROFILE_NAME
  !define RADIO_PROFILE_NAME "PersonalRadio"
!endif

Var radioDataDirectory
Var radioDataInput
Var radioLocation
Var radioDirectoryError

!macro customHeader
Function RadioReadDataDirectory
  SetShellVarContext current
  StrCpy $radioLocation "$APPDATA\${RADIO_PROFILE_NAME}-location"
  StrCpy $radioDataDirectory "$APPDATA\${RADIO_PROFILE_NAME}"
  ${If} $installMode == "all"
    SetShellVarContext all
  ${EndIf}
  ClearErrors
  FileOpen $0 "$radioLocation\requested.txt" r
  ${If} ${Errors}
    ClearErrors
    FileOpen $0 "$radioLocation\active.txt" r
  ${EndIf}
  ${IfNot} ${Errors}
    FileReadUTF16LE $0 $1
    FileClose $0
    ${If} $1 != ""
      StrCpy $radioDataDirectory $1
    ${EndIf}
  ${EndIf}
FunctionEnd

Function RadioVerifyDataDirectory
  StrCpy $radioDirectoryError "请选择本机磁盘上的子目录，例如 D:\我的电台数据。"
  StrCpy $0 $radioDataDirectory 2 1
  StrLen $1 $radioDataDirectory
  ${If} $0 != ":\"
  ${OrIf} $1 <= 3
    SetErrors
    Return
  ${EndIf}
  # Windows 按字符串规范化尚未创建的目录，NSIS 的同名指令要求父目录存在。
  System::Call 'kernel32::GetFullPathNameW(w "$radioDataDirectory", i ${NSIS_MAX_STRLEN}, w .r6, p 0)i.r7'
  System::Call 'kernel32::GetFullPathNameW(w "$INSTDIR", i ${NSIS_MAX_STRLEN}, w .r1, p 0)i.r8'
  ${If} $7 <= 3
  ${OrIf} $7 >= ${NSIS_MAX_STRLEN}
  ${OrIf} $8 <= 0
  ${OrIf} $8 >= ${NSIS_MAX_STRLEN}
    SetErrors
    Return
  ${EndIf}
  StrCpy $radioDataDirectory $6
  StrCpy $2 "$radioDataDirectory\"
  StrCpy $3 "$1\"
  StrLen $0 $3
  StrCpy $4 $2 $0
  StrLen $0 $2
  StrCpy $5 $3 $0
  ${If} $4 == $3
  ${OrIf} $5 == $2
    StrCpy $radioDirectoryError "用户数据目录和程序安装目录不能重叠。"
    SetErrors
    Return
  ${EndIf}
  ClearErrors
FunctionEnd

Function RadioBrowseDataDirectory
  Pop $0
  ${NSD_GetText} $radioDataInput $0
  nsDialogs::SelectFolderDialog "选择用户数据位置" "$0"
  Pop $0
  ${If} $0 != "error"
    ${NSD_SetText} $radioDataInput $0
  ${EndIf}
FunctionEnd

Function RadioDataPage
  !insertmacro MUI_HEADER_TEXT "用户数据位置" "选择曲库、DJ 记忆、设置和播放记录的保存位置。"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == "error"
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 2u 100% 26u "可以保留当前目录。更换位置时，请选择一个新目录或空文件夹。首次启动会复制并校验原有数据。"
  Pop $0
  ${NSD_CreateDirRequest} 0 36u 75% 14u "$radioDataDirectory"
  Pop $radioDataInput
  ${NSD_CreateBrowseButton} 78% 35u 22% 16u "浏览…"
  Pop $0
  ${NSD_OnClick} $0 RadioBrowseDataDirectory
  nsDialogs::Show
FunctionEnd

Function RadioDataPageLeave
  ${NSD_GetText} $radioDataInput $radioDataDirectory
  Call RadioVerifyDataDirectory
  ${If} ${Errors}
    MessageBox MB_ICONEXCLAMATION|MB_OK "$radioDirectoryError"
    Abort
  ${EndIf}
FunctionEnd
!macroend

!macro customPageAfterChangeDir
  Page custom RadioDataPage RadioDataPageLeave
!macroend

!macro customInit
  Call RadioReadDataDirectory
  ${If} ${Silent}
    ${StdUtils.GetParameter} $0 "data-directory" ""
    ${If} $0 != ""
      StrCpy $radioDataDirectory "$0"
    ${EndIf}
    Call RadioVerifyDataDirectory
    ${If} ${Errors}
      SetErrorLevel 2
      Quit
    ${EndIf}
  ${EndIf}
!macroend

!macro customInstall
  ClearErrors
  CreateDirectory "$radioLocation"
  FileOpen $0 "$radioLocation\requested.tmp" w
  ${IfNot} ${Errors}
    FileWriteUTF16LE /BOM $0 "$radioDataDirectory"
    FileClose $0
    ${IfNot} ${Errors}
      System::Call 'kernel32::MoveFileExW(w "$radioLocation\requested.tmp", w "$radioLocation\requested.txt", i 9)i.r1'
      ${If} $1 != 0
        Goto radio_location_done
      ${EndIf}
    ${EndIf}
  ${EndIf}
  MessageBox MB_ICONSTOP|MB_OK "无法保存用户数据位置。请检查权限后重试安装。" /SD IDOK
  SetErrorLevel 3
  Quit
  radio_location_done:
!macroend
!endif
