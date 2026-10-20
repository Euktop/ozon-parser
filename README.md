# Ozon Parser

[![CI](https://github.com/Euktop/ozon-parser/actions/workflows/ci.yml/badge.svg)](https://github.com/Euktop/ozon-parser/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Кроссплатформенный парсер страниц товара Ozon → Markdown.
Собирает скопированные страницы в локальную коллекцию (SQLite) и выдаёт всё сразу по команде.

## Идея

Парсер разделён на три слоя:

- **Приём** — клипборд (Windows/macOS/Linux), bookmarklet, расширение, HTTP-API.
- **Хранение** — SQLite `~/.ozon-parser/collection.db`. Дедупликация по SKU.
- **Выдача** — по явной команде `ozon-parser flush`, в stdout/clipboard/файл, в Markdown/JSONL/CSV.

По умолчанию буфер обмена **не затирается** после парсинга.

## Статус

**0.4.0** (октябрь 2026). Все P0–P4 из `ARCHITECTURE.md` закрыты.
Архитектура — 4 слоя CA: `domain` / `application` / `infrastructure` /
`presentation`. **330+ автотестов**, включая 7 архитектурных (правило
зависимостей) и регрессии на 10 багов.

Что нового в 0.4.0:

- **Native Messaging** — расширение запускает Python-ядро через stdio
  (Chrome сам поднимает host-процесс), без фонового `serve` и без порта
  в `netstat`. Fallback на HTTP — если host не зарегистрирован.
- **Installer** — `ozon-parser-setup.exe` (Inno Setup), one-click
  без wizard'а. Extension ID стабилен (`bmdjfjhckhfifhdnohiojamggfnlbcgf`):
  зафиксирован через `key` в `manifest.json`, генерируется
  `build/gen_extension_key.py`.
- **IngestHtmlUseCase** / **ReparseUseCase** — единая точка ingest,
  без тройного дублирования parse+quality+upsert.
- **Extension: popup** — список товаров, удаление, выгрузка (MD/JSONL/CSV),
  очистка, ingest текущей. Работает без консоли.
- **Токен-аутентификация убрана.** Локальный API доступен без токена.
  Защита — CORS (только `ozon.ru`) и отказ от `to:file` в `/flush`.
- **HTTP API сужен:** `/flush` принимает только `{format}`, отвечает
  `text/plain`. Запись файлов на сервере (`to:file`) убрана — extension
  сохраняет через `chrome.downloads`.
- **CORS** — `Access-Control-Allow-Origin: https://www.ozon.ru` (был `*`).
- **Windows:** `allow_reuse_address=False` — второй `serve` на занятом порту
  падает с `WinError 10048`, а не создаёт зомби в LISTENING.

## Установка

### Windows: двойной клик

1. Скачай `ozon-parser-setup.exe` (или собери: `build\build.bat` + `iscc installer\install.iss`)
2. Двойной клик → «Установить» (wizard без шага Extension ID — ID
   захардкожен в `_extension_id.py`)
3. `chrome://extensions` → Load unpacked → папка `extension/`
4. Открой Ozon PDP → товар в popup

**Никакого cmd.** Расширение запускает Python-ядро само через Native Messaging, без фонового `serve`.

### Dev / macOS / Linux

    python -m venv .venv
    .venv\Scripts\activate          # Windows
    pip install -e .[dev]
    python build/gen_extension_key.py   # один раз — генерирует RSA key + ID
    ozon-parser native register          # ID берётся из _extension_id.py

## Запуск

    ozon-parser --version
    ozon-parser --help

## Быстрый старт с bookmarklet (1 клик вместо Ctrl+A/Ctrl+C)

1. Запустить HTTP-сервер:

       ozon-parser serve --port 8765

2. Сгенерировать букмарклет и скопировать в буфер:

       ozon-parser bookmarklet --to clipboard

3. Добавить закладку в браузере:
   - имя: `→ Ozon`
   - URL: вставить из буфера (`javascript:(async()=>{...})()`)

4. Открыть страницу товара Ozon и нажать на закладку.
   Букмарклет тянет полный HTML через `fetch(location.href)`
   (source, с `data-server-rendered="true"`) и отправляет
   на `http://127.0.0.1:8765/ingest`.

5. `ozon-parser list` / `flush` — товар уже в коллекции.

> **Замечание про cmd.exe.** В примерах `#` — комментарий (PowerShell, bash).
> В `cmd.exe` `#` не является комментарием: строка `ozon-parser count # 0`
> будет воспринята как `ozon-parser count` + лишние аргументы `#` и `0`,
> и argparse упадёт. Копируйте только команду, без `# ...`.

## Диагностика

Если `ozon-parser once` отвечает `skipped`, но ты уверен, что в буфере Ozon PDP:

    ozon-parser read

Покажет: бэкенд, длины text/html, результаты `detect_copy_type` /
`looks_like_ozon_product_page` / `is_usable_ozon_copy`, наличие маркеров
(`id="__ozon"`, `data-server-rendered="true"`, `data-widget="webproductmainwidget"` и т.п.)
и первые 300 символов каждого payload'а.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `OZON_PARSER_CONFIG` | Путь к TOML-конфигу. Переопределяет `%APPDATA%\ozon-parser\config.toml`. |
| `OZON_PARSER_DB` | (TODO) Путь к SQLite. Пока не реализовано — используйте флаг `--db` в каждой команде или настройте алиас. |

## Автозапуск (фоновый сервис)

Чтобы `serve` (для bookmarklet/extension) и/или `daemon` (опрос клипборда)
запускались сами при логине:

    ozon-parser service install --mode serve     REM HTTP для extension
    ozon-parser service install --mode daemon    REM опрос клипборда

    ozon-parser service status                   REM показать оба
    ozon-parser service uninstall --mode serve   REM удалить

Под капотом:

| ОС | Механизм | Где |
|---|---|---|
| Windows | Startup folder + `.vbs` | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ozon-parser-{mode}.vbs` |
| macOS | launchd | `~/Library/LaunchAgents/com.ozon-parser.{mode}.plist` |
| Linux | systemd `--user` | `~/.config/systemd/user/ozon-parser-{mode}.service` |

После `install` сервис запускается сразу — не ждём перезагрузки.

**Логи** (по умолчанию пишутся автоматически при `service install`):

| ОС | Путь |
|---|---|
| Windows | `%LOCALAPPDATA%\ozon-parser\{mode}.log` |
| macOS | `~/Library/Logs/ozon-parser-{mode}.log` |
| Linux | `${XDG_STATE_HOME:-~/.local/state}/ozon-parser/{mode}.log` |

Флаги:

- `serve --log-file PATH` / `daemon --log-file PATH` — переопределить путь
  (для прямого запуска из CLI, без `service`).
- `service install --log-file PATH` — переопределить путь при установке сервиса.
- `service install --no-log-file` — отключить файловый лог (только stderr).
  Для `serve` в консоли это норм; для `daemon` под Windows — нет, stderr
  теряется при `wscript.exe`.

Посмотреть лог: `type "%LOCALAPPDATA%\ozon-parser\daemon.log"` (Windows),
`tail -f ~/Library/Logs/ozon-parser-daemon.log` (macOS),
`tail -f ~/.local/state/ozon-parser/daemon.log` (Linux).

**Windows:** используется `%APPDATA%\...\Startup` — не требует прав
администратора. `schtasks /create /sc onlogon` **не используется**: он
пишет в системный Task Scheduler Library и падает с `Access is denied`
у обычного пользователя. Если нужен restart-on-crash — создайте задачу
в Task Scheduler вручную (admin) с `Restart on failure`.

## Расширение (Manifest V3)

Если bookmarklet кажется ручным — есть браузерное расширение:

    REM chrome://extensions → Developer mode → Load unpacked → extension/
    REM HTTP-сервер не нужен: расширение работает через Native Messaging.
    REM `ozon-parser serve` — только для bookmarklet / Web UI.

**Клик по иконке** открывает popup:

- список товаров с кнопкой `×` для удаления
- кнопки «MD / JSONL / CSV» — скачать коллекцию (через браузер,
  файл падает в «Загрузки»)
- «Очистить всё» — с подтверждением
- «Ingest текущей» — отправить PDP активной вкладки
- ⚙ — настройки (порт, auto-ingest)

`Alt+Shift+O` — ingest текущей PDP (hotkey). Правой кнопкой → «→ Ozon Parser».
Badge на иконке — число товаров.

В настройках (⚙ в popup, или правой кнопкой по иконке → Options):

- **Порт** (по умолчанию 8765) — куда `serve` слушает (fallback,
  если native host не зарегистрирован).
- **Auto-ingest** — при заходе на `/product/...` расширение само
  отправит HTML. Opt-in. Уведомление после auto-ingest — отдельная
  галочка, показывается только для активной вкладки.

Подробности — `extension/README.md`.

## HTTP API

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/health` | `{"ok": true}` |
| GET | `/config` | `{http_port, default_format, ...}` для extension |
| POST | `/ingest?url=...` | body = HTML, parse + upsert |
| GET | `/items` | список SKU |
| GET | `/items/{sku}` | Markdown одного товара |
| POST | `/flush` | `{format}`, `only?` → `text/plain` |
| DELETE | `/items/{sku}` | удалить |
| OPTIONS | `*` | CORS preflight |

`POST /flush` отвечает **только** `text/plain` (формат из `format`).
Запись файла на диск сервера (`to:file`) и управление буфером сервера
(`to:clipboard`) в HTTP **убраны** — это CLI-операции
(`ozon-parser flush -t FILE`). Extension получает текст в ответе и
сохраняет через `chrome.downloads`.

**CORS.** `Access-Control-Allow-Origin: https://www.ozon.ru` (не `*`).
Bookmarklet работает, любой другой сайт получает CORS-блок на fetch
к localhost. Extension обходит CORS через `host_permissions`.


## Удаление отдельных товаров

    ozon-parser rm 3184585029               REM refuses without --yes
    ozon-parser rm 3184585029 --yes         REM delete one
    ozon-parser rm 111 222 --yes            REM delete several (positional)
    ozon-parser rm 111,222 --yes            REM delete several (comma)
    ozon-parser rm 111,222 333 --yes        REM mix

Позиционные SKU и запятые комбинируются. Дубликаты отбрасываются.
Если хоть один SKU не найден — код возврата 1, остальные всё равно
удаляются (вывод: `deleted: 111,222` / `not found: 333`).

`to` в `/flush` (HTTP): **только `response`**. `to:file` / `to:stdout` /
`to:clipboard` доступны из CLI (`ozon-parser flush -t FILE`), но не из
HTTP — иначе злой сайт мог бы записать `.bat` в Startup folder
(arbitrary file write vector). Extension получает текст в ответе и
сохраняет через `chrome.downloads`.

## Лицензия

MIT
