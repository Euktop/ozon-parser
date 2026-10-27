; Inno Setup script: ozon-parser-setup.exe
;
; Собирает: .exe (из build/), регистрирует native host, опционально
; ставит serve в автозапуск (--mode serve через существующий .vbs).
;
; Сборка: iscc installer/install.iss
; Требует: Inno Setup 6 (https://jrsoftware.org/isinfo.php)

#define AppName "Ozon Parser"
#define AppVersion "0.5.1"
#define AppPublisher "Ozon Parser"
#define AppExeName "ozon-parser.exe"
#define ExtensionId "bmdjfjhckhfifhdnohiojamggfnlbcgf"
; Chrome/Yandex/Edge автозагружают extension из этого манифеста. Файл
; лежит в репозитории (extension/updates.xml) и указывает на .crx,
; который публикуется как asset GitHub Release.
#define ExtensionUpdateUrl "https://raw.githubusercontent.com/Euktop/ozon-parser/main/extension/updates.xml"

[Setup]
AppId={{8D8A7B9C-4F1E-4D3A-9B2C-7E6F5A4D3C2B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\ozon-parser
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputBaseFilename=ozon-parser-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "autostart"; Description: "Запускать HTTP-сервер при входе в систему (для bookmarklet / Web UI)"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\dist\ozon-parser.exe";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\ozon-parser-native.exe"; DestDir: "{app}"; Flags: ignoreversion
; .crx и распакованная копия — fallback, если ForceList заблокирован
; корпоративной политикой. skipifsourcedoesntexist: если Chrome не был
; найден при build.bat, установка всё равно продолжается.
Source: "..\dist\extension.crx";          DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\extension\*";                 DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; ForceList: Chrome/Edge/Yandex/Brave/Chromium/Vivaldi — установят extension
; при следующем старте. Пишем в HKCU (без админа). uninsdeletevalue — при
; удалении снимается только наша запись, чужие force-list записи не трогаем.
; Chrome сверяет подпись .crx с manifest.key — у нас совпадает
; (build/gen_extension_key.py), поэтому установка проходит.
Root: HKCU; Subkey: "Software\Google\Chrome\ExtensionInstallForcelist";       ValueType: string; ValueName: "1"; ValueData: "{#ExtensionId};{#ExtensionUpdateUrl}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Edge\ExtensionInstallForcelist";      ValueType: string; ValueName: "1"; ValueData: "{#ExtensionId};{#ExtensionUpdateUrl}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Yandex\YandexBrowser\ExtensionInstallForcelist"; ValueType: string; ValueName: "1"; ValueData: "{#ExtensionId};{#ExtensionUpdateUrl}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\BraveSoftware\Brave-Browser\ExtensionInstallForcelist"; ValueType: string; ValueName: "1"; ValueData: "{#ExtensionId};{#ExtensionUpdateUrl}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Chromium\ExtensionInstallForcelist";            ValueType: string; ValueName: "1"; ValueData: "{#ExtensionId};{#ExtensionUpdateUrl}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Vivaldi\ExtensionInstallForcelist";             ValueType: string; ValueName: "1"; ValueData: "{#ExtensionId};{#ExtensionUpdateUrl}"; Flags: uninsdeletevalue

[Icons]
Name: "{group}\{#AppName} (CLI)"; Filename: "{cmd}"; Parameters: "/k {app}\ozon-parser.exe --help"; WorkingDir: "{app}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"

[Run]
; Регистрация native host. Extension ID берётся из _extension_id.py
; (сгенерирован build/gen_extension_key.py). Без wizard — двойной клик и готово.
Filename: "{app}\ozon-parser.exe"; Parameters: "native register"; Flags: runhidden waituntilterminated; StatusMsg: "Регистрация Native Messaging host..."
; Опционально — автозапуск serve.
Filename: "{app}\ozon-parser.exe"; Parameters: "service install --mode serve"; Flags: runhidden waituntilterminated; Tasks: autostart; StatusMsg: "Установка автозапуска..."

[UninstallRun]
Filename: "{app}\ozon-parser.exe"; Parameters: "native unregister"; Flags: runhidden waituntilterminated; RunOnceId: "NativeUnregister"
Filename: "{app}\ozon-parser.exe"; Parameters: "service uninstall --mode serve"; Flags: runhidden waituntilterminated; RunOnceId: "ServiceUninstall"

; Extension ID захардкожен в ozon_parser/native/_extension_id.py
; (генерируется build/gen_extension_key.py). Installer вызывает
; `ozon-parser native register` без аргументов — он читает ID из файла.
; Wizard не нужен: двойной клик → Установить → готово.

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    MsgBox(
      'Готово!' + #13#10 + #13#10 +
      'Закройте все окна браузера (Chrome, Яндекс, Edge) и откройте заново — ' +
      'расширение установится автоматически.' + #13#10 + #13#10 +
      'Если иконки «O» нет: откройте chrome://extensions, включите ' +
      '«Режим разработчика» и перетащите extension.crx из папки: ' +
      ExpandConstant('{app}'),
      mbInformation, MB_OK
    );
  end;
end;
