# Changelog

Все заметные изменения проекта. Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## [Unreleased]

### Ускорение (persistent native port)

- `extension/background.js`: `connectNative` держится между вызовами
  (id-based matching) вместо `connectNative+disconnect` на каждый
  запрос. Убирает spawn PyInstaller `.exe` (~1.5–2 с) с каждого
  ingest — главный источник задержки 2–3 с.
- `notifyResult` больше не ждёт `GET /items`: badge обновляется
  оптимистично (+1 при `added`), фоновое выравнивание через 2 с.

### Auto-ingest расширен

- `manifest.json`: permission `webNavigation`.
- `webNavigation.onCommitted` + `onHistoryStateUpdated` — SPA-переходы
  Ozon (листинг → PDP) и ранний триггер до `onUpdated`.
- `tabs.onCreated` с `pendingUrl` — Ctrl+Click / middle-click, пока
  вкладка ещё грузится.
- `runtime.onStartup` + `tabs.query` — session restore (Chrome
  перезапустился с открытыми PDP).
- Popup: кнопка «Все вкладки» — bulk ingest всех открытых PDP.

### Extension: прочее

- Popup «Очистить всё» → один вызов `items.clear` (native)
  / `DELETE /items` (HTTP) вместо N × `items.delete`.
- Options: «Reload extension + native» и checkbox «Dev mode» —
  перезапуск без пересборки при правке Python-кода.

### Добавлено

- `DELETE /items` (без SKU) — очистка всей коллекции одним вызовом.
  Popup «Очистить всё» теперь делает один запрос, а не N × DELETE.
- Native Messaging action `items.clear` — bulk clear для extension.
- `ozon-parser dev register` — регистрация host как wrapper (.bat/.sh)
  с чек-листом; отказывается работать из frozen .exe.
- `ozon-parser dev serve` — HTTP-сервер с автоперезапуском при правке
  `.py` (через `watchfiles`, новая dev-зависимость).

### Изменено

- `parser.py`: `lxml` вместо `html.parser` — парсинг PDP в 3–5 раз
  быстрее на больших страницах. Fallback на `html.parser`, если
  `lxml` недоступен. `lxml` добавлен в основные зависимости.


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

## [0.2.0] — 2026-XX-XX

### Добавлено

- Extension (Manifest V3): popup, hotkey, context menu, auto-ingest.
- Bookmarklet — генератор JS-закладки.
- Автозапуск: `service install --mode {serve,daemon}` для Windows/macOS/Linux.
- `reparse` — перепарсинг `raw_html` из БД.
- `export-fixture` — выгрузка `raw_html` для регрессионных тестов.

## [0.1.0] — 2026-XX-XX

Первый рабочий релиз: CLI (`once`/`daemon`/`list`/`flush`/`clear`), SQLite,
парсер source/rendered.
