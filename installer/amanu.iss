; Amanu installer: classic wizard for the bundle; the app itself downloads
; models on first run (see install-choices.json, consumed and deleted by
; amanu_win.setup.apply_install_choices).

[Setup]
AppName=Amanu
AppVersion=1.0
AppPublisher=Amanu
DefaultDirName={localappdata}\Programs\Amanu
PrivilegesRequired=lowest
OutputBaseFilename=amanu-setup
OutputDir={#OutDir}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\amanu.exe

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked
Name: "autorun"; Description: "Запускать с Windows"

[Icons]
Name: "{userdesktop}\Amanu"; Filename: "{app}\amanu.exe"; Tasks: desktopicon
Name: "{userprograms}\Amanu"; Filename: "{app}\amanu.exe"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Amanu"; ValueData: """{app}\amanu.exe"""; Tasks: autorun; Flags: uninsdeletevalue

[Run]
Filename: "{app}\amanu.exe"; Description: "Запустить Amanu"; Flags: nowait postinstall skipifsilent

[Code]
var
  OptionsPage: TInputOptionWizardPage;
  OllamaCheck: TNewCheckBox;
  RecDirEdit: TNewEdit;
  SpaceLabel: TNewStaticText;
  RecDirDirty: Boolean;

const
  BUNDLE_MB = 300;
  OLLAMA_MB = 5000;

function TierMB(i: Integer): Integer;
begin
  case i of
    0: Result := 1600;
    1: Result := 460;
  else Result := 460;
  end;
end;

function SelectedTier: Integer;
begin
  if OptionsPage.Values[0] then Result := 0
  else if OptionsPage.Values[1] then Result := 1
  else Result := 2;
end;

procedure UpdateSpaceLabel;
var
  total: Integer;
begin
  total := BUNDLE_MB + TierMB(SelectedTier);
  if OllamaCheck.Checked then total := total + OLLAMA_MB;
  SpaceLabel.Caption := 'Потребуется места: около ' +
    IntToStr(total div 1024 + 1) + ' ГБ (программа + выбранная модель)';
end;

procedure OptionsChanged(Sender: TObject);
begin
  UpdateSpaceLabel;
end;

procedure RecDirEdited(Sender: TObject);
begin
  RecDirDirty := True;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { recordings default follows the chosen install drive; a path the user
    typed themselves (RecDirDirty) is never touched }
  if (CurPageID = OptionsPage.ID) and not RecDirDirty then
    RecDirEdit.Text := ExpandConstant('{app}\Recordings');
end;

procedure InitializeWizard;
begin
  OptionsPage := CreateInputOptionPage(wpSelectDir,
    'Компоненты', 'Модель распознавания и дополнения',
    'Выберите качество распознавания. Модель скачивается при первом запуске.',
    True, False);
  OptionsPage.Add('Точная (large-v3-turbo, ~1.6 ГБ) — нужен NVIDIA GPU');
  OptionsPage.Add('Сбалансированная (small, ~460 МБ)');
  OptionsPage.Add('Компактная (small + int8, ~460 МБ) — для слабых машин');
  OptionsPage.Values[0] := True;

  OllamaCheck := TNewCheckBox.Create(OptionsPage);
  OllamaCheck.Parent := OptionsPage.Surface;
  OllamaCheck.Top := OptionsPage.SurfaceHeight - ScaleY(64);
  OllamaCheck.Width := OptionsPage.SurfaceWidth;
  OllamaCheck.Caption := 'Сводки и пунктуация (Ollama + модель, ~5 ГБ)';
  OllamaCheck.Checked := True;
  OllamaCheck.OnClick := @OptionsChanged;

  RecDirEdit := TNewEdit.Create(OptionsPage);
  RecDirEdit.Parent := OptionsPage.Surface;
  RecDirEdit.Top := OptionsPage.SurfaceHeight - ScaleY(40);
  RecDirEdit.Width := OptionsPage.SurfaceWidth;
  RecDirEdit.OnChange := @RecDirEdited;
  RecDirDirty := False;

  SpaceLabel := TNewStaticText.Create(OptionsPage);
  SpaceLabel.Parent := OptionsPage.Surface;
  SpaceLabel.Top := OptionsPage.SurfaceHeight - ScaleY(16);
  SpaceLabel.Width := OptionsPage.SurfaceWidth;
  UpdateSpaceLabel;
end;

function JsonEscape(S: string): string;
begin
  StringChange(S, '\', '\\');
  Result := S;
end;


procedure CurStepChanged(CurStep: TSetupStep);
var
  tier, json: string;
  ollama: string;
begin
  if CurStep = ssPostInstall then
  begin
    case SelectedTier of
      0: tier := 'accurate';
      1: tier := 'balanced';
    else tier := 'compact';
    end;
    if OllamaCheck.Checked then ollama := 'true' else ollama := 'false';
    json := '{"tier": "' + tier + '", "ollama": ' + ollama +
            ', "recordings_dir": "' + JsonEscape(RecDirEdit.Text) +
            '", "models_dir": "' + JsonEscape(ExpandConstant('{app}\models')) + '"}';
    SaveStringToFile(ExpandConstant('{app}\install-choices.json'), json, False);
  end;
end;

function InitializeUninstall: Boolean;
var
  models, rec: string;
begin
  Result := True;
  if UninstallSilent then
    exit;  // silent uninstall never deletes user data and never asks
  models := ExpandConstant('{localappdata}\amanu\models');
  rec := ExpandConstant('{userdocs}\Amanu Recordings');
  if MsgBox('Удалить также скачанные модели (' + models + ') и папку записей (' + rec + ')?',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DelTree(models, True, True, True);
    DelTree(rec, True, True, True);
  end;
end;
