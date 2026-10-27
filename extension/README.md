# Ozon Parser — браузерное расширение (Manifest V3)

Отправляет текущую Ozon PDP в локальный `ozon-parser serve` одним
кликом / горячей клавишей / контекстным меню.

## Установка

1. Запустить HTTP-сервер:

       ozon-parser serve

2. Открыть `chrome://extensions` (или `edge://extensions`).

3. Включить **Developer mode** (тумблер справа сверху).

4. **Load unpacked** → выбрать папку `extension/` этого репозитория.

5. Иконка «O» появится на тулбаре. Открыть настройки (правой кнопкой →
   Options, или ⚙ в popup):
   - **Port**: 8765 (по умолчанию)
   - **Auto-ingest**: ☑ (опционально, чтобы товары добавлялись сами)

### Яндекс Браузер

Yandex Browser — Chromium (как Chrome, Edge, Brave), расширение работает
без изменений. Отличия только в установке и в путях native host.

1. **Native host:** `ozon-parser dev register` (или `native register`)
   уже регистрирует `Software\Yandex\YandexBrowser\NativeMessagingHosts`
   (Windows), `~/Library/Application Support/Yandex/YandexBrowser/NativeMessagingHosts`
   (macOS), `~/.config/yandex-browser/NativeMessagingHosts` (Linux).
2. **Загрузка расширения:** открой `browser://extensions` (в свежих
   версиях Yandex), либо `chrome://extensions` — оба работают. Включи
   **Режим разработчика** → **Загрузить распакованное** → папка `extension/`.
3. **Проверка:** Options (⚙ в popup) внизу покажет «Браузер: Яндекс Браузер
   (yandex)». Если `(chrome)` — расширение загружено в Chrome, не в Yandex.

Extension ID стабилен (`bmdjfjhckhfifhdnohiojamggfnlbcgf`) и одинаков в
Chrome и Yandex — `allowed_origins` в manifest host работает в обоих.

## Использование

| Способ | Действие |
|---|---|
| `Alt+Shift+O` | Ingest активной вкладки |
| Клик по иконке | **Popup** (список, удаление, выгрузка, очистка, ingest текущей) |
| Правой кнопкой → «→ Ozon Parser» | Ingest активной вкладки (на Ozon-странице) |

После ingest появляется нотификация: `added: 3184585029 — Быстрая Зарядка 67W`
или `updated: ...`. Badge на иконке показывает число товаров в коллекции.

### Popup (0.2.0)

Клик по иконке открывает окно со списком товаров:

- **× на товаре** — удалить (DELETE /items/{sku})
- **MD / JSONL / CSV** — скачать коллекцию. Файл сохраняется через
  `chrome.downloads` в «Загрузки» браузера (с диалогом «Сохранить как»).
  Прямого доступа к файловой системе сервера нет.
- **Очистить всё** — удалить все товары одним вызовом (`items.clear`
  native / `DELETE /items` HTTP), с `confirm()`
- **Все вкладки** — bulk ingest всех открытых `*.ozon.ru/product/*`.
  На 10 PDP — ~15–30 с, статус `Готово: N добавлено, M пропущено,
  K ошибок`.
- **Ingest текущей** — отправить PDP активной вкладки
- **⚙** — открыть Options

## Auto-ingest (opt-in)

В options можно включить **Auto-ingest** — тогда расширение будет
автоматически отправлять HTML при заходе на страницу `/product/...`.

- **Opt-in**: по умолчанию выключено.
- **Триггеры**: `tabs.onUpdated` + `tabs.onCreated` (`pendingUrl` —
  Ctrl+Click и middle-click, пока вкладка ещё грузится) +
  `webNavigation.onCommitted` (обычная навигация, раньше `onUpdated`) +
  `webNavigation.onHistoryStateUpdated` (SPA-переход листинг → PDP через
  `history.pushState`) + `runtime.onStartup` (session restore: Chrome
  перезапустился с открытыми PDP, навигации не было).
- **Дебаунс 3 с** — SPA-навигация Ozon дёргает `tabs.onUpdated`
  несколько раз, дебаунс ждёт «успокоения».
- **Дедуп 60 с** — тот же URL в течение минуты не отправляется повторно.
  F5 через 10 сек не сработает; F5 через 2 минуты — сработает.
- **Уведомления (опция)**: галочка «Уведомление после auto-ingest».
  По умолчанию включена. Показывается **только для активной вкладки**:
  если ты в другой вкладке, badge обновится без уведомления.
  Заголовок: «Ozon Parser — авто», текст: `added: <sku> — <title> (<method>)`.
- **Ошибки auto глотаются**: если сервер не отвечает — пропускаем
  без нотификаций (даже при включённом notifyAutoIngest), чтобы не спамить.
  Badge не растёт — сам заметишь.

С hotkey / кликом / context menu auto-ingest не конфликтует —
ручной триггер идёт сразу, без дебаунса.

## Ограничения (MVP)

- Только Chromium-браузеры (Chrome, Edge, Brave, Arc). Firefox MV3 отличается.
- Требуется `chrome.scripting` (MV3, Chrome 95+) и `tabs` permission.
- `world: "MAIN"` fetch может 403'нуться на особо злых WAF-настройках —
  тогда срабатывает rendered fallback. Источник виден в нотификации:
  `(page-fetch)` = source, `(rendered-dom)` = rendered.
- Иконки не приложены — Chrome показывает дефолтную с буквой «O». При желании
  положить `icons/16.png`, `48.png`, `128.png` и добавить `icons` в `manifest.json`.

## Как это работает

`background.js` (service worker) на любом триггере:

1. Проверяет, что `tab.url` содержит `ozon.ru`.
2. **Попытка 1:** `chrome.scripting.executeScript({world: "MAIN"})`
   запускает `fetch(location.href)` **в контексте страницы** — с её
   заголовками (`Sec-Fetch-*`), куками, `Accept`. Это обычно проходит
   WAF Ozon и даёт **source** HTML (`data-server-rendered="true"`).
3. **Попытка 2 (fallback):** `document.documentElement.outerHTML` —
   **rendered** DOM, никакого сетевого запроса. Парсер обрабатывает
   rendered с тем же `quality 9/9`.
4. `POST http://127.0.0.1:8765/ingest?url=<tab.url>` с телом HTML.
5. Читает JSON-ответ `{status, sku, quality, copy_type, ...}`,
   показывает нотификацию (включая `(page-fetch)` или `(rendered-dom)`).
6. `GET /items` → обновляет badge.

### Почему не `fetch(tab.url)` из service worker

Первая версия делала `fetch(tab.url)` прямо из SW. Ozon отвечает
**403** — WAF видит XHR-like запрос (`Sec-Fetch-Dest: empty`) вместо
навигации. Из SW это не побороть. `chrome.scripting` + `world: MAIN`
даёт page-контекст, `document.documentElement.outerHTML` — гарантированный
fallback без сети.

### Persistent native port

`background.js` держит **один** `chrome.runtime.connectNative(...)` между
вызовами (id-based matching). Раньше каждый запрос открывал и закрывал
порт — Chrome спавнил PyInstaller `.exe` заново (~1.5–2 с), это была
основная задержка при клике Ingest. Сейчас второй и последующие ingest —
~300–700 мс.

При крэше host'а порт сбрасывается (`onDisconnect` → `_nativePort = null`),
следующий вызов автоматически поднимет новый процесс. Также `onStartup`
делает `updateBadge()` — это и восстанавливает badge после перезапуска
Chrome, и прогревает порт до первого клика.

## Dev-режим

Без пересборки `.exe` + installer на каждое изменение Python-кода.

1. `pip install -e .[dev]` — lxml, watchfiles, cryptography.
2. `ozon-parser dev register` — регистрирует host как `.bat`/`.sh` wrapper
   (`python -m ozon_parser.presentation.native_host`), а не `.exe`.
   Отказывается работать из frozen `.exe`.
3. Правь Python-код, в Options → **Reload extension + native** — Chrome
   перезапустит SW, следующий ingest создаст новый host-процесс
   с актуальным кодом.
4. `ozon-parser dev serve` — опционально: HTTP-сервер с автоперезапуском
   при правке `.py`. Нужен только для fallback-транспорта (bookmarklet /
   Web UI) — native host работает без него.
