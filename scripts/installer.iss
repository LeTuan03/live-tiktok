; Inno Setup script — tạo bộ cài Windows cho LiveFlow AI Studio.
; 1) Chạy BUILD_EXE.bat để có dist\LiveFlowAIStudio
; 2) Cài Inno Setup 6 (https://jrsoftware.org/isdl.php), mở file này và bấm Compile.
; Kết quả: installer_output\LiveFlowAIStudio-Setup-1.0.0.exe

#define MyAppName "LiveFlow AI Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "LiveFlow"
#define MyAppExeName "LiveFlowAIStudio.exe"

[Setup]
AppId={{7B0C5D7E-1F6A-4C33-9B4E-2A9F1C0D5E77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\LiveFlow AI Studio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\installer_output
OutputBaseFilename=LiveFlowAIStudio-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng trên Desktop"; GroupDescription: "Tùy chọn:"

[Files]
Source: "..\dist\LiveFlowAIStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\HUONG_DAN_SU_DUNG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Mở {#MyAppName}"; Flags: nowait postinstall skipifsilent
