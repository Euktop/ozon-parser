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
const AUTO_DEBOUNCE_MS = 3000;
const AUTO_DEDUP_MS = 60 * 1000;
const NATIVE_TIMEOUT_MS = 5000;

const ICON_1PX =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

let _autoTimer = null;
let _lastAutoUrl = "";
let _lastAutoAt = 0;

async function getSettings() {
  return chrome.storage.sync.get({
    port: DEFAULT_PORT,
    autoIngest: false,
    notifyAutoIngest: true,
  });
}

// ---------- transport: native → HTTP fallback ----------

async function nativeCall(action, params) {
  let port;
  try {
    port = chrome.runtime.connectNative("com.ozon_parser");
  } catch (e) {
    return null; // host not registered → fallback to HTTP
  }
  return new Promise((resolve) => {
    let done = false;
    const finish = (val) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try {
        port.disconnect();
      } catch (_) {}
      resolve(val);
    };
    const timer = setTimeout(() => finish(null), NATIVE_TIMEOUT_MS);
    port.onMessage.addListener((msg) => finish(msg));
    port.onDisconnect.addListener(() => {
      if (chrome.runtime.lastError) {
        // host crashed / not found → fallback
        finish(null);
      } else {
        finish({ error: "native host disconnected" });
      }
    });
    try {
      port.postMessage({ action, ...params });
    } catch (e) {
      finish(null);
    }
  });
}

async function httpCall(action, params) {
  const { port } = await getSettings();
  const base = `http://127.0.0.1:${port}`;
  if (action === "health") {
    const r = await fetch(`${base}/health`);
    return r.json();
  }
  if (action === "items.list") {
    const r = await fetch(`${base}/items`);
    if (!r.ok) return { error: `GET /items → ${r.status}` };
    return r.json();
  }
  if (action === "items.get") {
    const r = await fetch(`${base}/items/${encodeURIComponent(params.sku)}`);
    if (!r.ok) return { error: `GET /items/${params.sku} → ${r.status}` };
    return { item: { markdown: await r.text() } };
  }
  if (action === "items.delete") {
    const r = await fetch(
      `${base}/items/${encodeURIComponent(params.sku)}`,
      { method: "DELETE" }
    );
    return r.json();
  }
  if (action === "ingest") {
    const url = `${base}/ingest?url=${encodeURIComponent(params.url || "")}`;
    const r = await fetch(url, {
      method: "POST",
      body: params.html || "",
      headers: { "Content-Type": "text/plain" },
    });
    if (!r.ok) {
      const text = await r.text();
      return { status: "error", error: `${r.status}: ${text.slice(0, 200)}` };
    }
    return r.json();
  }
  if (action === "flush") {
    const r = await fetch(`${base}/flush`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: params.format || "markdown", to: "response" }),
    });
    if (!r.ok) {
      const text = await r.text();
      return { error: `${r.status}: ${text.slice(0, 200)}` };
    }
    return { text: await r.text(), format: params.format || "markdown" };
  }
  return { error: `unknown action: ${action}` };
}

async function apiCall(action, params = {}) {
  const native = await nativeCall(action, params);
  if (native !== null) return native;
  return await httpCall(action, params);
}

// ---------- Ozon fetch (page-context) ----------

function isOzonUrl(url) {
  return typeof url === "string" && url.includes("ozon.ru");
}

function isOzonPdp(url) {
  return (
    typeof url === "string" &&
    /^https?:\/\/(www\.)?ozon\.ru\/product\//.test(url)
  );
}

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
  await updateBadge();
}

async function updateBadge() {
  try {
    const j = await apiCall("items.list", {});
    if (!j || j.error) {
      chrome.action.setBadgeText({ text: "" });
      return;
    }
    const n = (j.items || []).length;
    chrome.action.setBadgeText({ text: n > 0 ? String(n) : "" });
    chrome.action.setBadgeBackgroundColor({ color: "#005bff" });
  } catch (e) {
    chrome.action.setBadgeText({ text: "" });
  }
}

async function scheduleAutoIngest(tabId, url) {
  if (_autoTimer) {
    clearTimeout(_autoTimer);
    _autoTimer = null;
  }
  _autoTimer = setTimeout(async () => {
    _autoTimer = null;
    const { autoIngest, notifyAutoIngest } = await getSettings();
    if (!autoIngest) return;

    if (_lastAutoUrl === url && Date.now() - _lastAutoAt < AUTO_DEDUP_MS) {
      return;
    }

    let tab;
    try {
      tab = await chrome.tabs.get(tabId);
    } catch (e) {
      return;
    }
    if (!tab || !isOzonPdp(tab.url)) return;

    const res = await ingestTab(tab);
    _lastAutoUrl = url;
    _lastAutoAt = Date.now();

    if (res.status === "added" || res.status === "updated") {
      if (notifyAutoIngest && tab.active) {
        await notifyResult(res, "auto");
      } else {
        await updateBadge();
      }
    }
  }, AUTO_DEBOUNCE_MS);
}

// ---------- listeners ----------

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = changeInfo.url || (tab && tab.url);
  if (!isOzonPdp(url)) return;
  scheduleAutoIngest(tabId, url);
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
});
