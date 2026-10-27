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

[Files]
Source: "..\dist\ozon-parser.exe";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\ozon-parser-native.exe"; DestDir: "{app}"; Flags: ignoreversion
; .crx и распакованная копия — fallback, если ForceList заблокирован
; корпоративной политикой. skipifsourcedoesntexist: если Chrome не был
; найден при build.bat, установка всё равно продолжается.
Source: "..\dist\extension.crx";          DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\extension\*";                 DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName} (CLI)"; Filename: "{cmd}"; Parameters: "/k {app}\ozon-parser.exe --help"; WorkingDir: "{app}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"

[Run]
; Регистрация native host. Extension ID берётся из _extension_id.py
; (сгенерирован build/gen_extension_key.py). Без wizard — двойной клик и готово.
Filename: "{app}\ozon-parser.exe"; Parameters: "native register"; Flags: runhidden waituntilterminated; StatusMsg: "Регистрация Native Messaging host..."

[UninstallRun]
Filename: "{app}\ozon-parser.exe"; Parameters: "native unregister"; Flags: runhidden waituntilterminated; RunOnceId: "NativeUnregister"

; Extension ID захардкожен в ozon_parser/native/_extension_id.py
; (генерируется build/gen_extension_key.py). Installer вызывает
; `ozon-parser native register` без аргументов — он читает ID из файла.
; Wizard не нужен: двойной клик → Установить → готово.

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Открываем проводник с папкой расширения
    ShellExec('open', 'explorer.exe', ExpandConstant('{app}\extension'), '', SW_SHOWNORMAL, ewNoWait, ErrorCode);

    // Пытаемся открыть страницу расширений в браузере по умолчанию
    ShellExec('open', 'chrome://extensions', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);

    // Показываем финальную инструкцию (без привязки к Chrome)
    MsgBox(
      'Остался последний шаг!' + #13#10 + #13#10 +
      'Мы открыли папку с расширением в проводнике.' + #13#10 + #13#10 +
      '1. Откройте в браузере страницу настроек расширений:' + #13#10 +
      '   Chrome: chrome://extensions' + #13#10 +
      '   Яндекс: browser://extensions' + #13#10 +
      '   Edge: edge://extensions' + #13#10 + #13#10 +
      '2. Включите тумблер «Режим разработчика» (справа вверху).' + #13#10 +
      '3. Нажмите «Загрузить распакованное» (Load unpacked).' + #13#10 +
      '4. Выберите папку, которая уже открыта в проводнике.' + #13#10 + #13#10 +
      'Иконка «O» появится на панели браузера!',
      mbInformation, MB_OK
    );
  end;
end;
