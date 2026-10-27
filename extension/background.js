// Service worker (MV3). Триггеры:
//   - hotkey Alt+Shift+O
//   - context menu «→ Ozon Parser»
//   - клик по иконке → popup (popup шлёт messages сюда)
//   - auto-ingest (opt-in) при заходе на Ozon PDP
//
// ТРАНСПОРТ: native → HTTP fallback.
//   1. Native Messaging (connectNative("com.ozon_parser")) — без порта,
//      без фонового serve. Chrome сам запускает host-процесс.
//   2. HTTP (http://127.0.0.1:<port>) — fallback, если host не
//      зарегистрирован, но запущен `ozon-parser serve`.
//   apiCall() инкапсулирует выбор; ingestTab и updateBadge зовут только его.
//
// Popup не знает про транспорт: шлёт {type: "api", action, params} в
// service worker, получает результат через sendResponse.
//
// AUTO-INGEST (opt-in): chrome.tabs.onUpdated → дебаунс 3s → дедуп 60s →
// ingest. Тихо: только badge, если пользователь не на активной вкладке.

const DEFAULT_PORT = 8765;
// Dedup окно: 5с хватает, чтобы склеить onCommitted + onUpdated(status=complete)
// для одной навигации, но не мешает F5 вручную. У старого дебаунса
// (3000мс таймер) не было замеров — оказалось, что page-fetch не зависит
// от состояния DOM, и задержка чисто тавтологическая.
const AUTO_DEDUP_MS = 5000;
const NATIVE_TIMEOUT_MS = 5000;

const ICON_1PX =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

// In-memory dedup: 
// _autoInflight[tabId] = { url, promise } — если ingest того же URL уже
// в процессе для этой вкладки, не запускаем второй.
const _autoInflight = new Map();

async function getSettings() {
  return chrome.storage.sync.get({
    port: DEFAULT_PORT,
    autoIngest: false,
    notifyAutoIngest: true,
  });
}

import { apiCall } from "./lib/api_client.js";
import { disconnectNative } from "./lib/native_client.js";
import { isOzonUrl, isOzonPdp } from "./lib/url_matcher.js";

// ---------- Ozon fetch (page-context) ----------

async function fetchSourceInPage(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: async () => {
        try {
          const r = await fetch(location.href, {
            credentials: "include",
            headers: { Accept: "text/html,application/xhtml+xml" },
          });
          if (!r.ok) return { status: r.status, html: null };
          return { status: r.status, html: await r.text() };
        } catch (e) {
          return { status: 0, html: null, error: String(e) };
        }
      },
    });
    const r = results && results[0] ? results[0].result : null;
    if (r && r.html) return { html: r.html, method: "page-fetch" };
    return {
      html: null,
      method: "page-fetch",
      error: r ? `status ${r.status}${r.error ? ": " + r.error : ""}` : "no result",
    };
  } catch (e) {
    return { html: null, method: "page-fetch", error: String(e) };
  }
}

async function fetchRenderedInPage(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => document.documentElement.outerHTML,
    });
    const html = results && results[0] ? results[0].result : null;
    if (html) return { html, method: "rendered-dom" };
    return { html: null, method: "rendered-dom", error: "empty result" };
  } catch (e) {
    return { html: null, method: "rendered-dom", error: String(e) };
  }
}

async function ingestTab(tab) {
  if (!tab || !tab.url) {
    return { status: "skipped", error: "no active tab" };
  }
  if (!isOzonUrl(tab.url)) {
    return { status: "skipped", error: "not an Ozon page" };
  }
  if (!tab.id) {
    return { status: "skipped", error: "no tab.id" };
  }

  let res = await fetchSourceInPage(tab.id);
  let method = res.method;
  let html = res.html;
  let sourceError = res.error;

  if (!html) {
    res = await fetchRenderedInPage(tab.id);
    method = res.method;
    html = res.html;
    if (!html) {
      return {
        status: "error",
        error: `source fetch failed (${sourceError}); rendered dom failed (${res.error})`,
      };
    }
  }

  try {
    const j = await apiCall("ingest", { html, url: tab.url });
    if (!j) return { status: "error", error: "no response from backend" };
    j._method = method;
    return j;
  } catch (e) {
    return { status: "error", error: String(e) };
  }
}

async function notifyResult(res, source = "manual") {
  let message;
  if (res.status === "added" || res.status === "updated") {
    const title = res.title ? ` — ${res.title}` : "";
    const via = res._method ? ` (${res._method})` : "";
    message = `${res.status}: ${res.sku || "?"}${title}${via}`;
  } else if (res.status === "skipped") {
    message = `skipped: ${res.error || "not an Ozon PDP"}`;
  } else {
    message = `${res.status || "error"}: ${res.error || "unknown"}`;
  }
  const title = source === "auto" ? "Ozon Parser — авто" : "Ozon Parser";
  chrome.notifications.create({
    type: "basic",
    iconUrl: ICON_1PX,
    title: title,
    message: message.slice(0, 200),
    priority: 0,
    requireInteraction: false,
  });
  // Оптимистичный badge: не блокируем notifyResult на GET /items.
  // added → +1 локально; updated → без изменений; error/skipped → откат
  // на синхронный updateBadge(). Фоновое выравнивание через 2 с.
  if (res.status === "added") {
    try {
      const { count = 0 } = await chrome.storage.local.get({ count: 0 });
      const newCount = count + 1;
      await chrome.storage.local.set({ count: newCount });
      chrome.action.setBadgeText({ text: String(newCount) });
      chrome.action.setBadgeBackgroundColor({ color: "#005bff" });
    } catch (_) {}
  } else if (res.status !== "updated") {
    await updateBadge();
  }
  setTimeout(() => { updateBadge(); }, 2000);
}

async function updateBadge() {
  try {
    const j = await apiCall("items.list", {});
    if (!j || j.error) {
      chrome.action.setBadgeText({ text: "" });
      await chrome.storage.local.set({ count: 0 });
      return;
    }
    const n = (j.items || []).length;
    chrome.action.setBadgeText({ text: n > 0 ? String(n) : "" });
    chrome.action.setBadgeBackgroundColor({ color: "#005bff" });
    // Синхронизируем локальный счётчик с реальным — иначе
    // оптимистичный инкремент в notifyResult разъедется после reload
    // service worker'а.
    await chrome.storage.local.set({ count: n });
  } catch (e) {
    chrome.action.setBadgeText({ text: "" });
  }
}

// Dedup через chrome.storage.session: _lastAutoAt переживает SW restart.
// Без этого SW умирает через 30с → _lastAutoUrl сбрасывается → дедуп
// не работает между сессиями (наблюдалось: первый ingest мгновенно,
// второй 3с — это как раз дебаунс без дедупа).
async function _autoDedupCheck(url) {
  const { lastAutoUrl, lastAutoAt } = await chrome.storage.session.get({
    lastAutoUrl: "",
    lastAutoAt: 0,
  });
  if (lastAutoUrl === url && Date.now() - lastAutoAt < AUTO_DEDUP_MS) {
    return true;
  }
  await chrome.storage.session.set({ lastAutoUrl: url, lastAutoAt: Date.now() });
  return false;
}

async function scheduleAutoIngest(tabId, url) {
  if (!url) return;

  // 1) Уже в процессе для этой вкладки (onCommitted + onUpdated дубли)?
  const inflight = _autoInflight.get(tabId);
  if (inflight && inflight.url === url) {
    return;
  }

  // 2) Недавно уже обработали этот URL (persist across SW restarts)?
  if (await _autoDedupCheck(url)) return;

  const { autoIngest, notifyAutoIngest } = await getSettings();
  if (!autoIngest) return;

  let tab;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch (e) {
    return;
  }
  if (!tab || !isOzonPdp(tab.url)) return;

  // Не ждём DOM: page-fetch делает свой сетевой запрос и не зависит
  // от состояния страницы. Если page-fetch упадёт — fallback на
  // rendered-dom всё равно даст что-то.
  const promise = ingestTab(tab).then(async (res) => {
    _autoInflight.delete(tabId);
    if (res.status === "added" || res.status === "updated") {
      if (notifyAutoIngest && tab.active) {
        await notifyResult(res, "auto");
      } else {
        await updateBadge();
      }
    }
  });

  _autoInflight.set(tabId, { url, promise });
}

// ---------- listeners ----------

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = changeInfo.url || (tab && tab.url);
  if (!isOzonPdp(url)) return;
  scheduleAutoIngest(tabId, url);
});

// Ctrl+Click / middle-click: onUpdated сработает для фоновой вкладки, но
// pendingUrl фиксирует URL раньше — успеваем поставить в очередь, пока
// страница ещё грузится (важно для auto-ingest).
chrome.tabs.onCreated.addListener((tab) => {
  const url = (tab && (tab.pendingUrl || tab.url)) || "";
  if (!isOzonPdp(url)) return;
  scheduleAutoIngest(tab.id, url);
});

// SPA-навигация Ozon (листинг → PDP) не всегда даёт onUpdated:
// history.pushState меняет URL без полной навигации.
// onHistoryStateUpdated ловит именно это; onCommitted — обычную навигацию
// (срабатывает раньше onUpdated status=complete).
if (chrome.webNavigation) {
  chrome.webNavigation.onCommitted.addListener((details) => {
    if (details.frameId !== 0) return;
    if (!isOzonPdp(details.url)) return;
    scheduleAutoIngest(details.tabId, details.url);
  });
  chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
    if (details.frameId !== 0) return;
    if (!isOzonPdp(details.url)) return;
    scheduleAutoIngest(details.tabId, details.url);
  });
}

// Session restore: Chrome перезапустился с открытыми PDP-табами —
// onUpdated не сработает (навигации не было). onStartup + tabs.query.
chrome.runtime.onStartup.addListener(async () => {
  // Восстанавливаем badge и прогреваем native host одним вызовом:
  // updateBadge() дёргает apiCall("items.list") → создаёт persistent
  // порт → к первому клику host уже поднят.
  updateBadge().catch(() => {});
  try {
    const tabs = await chrome.tabs.query({ url: "*://*.ozon.ru/product/*" });
    for (const t of tabs) {
      if (t.id) scheduleAutoIngest(t.id, t.url);
    }
  } catch (_) {}
});

chrome.runtime.onInstalled.addListener(async () => {
  await chrome.contextMenus.removeAll();
  chrome.contextMenus.create({
    id: "ozon-ingest",
    title: "→ Ozon Parser",
    contexts: ["page", "link"],
    documentUrlPatterns: ["*://www.ozon.ru/*"],
  });
  await updateBadge();
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "ozon-ingest") {
    ingestTab(tab).then(notifyResult);
  }
});

chrome.commands.onCommand.addListener((command) => {
  if (command === "ingest-active") {
    chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
      ingestTab(tab).then(notifyResult);
    });
  }
});

// Popup зовёт apiCall и ingest-active через messages.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "api") {
    apiCall(msg.action, msg.params || {})
      .then(sendResponse)
      .catch((e) => sendResponse({ error: String(e) }));
    return true;
  }
  if (msg && msg.type === "ingest-active") {
    chrome.tabs
      .query({ active: true, currentWindow: true })
      .then(([tab]) => ingestTab(tab))
      .then((res) => {
        updateBadge();
        sendResponse(res);
      })
      .catch((e) => sendResponse({ status: "error", error: String(e) }));
    return true;
  }
  if (msg && msg.type === "ingest-tab") {
    // Bulk ingest из popup: явный tabId, вкладка может быть неактивной.
    chrome.tabs
      .get(msg.tabId)
      .then((tab) => ingestTab(tab))
      .then(sendResponse)
      .catch((e) => sendResponse({ status: "error", error: String(e) }));
    return true;
  }
  if (msg && msg.type === "reload-native") {
    // Dev: принудительно закрыть порт — Chrome убьёт host-процесс,
    // следующий apiCall поднимет его заново со свежим кодом.
    disconnectNative();
    sendResponse({ ok: true });
    return false;
  }
});
