# Changelog

Все заметные изменения проекта. Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## [Unreleased]

### Добавлено

- 

### Изменено

- 

### Исправлено

- 

## [0.6.1] — 2026-10-27

### Удалено

- `extension.crx` из release assets и `install.iss` — Chrome 137+ блокирует
  drag-drop .crx, ForceList на домашних ПК не работает, «Load unpacked»
  из папки `extension/` — единственный работающий путь. `.crx` был мёртвым
  грузом (никем не использовался в install flow).
- `extension/updates.xml` — обслуживал только ForceList (который удалён
  в 0.6.0). Ссылка на .crx в GitHub Release больше не нужна.
- `build/pack_crx.py` и шаг `[3/3]` из `build.bat` — упаковка .crx больше
  не нужна. `chrome --pack-extension` удалён в Chrome 137+.

### Добавлено

- 

### Изменено

- 

### Исправлено

- 

## [0.6.0] — 2026-10-27

### Исправлено (frozen build)

- `native_host.py`: в **frozen** (PyInstaller onefile) сборке отключён
  `ProcessPoolExecutor`. Spawn 6 копий `.exe` на Windows распаковывал
  архив заново при каждом старте — первый ingest занимал ~10 с. В frozen
  используется только `ThreadPoolExecutor`; `lxml` отпускает GIL на
  C-парсинге, этого достаточно. В dev-режиме (`python -m ...`) — полный
  ProcessPool, как раньше.

### Добавлено

- `native_host.py`: `threading.Semaphore(thread_workers * 2)` — **backpressure**.
  Без него при bulk-ingest на 100+ URL очередь in-flight задач растёт в
  память быстрее, чем `parse_product` успевает обрабатывать.
- `domain/ports.py`: `ParseFunc = Callable[[str, bool], tuple[str, dict]]` —
  тип для инъекции парсера в use case.
- `application/ingest.py`: `IngestHtmlUseCase` принимает `parser=None` +
  `parse_func` — DI без обязательного `HtmlParser`. `ValueError` если
  ни один не передан.

### Изменено

- `native_host.py`: `LOG.warning` → `LOG.info` для стартового сообщения
  о параллелизме (frozen / dev). WARNING в stderr native host'а попадал в
  `chrome_debug.log` Chrome'а и засорял его на каждом старте.

## [0.5.1] — 2026-10-27

### Быстрый режим bulk-ingest (fetch + fallback + капча)

- `lib/bulk_fetcher.js`: fast mode — fetch из уже открытой Ozon-вкладки,
  без создания новых. Замер: 31 URL за ~2 с (~60–80 мс/URL). WAF Ozon
  пропускает page-context fetch (проверено 31/31 = 200).
- Fallback на навигацию для URL, где fetch упал (403/network).
- Обработка капчи: `detectCaptcha()` по final URL + маркерам HTML +
  аномальной длине (< 30 КБ). При срабатывании — `handleCaptcha()`:
  открывает активную вкладку, уведомление «Пройдите капчу», polling
  каждые 3 с до 2 минут. После прохождения — продолжаем bulk.
- `bulk.html`: checkbox «Быстрый режим» (вкл. по умолчанию). Статус
  «⚠️ Ждём прохождения капчи» в UI.

### Ускорено (parallel ingest — dev only)

> **Важно (см. 0.6.0):** в **frozen** сборке `ProcessPoolExecutor` отключён —
> spawn 6 копий `.exe` распаковывал архив заново на каждом старте (~10 с на
> первый ingest). Здесь описано поведение **dev-режима**
> (`python -m ozon_parser.presentation.native_host`).

- `native_host.py`: параллельная обработка входящих сообщений.
  ThreadPoolExecutor (I/O: JSON, SQLite, gzip) + ProcessPoolExecutor
  (парсинг `parse_product`, обход GIL — каждый процесс на своём ядре).
  Число процессов: `min(6, cpu_count - 1)`, потоков: `proc_workers * 2`.
  stdin-loop больше не блокируется на парсинге — 5 воркеров bulk шлют
  сообщения одновременно, host обрабатывает их параллельно.
- `IngestHtmlUseCase` принимает опциональный `parse_func` — DI для
  CPU-параллелизма без поломки слоёв CA. `parse_func=None` → старое
  поведение (`self._parser.parse`).
- `NATIVE_TIMEOUT_MS` в extension: 5000 → 10000 мс (первый ingest
  после старта host'а включает spawn процессов ~1-2 с).

## [0.5.0] — 2026-10-27

### Добавлено

- `scripts/sync_docs.py` — single source of truth для версии:
  маркеры `<!-- AUTO:VERSION -->` в .md, автосинк `__version__`,
  `server_version`, `manifest.json`, таблицы поддержки в `SECURITY.md`.
  `--bump X.Y.Z` создаёт заготовку в CHANGELOG, `--check` — для CI.
- `parser.py`: `lxml` (C-парсер) вместо `html.parser` — в 3–5 раз
  быстрее на больших PDP. Fallback на `html.parser`, если lxml нет.
- `DELETE /items` (без SKU) — атомарная очистка коллекции одним вызовом.
- Native Messaging action `items.clear` — bulk clear для extension.
- `ozon-parser dev register` / `dev serve` — итерация без пересборки.
- Extension: кнопка «Копировать MD», «Все вкладки», «Reload extension + native».

### Изменено

- `background.js`: **persistent native port** (`connectNative` живёт между
  вызовами, id-based matching). Убирает spawn PyInstaller `.exe`
  (−1.5–2 с) с каждого ingest — главный источник задержки 2–3 с.
- **Auto-ingest без debounce**: 3-секундный таймер убран, заменён на
  event-based dedup через `chrome.storage.session` (5 с) + inflight-карта.
  `page-fetch` не зависит от DOM — таймер был карго-культом.
- Триггеры auto-ingest расширены: `tabs.onCreated(pendingUrl)`,
  `webNavigation.onCommitted` / `onHistoryStateUpdated`,
  `runtime.onStartup(tabs.query)`.
- `notifyResult` больше не блокирует на `GET /items` — оптимистичный badge.

### Исправлено

- Popup «Очистить всё» → один вызов `items.clear`, а не N × `items.delete`.
- Badge на иконке Chrome синхронизируется при `items.clear` / bulk ingest
  (popup сам зовёт `chrome.action.setBadgeText` в `renderList`).
- В popup видно `copy_type` (`source` / `rendered`) — сразу ясно, полные
  ли данные (rendered на фоновой вкладке теряет user_media).

## [0.4.0] — 2026-10-21

### Добавлено

- **Native Messaging** — расширение запускает Python-ядро через stdio
  (Chrome сам поднимает host-процесс), без фонового `serve` и без порта
  `:8765` в `netstat`. Fallback на HTTP — если host не зарегистрирован.
- **Installer** (`ozon-parser-setup.exe`, Inno Setup) — установка двойным
  кликом, без прав администратора, без wizard'а Extension ID.
- `ozon-parser native register/unregister` — регистрация host во всех
  Chromium-браузерах (Chrome, Edge, Yandex, Brave, Chromium, Vivaldi).
- `build/gen_extension_key.py` — генератор RSA-2048 ключа и стабильного
  extension ID.
- **Auto-ingest** (opt-in) — при заходе на `/product/...` расширение само
  отправляет HTML. Дебаунс 3с, дедуп 60с, уведомление после ingest.
- **Popup** расширения — список товаров, удаление, выгрузка (MD/JSONL/CSV),
  очистка, ingest текущей. Работает без консоли.
- Hotkey `Alt+Shift+O` и context menu на Ozon-странице.

### Изменено

- **Extension ID стабилен**: `bmdjfjhckhfifhdnohiojamggfnlbcgf`. Зафиксирован
  через `key` в `manifest.json` — больше не случаен при `Load unpacked`.
- Options расширения: убрано поле Token, обновлена справка (native без
  сервера), «Порт HTTP (fallback)» с пояснением.
- Документация (`README`, `ARCHITECTURE`, `extension/README`) синхронизирована
  с фактическим состоянием.

### Удалено

- `ozon_parser/auth.py` и `tests/test_auth.py` — токен-аутентификация не
  использовалась с 0.3.0.
- Мёртвый код в `daemon.py` (`_resolve_sku`, `_sku_from_url`, `_sku_from_html`)
  — SKU резолвится в `IngestHtmlUseCase`.
- `cli._cmd_token` — никогда не была зарегистрирована в argparse (и падала
  бы с `NameError`).

## [0.3.0] — 2026-09-XX

### Добавлено

- `IngestHtmlUseCase` / `ReparseUseCase` — единая точка ingest, без тройного
  дублирования parse+quality+upsert в `daemon`/`server`/`cli`.
- Clean Architecture: `domain` / `application` / `infrastructure` /
  `presentation`, правило зависимостей проверяется `test_architecture.py`.

### Изменено

- CORS сужен до `https://www.ozon.ru` (было `*`).
- `POST /flush` принимает только `{format}` — `to:file` убран из HTTP
  (arbitrary file write vector).
- Windows: `allow_reuse_address=False` — второй `serve` падает с
  `WinError 10048`, а не создаёт зомби в LISTENING.

### Удалено

- Токен-аутентификация HTTP API (была по умолчанию).

## [0.2.0] — TBD

### Добавлено

- Extension (Manifest V3): popup, hotkey, context menu, auto-ingest.
- Bookmarklet — генератор JS-закладки.
- Автозапуск: `service install --mode {serve,daemon}` для Windows/macOS/Linux.
- `reparse` — перепарсинг `raw_html` из БД.
- `export-fixture` — выгрузка `raw_html` для регрессионных тестов.

## [0.1.0] — TBD

Первый рабочий релиз: CLI (`once`/`daemon`/`list`/`flush`/`clear`), SQLite,
парсер source/rendered.
