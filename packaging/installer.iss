; Inno Setup script: wraps the PyInstaller folder (dist\MusiSplitSeven) into a single Setup.exe.
;   ISCC.exe /DAppVersion=0.1.3 packaging\installer.iss
; Output: installer\MusiSplitSeven-<version>-Setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define Root SourcePath + ".."
#define AppName "MusiSplitSeven"
#define AppExe "MusiSplitSeven.exe"

[Setup]
; Never change AppId: it is how upgrades and the uninstaller find the installed app.
AppId={{CE18C227-FC0D-4F29-B238-C84EA824DD04}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}
; Per-user install: no administrator prompt.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#Root}\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir={#Root}\installer
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
CloseApplications=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
en.DeleteData=Also delete the downloaded separation model and your History of split songs?%n%nMP3s you saved to your own folders are not affected.
fr.DeleteData=Supprimer aussi le modèle de séparation téléchargé et l'historique des morceaux ?%n%nLes MP3 que vous avez enregistrés dans vos dossiers ne sont pas touchés.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Files from a previous version must not mix with the new ones.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Root}\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  DataDir := ExpandConstant('{userappdata}\StemSplit');
  if UninstallSilent or not DirExists(DataDir) then
    exit;
  if MsgBox(CustomMessage('DeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DelTree(DataDir, True, True, True);
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\{#AppName}');
  end;
end;
