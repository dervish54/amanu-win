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

CloseApplications=force
CloseApplicationsFilter=amanu.exe
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
russian.ConfirmUninstall=Удалить программу %1?%n%nБудут удалены только файлы самой программы. Модели, записи и настройки останутся на месте — следующим шагом вы сможете выбрать, что с ними сделать.

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

function PosFrom(const Sub, S: string; From: Integer): Integer;
begin
  Result := Pos(Sub, Copy(S, From, Length(S)));
  if Result > 0 then
    Result := Result + From - 1;
end;

function JsonValue(const Json, Key: string): string;
var
  p, a, b: Integer;
begin
  Result := '';
  p := Pos('"' + Key + '"', Json);
  if p = 0 then
    exit;
  p := PosFrom(':', Json, p);
  a := PosFrom('"', Json, p + 1);
  b := PosFrom('"', Json, a + 1);
  Result := Copy(Json, a + 1, b - a - 1);
  StringChange(Result, '\\', '\');
end;


procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  CfgPath, CfgJson: string;
  CfgAnsi: AnsiString;
  ModelsDir, RecDir, OllamaDir: string;
  DataForm: TSetupForm;
  ChkModels, ChkOllama, ChkRec, ChkCfg: TNewCheckBox;
  Btn: TNewButton;
  Y: Integer;
begin
  { CloseApplications=force cannot be relied on for a windowless tray app
    (verified live 2026-09-20: the process survived and its locked files
    were skipped) — kill it ourselves before file removal }
  if CurUninstallStep = usUninstall then
  begin
    Exec('taskkill.exe', '/F /IM amanu.exe', '', SW_HIDE, ewWaitUntilTerminated, Y);
    Sleep(1500);
    exit;
  end;

  { ask AFTER the program files are gone: the standard confirmation covers
    the bundle, this form covers only user data; silent uninstall deletes
    nothing and asks nothing }
  if (CurUninstallStep <> usPostUninstall) or UninstallSilent then
    exit;

  CfgPath := GetEnv('USERPROFILE') + '\.config\amanu\config.json';
  ModelsDir := ExpandConstant('{localappdata}\amanu\models');
  RecDir := ExpandConstant('{userdocs}\Amanu Recordings');
  if LoadStringFromFile(CfgPath, CfgAnsi) then
  begin
    CfgJson := String(CfgAnsi);
    if JsonValue(CfgJson, 'models_dir') <> '' then
      ModelsDir := JsonValue(CfgJson, 'models_dir');
    if JsonValue(CfgJson, 'recordings_dir') <> '' then
      RecDir := JsonValue(CfgJson, 'recordings_dir');
  end;
  OllamaDir := GetEnv('USERPROFILE') + '\.ollama';

  DataForm := CreateCustomForm(ScaleX(560), ScaleY(320), False, True);
  DataForm.Caption := 'Amanu — удаление данных';
  DataForm.ClientWidth := 560;
  DataForm.ClientHeight := 320;

  with TNewStaticText.Create(DataForm) do
  begin
    Parent := DataForm;
    Left := 12; Top := 10; Width := 536; Height := 34;
    Caption := 'Программа удалена. Отметьте, какие данные также удалить (по умолчанию всё сохраняется):';
    WordWrap := True;
  end;

  Y := 52;
  ChkModels := TNewCheckBox.Create(DataForm);
  ChkModels.Parent := DataForm;
  ChkModels.Left := 16; ChkModels.Top := Y; ChkModels.Width := 528;
  ChkModels.Caption := 'Модели распознавания';
  ChkModels.Checked := False;
  with TNewStaticText.Create(DataForm) do
  begin
    Parent := DataForm;
    Left := 34; Top := Y + 18; Width := 510; Height := 16;
    Caption := ModelsDir;
    WordWrap := True;
  end;

  Y := Y + 44;
  ChkOllama := TNewCheckBox.Create(DataForm);
  ChkOllama.Parent := DataForm;
  ChkOllama.Left := 16; ChkOllama.Top := Y; ChkOllama.Width := 528;
  ChkOllama.Caption := 'Данные Ollama и модель сводок';
  ChkOllama.Checked := False;
  with TNewStaticText.Create(DataForm) do
  begin
    Parent := DataForm;
    Left := 34; Top := Y + 18; Width := 510; Height := 16;
    Caption := OllamaDir + ' (саму программу Ollama удалите через «Приложения»)';
    WordWrap := True;
  end;

  Y := Y + 44;
  ChkRec := TNewCheckBox.Create(DataForm);
  ChkRec.Parent := DataForm;
  ChkRec.Left := 16; ChkRec.Top := Y; ChkRec.Width := 528;
  ChkRec.Caption := 'Записи и расшифровки';
  ChkRec.Checked := False;
  with TNewStaticText.Create(DataForm) do
  begin
    Parent := DataForm;
    Left := 34; Top := Y + 18; Width := 510; Height := 16;
    Caption := RecDir;
    WordWrap := True;
  end;

  Y := Y + 44;
  ChkCfg := TNewCheckBox.Create(DataForm);
  ChkCfg.Parent := DataForm;
  ChkCfg.Left := 16; ChkCfg.Top := Y; ChkCfg.Width := 528;
  ChkCfg.Caption := 'Настройки';
  ChkCfg.Checked := False;
  with TNewStaticText.Create(DataForm) do
  begin
    Parent := DataForm;
    Left := 34; Top := Y + 18; Width := 510; Height := 16;
    Caption := CfgPath;
    WordWrap := True;
  end;

  Btn := TNewButton.Create(DataForm);
  Btn.Parent := DataForm;
  Btn.Left := 560 - 12 - 75; Btn.Top := 320 - 12 - 23;
  Btn.Caption := 'OK';
  Btn.Default := True;
  Btn.ModalResult := mrOk;

  DataForm.ShowModal;

  if ChkModels.Checked then
    DelTree(ModelsDir, True, True, True);
  if ChkOllama.Checked then
    DelTree(OllamaDir, True, True, True);
  if ChkRec.Checked then
    DelTree(RecDir, True, True, True);
  if ChkCfg.Checked then
    DelTree(GetEnv('USERPROFILE') + '\.config\amanu', True, True, True);
end;
