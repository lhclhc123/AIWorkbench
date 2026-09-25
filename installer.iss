; AI 工作台 安装包脚本（Inno Setup）
; 用法：用 Inno Setup Compiler 打开本文件 -> Build。
; 安装时用户可自行选择安装路径，并创建开始菜单/桌面快捷方式、卸载程序。
; 注意：先运行 `python build_v9.py` 生成 dist_v9\AIWorkbench\ 目录，再编译本脚本。
;       （本机未安装 Inno Setup；装一个 6.x 版即可，或直接用 dist_v9\AIWorkbench_v9.zip 解压即用）

#define MyAppVersion "9.0.0"

[Setup]
AppName=AI 工作台
AppVersion={#MyAppVersion}
AppVerName=AI 工作台 {#MyAppVersion}
AppPublisher=AIWorkbench
DefaultDirName={autopf}\AIWorkbench
DefaultGroupName=AI 工作台
OutputDir=installer
OutputBaseFilename=AIWorkbench_Setup_v9
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 普通用户即可安装（不写系统目录、不需管理员）
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\AIWorkbench.exe
SetupIconFile=
; 允许用户在安装页改路径

[Languages]
; 若你的 Inno Setup 没有中文包，把下面这行删掉即可（默认英文）
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "dist_v9\AIWorkbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AI 工作台"; Filename: "{app}\AIWorkbench.exe"
Name: "{autodesktop}\AI 工作台"; Filename: "{app}\AIWorkbench.exe"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
