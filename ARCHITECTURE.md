# Архитектура ozon-parser

## Цели

1. **Кроссплатформенность** — Windows, macOS, Linux. Работает «из коробки» через один из трёх транспортов: клипборд, bookmarklet, расширение.
2. **Быстрый приём** — событийный листенер клипборда там, где возможно; HTTP-эндпоинт для bookmarklet/расширения.
3. **Накопление** — все распарсенные товары складываются в SQLite, дедупликация по SKU.
4. **Явная выдача** — буфер обмена не трогается без команды `flush`. Выгрузка в stdout/clipboard/файл, в Markdown/JSONL/CSV.

## Слои

    ┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
    │  Input           │  →    │  Store           │  →    │  Output          │
    │  clipboard       │       │  SQLite          │       │  flush →         │
    │  bookmarklet     │       │  ~/.ozon-parser/ │       │  stdout / clip / │
    │  extension       │       │  collection.db   │       │  file / dir      │
    │  HTTP POST       │       │  (dedup by SKU)  │       │  md / jsonl / csv│
    └──────────────────┘       └──────────────────┘       └──────────────────┘

## Структура пакета

    ozon_parser/
    ├── __init__.py
    ├── __main__.py
    │
    ├── domain/                 # слой 1: чистая логика, без I/O
    │   ├── detection.py        # detect_copy_type, looks_like, is_usable
    │   ├── quality.py          # quality_score (0..9)
    │   └── ports.py            # Protocol: HtmlParser, ProductRepository, Notifier
    │
    ├── application/            # слой 2: use cases (оркестрация)
    │   ├── ingest.py           # IngestHtmlUseCase — единая точка ingest
    │   └── reparse.py          # ReparseUseCase — 2-фазный streaming
    │
    ├── infrastructure/         # слой 3: адаптеры портов
    │   ├── parsing/ozon.py     # OzonHtmlParser (parse_product → .parse())
    │   ├── persistence/sqlite.py  # SqliteProductRepository (Store)
    │   └── notifier.py         # DesktopNotifier
    │
    ├── presentation/           # слой 4: драйверы
    │   ├── cli.py              # argparse, подкоманды
    │   ├── daemon.py           # опрос клипборда
    │   ├── server.py           # HTTP API
    │   └── render.py           # render_collection (md / jsonl / csv), per-sku
    │
    ├── bookmarklet.py          # генератор JS-закладки
    ├── config.py               # TOML-конфиг (APPDATA / XDG_CONFIG)
    ├── parser.py               # collect_product, parse_product, render_markdown
    ├── store.py                # SQLite upsert/list/get/delete/iter_all/iter_with_raw
    ├── notifier.py             # desktop toast (win/mac/linux)
    ├── logging_utils.py        # setup_logging / default_log_path
    ├── service.py              # автозапуск (Startup/launchd/systemd)
    └── clipboard/
        ├── __init__.py         # get_backend()
        ├── base.py
        ├── win.py              # pywin32
        ├── mac.py              # pbpaste + osascript
        └── linux.py            # wl-paste / xclip / xsel

    Правило зависимостей (проверяется tests/test_architecture.py):
    domain → stdlib only
    application → domain
    infrastructure → domain + application + (на время миграции) top-level
    presentation → application (+ domain для типов)

## Roadmap

### P0 — скелет и базовая ценность

- [x] Структура проекта, pyproject, тесты, фикстуры
- [x] `detection.py` — починить `detect_copy_type` (source = `data-server-rendered="true"`)
- [x] `store.py` — SQLite с дедупликацией по SKU
- [x] `cli.py` — подкоманды: `list`, `show`, `flush`, `count`, `clear` (P0.3)
- [x] `daemon.py` + `once` / `daemon` — один проход и фоновый цикл по буферу (P0.5)
- [x] `parser.py` (все extract_*, collect_product, render_markdown) — legacy, две правки: `_clean_desc_node` без мутации soup + `extract_marketing_labels_dom` regex по версиям класса

### P1 — кроссплатформенность и скорость

- [x] `clipboard/` — win32 / mac / linux бэкенды (P1.2)
- [x] `server.py` — HTTP `/health`, `/ingest`, `/items`, `/items/{sku}`, `/flush`, `DELETE` (P1.1)
- [x] Bookmarklet — генератор JS, тянет source через `fetch(location.href)` (P1.1)
- [x] Расширение (Manifest V3) — P1.3
  - [x] Auto-ingest (opt-in, tabs.onUpdated, дебаунс 3s, дедуп 60s) — P1.3b
  - [x] Notify после auto-ingest (опция, только для активной вкладки) — P1.3c
- [ ] Событийный листенер клипборда (AddClipboardFormatListener / changeCount / xclip -l 1)
- [x] `config.py` — TOML-конфиг (P1.3)
- [x] `read` — диагностика клипборда (P1.4)

### P2 — полировка

- [x] `notifier.py` — desktop toast (P2.2)
- [x] `reparse` — хранить raw_html (gzip), перепарсить по команде (P2.1)
- [x] `export-fixture` — выгрузка raw_html в tests/fixtures (P2.1)
- [x] Слаг-файлы, YAML frontmatter, `--dry-run`, `--quiet` (P2.3)
- [x] `flush --per-sku` — отдельные .md с frontmatter (P2.3)
- [x] `export-fixture --latest` / `--all` (P2.3)
- [x] Автозапуск: `service install --mode {serve,daemon}` (P2.4)
  — Windows: Startup folder + `.vbs` (без админа); macOS: `launchd`; Linux: `systemd --user`
- [x] `--log-file` для serve/daemon (P2.5) — DEBUG в файл, чтобы `wscript` не терял stderr
- [x] Тесты на фикстурах source.html / rendered.html (P2.1) — 138 passed

### P3 — Clean Architecture рефакторинг (0.2.0)

- [x] `domain/` — detection, quality, ports (Protocol: HtmlParser /
      ProductRepository / Notifier)
- [x] `application/` — `IngestHtmlUseCase`, `ReparseUseCase`
- [x] `infrastructure/` — `SqliteProductRepository`, `OzonHtmlParser`,
      `DesktopNotifier` (адаптеры портов)
- [x] `presentation/` — `cli`, `daemon`, `server` зовут use case вместо
      дублирования parse+quality+upsert
- [x] 7 архитектурных тестов (`test_architecture.py`) — правило
      зависимостей через AST, ноль внешних зависимостей
- [x] B1–B9 + S1–S2 закрыты, 23 регрессионных теста
- [x] B10 (`allow_reuse_address=False` на Windows) — второй `serve` на
      занятом порту падает с `WinError 10048`, не создаёт зомби
- [x] HTTP `/flush {to:file}` убран — arbitrary file write vector;
      extension сохраняет через `chrome.downloads`
- [x] CORS `*` → `https://www.ozon.ru`
- [x] Extension 0.2.0: popup (list/delete/download/clear)
- [x] **339 passed**

### P4 — Native Messaging (0.4.0)

- [x] `presentation/native_host.py` — stdio-цикл, dispatch в use cases
- [x] `native/manifest.py` + `native/register.py` — регистрация во всех
      Chromium-браузерах (Chrome, Edge, Yandex, Brave, Chromium, Vivaldi)
- [x] `cli.py native register/unregister --extension-id` — ручная регистрация
- [x] Extension: `nativeCall` → HTTP fallback; popup через `sendMessage`
- [x] PyInstaller: `ozon-parser.exe` + `ozon-parser-native.exe`
- [x] Inno Setup: `ozon-parser-setup.exe` с wizard'ом extension ID
- [x] **339 passed**

## CLI

    ozon-parser daemon [--foreground] [--port 8765]
    ozon-parser once                 # один раз из клипборда (legacy --once)
    ozon-parser read                 # диагностика клипборда (legacy --read)
    ozon-parser list                 # список SKU в коллекции
    ozon-parser show SKU             # один товар в stdout
    ozon-parser flush [--to stdout|clipboard|FILE|DIR]
                       [--format md|jsonl|csv]
                       [--since DATE] [--only SKU[,SKU]]
    ozon-parser reparse [--since DATE]
    ozon-parser rm SKU [SKU ...] [--yes]   # удалить один/несколько SKU
    ozon-parser clear [--yes]
    ozon-parser config edit|show

## Схема SQLite

    CREATE TABLE items (
        sku         TEXT PRIMARY KEY,
        url         TEXT,
        title       TEXT,
        brand       TEXT,
        price       TEXT,
        rating      TEXT,
        seller      TEXT,
        copy_type   TEXT,          -- source | rendered
        quality     INTEGER,
        markdown    TEXT NOT NULL,
        json        TEXT NOT NULL,
        html_hash   TEXT,
        raw_html    BLOB,          -- gzip, опционально
        created_at  TEXT,
        updated_at  TEXT
    );
    CREATE INDEX items_created ON items(created_at DESC);

## HTTP API (для расширения и bookmarklet)

| Метод  | Путь            | Назначение                                       |
|--------|-----------------|--------------------------------------------------|
| GET    | /health         | пинг                                             |
| GET    | /config         | `{http_port, default_format, ...}` для extension |
| POST   | /ingest         | body = HTML, `?url=` опционально                 |
| GET    | /items          | список `[{sku, title, quality, created_at}]`     |
| GET    | /items/{sku}    | полный Markdown                                  |
| POST   | /flush          | `{format}`, `only?` → text/plain                 |
| DELETE | /items/{sku}    | удалить                                          |

**Аутентификация отсутствует** (убрана в 0.3.0). Локальный API доступен
без токена. Защита — CORS (`Access-Control-Allow-Origin: https://www.ozon.ru`)
и отказ от `to:file` в `/flush`.

**Ограничения HTTP API (0.2.0).** В HTTP доступны только:
`/ingest`, `/health`, `/config`, `/items`, `/items/{sku}`, `/flush`
(`format` → text/plain), `DELETE /items/{sku}`. Запись файла на диск
сервера (`to:file`) и управление буфером сервера (`to:clipboard`)
убраны из HTTP — это CLI-операции. Иначе любой сайт через
`fetch('http://127.0.0.1:8765/flush', {to:'file', path:'...'})`
писал бы произвольные файлы (например, `.bat` в Startup folder).

**CORS.** `Access-Control-Allow-Origin: https://www.ozon.ru` (не `*`).
Bookmarklet работает в page-origin ozon.ru. Extension обходит CORS
через `host_permissions`. Любой другой сайт получает CORS-блок.

## Соглашения

- **`argparse` и `%`.** Help-строки в `argparse` форматируются через `%`-оператор. Любой литеральный `%` в `help=` (например, `%LOCALAPPDATA%`) валит `argparse` с `ValueError: badly formed help string`. Экранируйте как `%%` или пишите без `%`.
- **`raw_html` — только в БД.** Сырой HTML хранится в `items.raw_html` (bytes | NULL) и не должен попадать в `flush` (markdown / jsonl / csv). Фильтруется в `render._jsonable` по имени ключа, а не по типу.
- **`Store` — thread-safe.** `ThreadingHTTPServer` использует новый поток на каждый HTTP-запрос, а `Store` один. `sqlite3.connect(..., check_same_thread=False)` + `threading.RLock` вокруг операций + `PRAGMA busy_timeout = 5000`. Без этого handler падает с `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`, и клиент получает `RemoteDisconnected`. `iter_all()` материализует `fetchall()` под локом, а не отдаёт курсор наружу. Регрессия: `test_store_is_thread_safe`.
- **`_clean_desc_node` — без мутации soup.** Legacy использовал `copy(section)` (shallow), который шарил `contents` с оригиналом: `decompose()` на дочернем узле удалял его и из `soup`. Сейчас пересобираем дерево через `BeautifulSoup(str(section), 'html.parser')`. Есть регрессионный тест `test_clean_desc_node_does_not_mutate_soup`.
- **Docstrings и `\`.** Обычная строка `"""…"""` интерпретирует escape-последовательности: `\d` в тексте docstring даёт `SyntaxWarning` (Python 3.12+) и станет `SyntaxError` в будущем. Если в docstring есть backslash (regex, windows-пути) — делайте её raw: `r"""…"""`. Регрессионный тест: `test_parser_module_has_no_invalid_escape_sequences`.
- **`daemon.py`: дедуп только «подряд».** Хэш сравнивается с последним обработанным poll'ом. Если между двумя копиями одного PDP в буфер попадало что-то ещё — PDP переобработается и обновит запись. Это правильно: цена/наличие могли измениться.
- **`daemon.py` не затирает буфер.** В отличие от legacy, после разбора буфер обмена не трогается. Копирование в буфер — только явно через `flush --to clipboard`.
- **`extract_nuxt_state` не портирован.** Legacy парсил мегабайты JSON, результат шёл только в неиспользуемый `source_has_nuxt`. Оставлена строковая проверка. Если понадобится — вернём в `extractors/nuxt.py`.
- **Версионно-зависимые CSS-классы Ozon.** `.b5_7_10-a4` (v10 ui-kit-badge), `ol.ce8_7` (breadcrumbs) и т.п. меняются между релизами Ozon. В `extract_marketing_labels_dom` используем regex `^b5_7_\d+-a4$`. Для breadcrumbs — fallback помечен комментарием.
- **`id="section-characteristics"` — не всегда в DOM.** На некоторых PDP характеристики приходят только через `webShortCharacteristics` (client-state JSON), без DOM-секции. `collect_product` сначала пробует `extract_full_characteristics_dom` (`#section-characteristics`), при пустом результате падает на `webShortCharacteristics`. Регрессионный тест на реальном source.html не должен требовать наличия `#section-characteristics` — он проверяет отсутствие мутации soup (`str(soup)` до/после идентичен).
- **CF_HTML при Ctrl+U-копии.** Chrome при `Ctrl+U → Ctrl+A → Ctrl+C` кладёт source HTML в **plain-text** (CF_UNICODETEXT). CF_HTML-формат содержит `<table>` с экранированным текстом `<pre>`-выделения — **это не сырой документ**, а фрагмент. `detect_copy_type(html)` вернёт `unknown` — норма, а не баг. `collect_candidates` фильтрует такой html через `is_usable_ozon_copy`, парсер берёт source из plain-text. `ozon-parser read` показывает это наглядно (`text.detect_copy_type: source`, `html.detect_copy_type: unknown` + `html.note: CF_HTML fragment`).
- **Extension: fetch из SW → 403.** Ozon WAF режет `fetch(tab.url)` из service worker'а — это XHR-like запрос (`Sec-Fetch-Dest: empty`), а не навигация. Решение: `chrome.scripting.executeScript({world: "MAIN"})` — page-context fetch с заголовками страницы; fallback — `document.documentElement.outerHTML` (rendered DOM, без сети). Парсер обрабатывает rendered с тем же quality 9/9. В нотификации виден метод: `(page-fetch)` = source, `(rendered-dom)` = rendered.

## Миграция с legacy

Оригинальный `ozon_clip.py` (v7) — монолит. Логика парсинга переносится в `parser.py` и `extractors/` **без изменений**, чтобы поведение сохранилось. Ломается только одно: `run()` больше не пишет в клипборд автоматически — вместо этого вызывает `store.upsert()`.

Оригинал полезно сохранить в `legacy/ozon_clip.py` (не коммитится в пакет, только как reference).
