[Setup]
AppId={{A4F47AF0-2CB8-4E8E-9BF8-BOOKING2026}
AppName=江苏大学羽毛球场地预约助手
AppVersion=1.0.1
AppPublisher=Jiangsu University Smart Booking
DefaultDirName={localappdata}\JiangsuBadmintonBooking
DefaultGroupName=江苏大学羽毛球场地预约助手
OutputDir=installer
OutputBaseFilename=羽毛球场地预约助手-安装程序-v1.0.1-双场地版
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\羽毛球场地预约助手.exe
DisableProgramGroupPage=yes
CloseApplications=force
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "dist-qt\羽毛球场地预约助手\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\江苏大学羽毛球场地预约助手"; Filename: "{app}\羽毛球场地预约助手.exe"
Name: "{autodesktop}\羽毛球场地预约助手"; Filename: "{app}\羽毛球场地预约助手.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\羽毛球场地预约助手.exe"; Description: "启动羽毛球场地预约助手"; Flags: nowait postinstall skipifsilent
