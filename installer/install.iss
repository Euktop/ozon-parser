; Inno Setup script: ozon-parser-setup.exe
;
; Собирает: .exe (из build/), регистрирует native host, опционально
; ставит serve в автозапуск (--mode serve через существующий .vbs).
;
; Сборка: iscc installer/install.iss
; Требует: Inno Setup 6 (https://jrsoftware.org/isinfo.php)

#define AppName "Ozon Parser"
#define AppVersion "0.4.0"
#define AppPublisher "Ozon Parser"
#define AppExeName "ozon-parser.exe"

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
