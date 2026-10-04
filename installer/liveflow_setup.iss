; Inno Setup 6 Script for LiveFlow AI Studio
; Packaging & One-Click Deployment on clean Windows hosts (§6 P6)

#define MyAppName "LiveFlow AI Studio"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "LiveFlow Official"
#define MyAppURL "https://github.com/liveflow"
#define MyAppExeName "LiveFlow.exe"

[Setup]
AppId={{E68A9F32-4217-48D3-89DF-073AB51C576F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=dist\installer
OutputBaseFilename=LiveFlow_Setup_v2.0
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\LiveFlow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\doctor.bat"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\OPERATOR_GUIDE.md"; DestDir: "{app}"; Flags: isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\LiveFlow Doctor (Kiểm tra hệ thống)"; Filename: "{app}\tools\doctor.bat"
Name: "{group}\Hướng dẫn vận hành (Operator Guide)"; Filename: "{app}\OPERATOR_GUIDE.md"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\tools\doctor.bat"; Description: "Chạy LiveFlow Doctor để kiểm tra môi trường"; Flags: postinstall skipifsilent nowait
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent unchecked
