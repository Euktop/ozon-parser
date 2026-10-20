# Native Messaging host

Позволяет расширению общаться с Python-ядром **без HTTP-сервера**: Chrome сам запускает `ozon-parser-native.exe` как дочерний процесс и обменивается сообщениями через stdio.

## Зачем

- Не нужен фоновый `serve`
- Нет порта `:8765` в `netstat`
- Нет фонового процесса между использованиями (Chrome убивает host при disconnect)
- Не видно в Task Manager, когда extension неактивен

`serve` остаётся как опциональный транспорт для bookmarklet и Web UI.

## Установка (Windows)

1. Скачать `ozon-parser-setup.exe` (или собрать самому — см. ниже)
2. Двойной клик → «Установить»
3. В мастере вставить **Extension ID** из `chrome://extensions`
4. Load unpacked extension (или из Chrome Web Store, когда будет)
5. Открыть Ozon PDP → товар в popup

## Ручная регистрация (dev)

```cmd
pip install -e .
ozon-parser native register --extension-id <ID>
```

Регистрирует во **все Chromium-браузеры** (Chrome, Edge, Yandex, Brave, Chromium, Vivaldi). Для каждого пишет:

- Windows: `HKCU\Software\<browser>\NativeMessagingHosts\com.ozon_parser` = путь к `com.ozon_parser.json`
- macOS/Linux: `~/.config/<browser>/NativeMessagingHosts/com.ozon_parser.json`

Сам `com.ozon_parser.json` (Windows) — в `%LOCALAPPDATA%\ozon-parser\native\`.

Удалить: `ozon-parser native unregister`.

## Отладка

Chrome пишет stderr native host в свой лог. На Windows:

```
%LOCALAPPDATA%\Google\Chrome\User Data\chrome_debug.log
```

Запусти Chrome с `--enable-logging=stderr --v=1`, если нужно подробнее.

Проверить host без Chrome:

```cmd
rem Windows: отправить health через pipe (framing: 4-byte length + JSON)
python -c "import struct,sys; msg=json.dumps({'action':'health'}).encode(); sys.stdout.buffer.write(struct.pack('<I',len(msg))+msg)" | ozon-parser-native
```

Выведет бинарный ответ. Прочитать:

```cmd
python -c "import struct,sys; d=sys.stdin.buffer.read(4); n=struct.unpack('<I',d)[0]; print(sys.stdin.buffer.read(n).decode())" < ответ
```

## Сборка .exe

```cmd
build\build.bat
```

Результат: `dist\ozon-parser.exe` (CLI) и `dist\ozon-parser-native.exe` (host).

Installer:

```cmd
iscc installer\install.iss
```

Результат: `installer\Output\ozon-parser-setup.exe`.

## Протокол

См. `presentation/native_host.py` — docstring модуля. Действия:

| action | body | response |
|---|---|---|
| `health` | — | `{ok, count}` |
| `items.list` | — | `{items: [...]}` |
| `items.get` | `{sku}` | `{item: dict}` |
| `items.delete` | `{sku}` | `{deleted, sku}` |
| `ingest` | `{html, url?}` | `{status, sku, quality, ...}` |
| `flush` | `{format}` | `{text, format, count}` |

Поле `id` (опционально) — эхо в ответе для матчинга.

## Fallback на HTTP

`background.js` сначала пробует `nativeCall()`, при `null` (host не зарегистрирован / crashed / timeout 5s) — `httpCall()` на `http://127.0.0.1:<port>`. Это позволяет работать bookmarklet'у и Web UI, а пользователю с `serve` — не трогать native.
