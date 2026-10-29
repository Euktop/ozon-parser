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
import { runFastBulk } from "./lib/bulk_fetcher.js";

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

// ---------- Bulk URL Import (worker pool) ----------
let _bulkCancel = false;

// Один воркер: переиспользует СВОЮ вкладку для нескольких URL.
// Преимущества перед «новая вкладка на URL»:
//   - экономит 200-500 мс на create/remove
//   - Chrome не дёргает tab strip
// 3 воркера параллельно → ×3 к скорости.
async function _bulkWorker(workerId, queue, delayMs, stats, sendProgress) {
  const tab = await chrome.tabs.create({ url: "about:blank", active: false });
  const tStart = performance.now();
  try {
    while (queue.length > 0 && !_bulkCancel) {
      const url = queue.shift();
      if (!url) break;

      const tNav = performance.now();
      try {
        await chrome.tabs.update(tab.id, { url });
      } catch (e) {
        stats.done++; stats.err++;
        console.warn(`[bulk-w${workerId}] update failed:`, e);
        sendProgress();
        continue;
      }
      await _waitForTabComplete(tab.id, 15000);
      const dtNav = (performance.now() - tNav).toFixed(0);

      const cur = await chrome.tabs.get(tab.id);
      if (!cur || !isOzonPdp(cur.url)) {
        stats.done++; stats.err++;
        console.warn(`[bulk-w${workerId}] not PDP: ${cur?.url}`);
        sendProgress();
        continue;
      }

      const tIngest = performance.now();
      const res = await ingestTab(cur);
      const dtIngest = (performance.now() - tIngest).toFixed(0);

      stats.done++;
      if (res.status === "added" || res.status === "updated") stats.ok++;
      else if (res.status === "skipped") stats.skip++;
      else stats.err++;

      const sku = (url.match(/\/product\/[^/]*-?([0-9]+)/) || [])[1] || "?";
      console.log(`[bulk-w${workerId}] ${sku} → ${res.status} (nav=${dtNav}ms, ingest=${dtIngest}ms)`);
      sendProgress();

      if (delayMs > 0 && queue.length > 0) {
        await new Promise((r) => setTimeout(r, delayMs));
      }
    }
  } finally {
    try { await chrome.tabs.remove(tab.id); } catch (_) {}
    console.log(`[bulk-w${workerId}] done in ${((performance.now() - tStart) / 1000).toFixed(1)}s`);
  }
}

function _waitForTabComplete(tabId, timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { chrome.tabs.onUpdated.removeListener(onUpd); } catch (_) {}
      resolve();
    };
    const onUpd = (tId, info) => {
      if (tId === tabId && info.status === "complete") finish();
    };
    chrome.tabs.onUpdated.addListener(onUpd);
    const timer = setTimeout(finish, timeoutMs);
  });
}

// Капча: открываем активную вкладку, показываем уведомление, ждём прохождения.
// Polling каждые 3 с: пробуем fetch оригинального URL из этой вкладки.
// Пока в ответе капча — продолжаем ждать. Таймаут — 2 минуты, отмена — _bulkCancel.
async function handleCaptcha(url) {
  const tab = await chrome.tabs.create({ url, active: true });

  chrome.notifications.create({
    type: "basic",
    iconUrl: ICON_1PX,
    title: "Ozon Parser — нужна помощь",
    message: "Пройдите капчу в открытой вкладке (ждём до 2 минут).",
    priority: 2,
    requireInteraction: true,
  });

  const deadline = Date.now() + 120000;  // 2 минуты
  let html = null;

  try {
    while (Date.now() < deadline) {
      if (_bulkCancel) {
        return null;
      }
      await new Promise((r) => setTimeout(r, 3000));

      try {
        const res = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: "MAIN",
          func: async (u) => {
            try {
              const r = await fetch(u, { credentials: "include" });
              return {
                status: r.status,
                finalUrl: r.url,
                html: r.ok ? await r.text() : null,
              };
            } catch (e) {
              return { status: 0, html: null, error: String(e) };
            }
          },
          args: [url],
        });
        const out = res[0]?.result;
        if (out && out.html && out.html.length > 30000) {
          const finalUrl = (out.finalUrl || "").toLowerCase();
          const lowHtml = out.html.toLowerCase();
          const stillCaptcha =
            finalUrl.includes("/captcha") ||
            finalUrl.includes("challenge") ||
            finalUrl.includes("/safety/") ||
            lowHtml.includes('data-widget="webchallengepage"') ||
            lowHtml.includes("защита от роботов") ||
            lowHtml.includes("подтвердите, что вы не робот");
          if (!stillCaptcha) {
            html = out.html;
            break;
          }
        }
      } catch (e) {
        // ещё на капче — продолжаем ждать
      }
    }
  } finally {
    try { await chrome.tabs.remove(tab.id); } catch (_) {}
  }

  if (!html) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: ICON_1PX,
      title: "Ozon Parser — капча не пройдена",
      message: "Таймаут 2 минуты. Bulk-ingest прерван.",
      priority: 2,
    });
    return null;
  }

  chrome.notifications.create({
    type: "basic",
    iconUrl: ICON_1PX,
    title: "Ozon Parser — капча пройдена",
    message: "Продолжаем обработку коллекции.",
    priority: 0,
  });

  return { solved: true, html };
}

async function startBulkIngestFast(urls, concurrency) {
  _bulkCancel = false;
  const total = urls.length;
  const t0 = performance.now();
  // Храним актуальные stats — иначе финальный sendProgress перезапишет их.
  let _lastStats = { done: 0, ok: 0, skip: 0, err: 0 };

  const sendProgress = (stats, finished = false, error = null, extra = null) => {
    _lastStats = { ...stats };
    const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
    const msg = {
      type: "bulk-progress",
      done: stats.done, total,
      ok: stats.ok, skip: stats.skip, err: stats.err,
      finished, error, elapsed,
    };
    if (extra && extra.timing) {
      const t = extra.timing;
      msg.detail =
        `fetch ${(t.fetch / 1000).toFixed(1)}с · ` +
        `ingest ${(t.ingest / 1000).toFixed(1)}с` +
        (t.fallback ? ` · fallback ${t.fallback}` : "") +
        (t.captcha ? ` · капча ${t.captcha}` : "");
    }
    chrome.runtime.sendMessage(msg).catch(() => {});
  };

  console.log(`[fast-bulk] start: ${total} URLs, concurrency=${concurrency}`);
  sendProgress({ done: 0, ok: 0, skip: 0, err: 0 });

  const timing = await runFastBulk(
    urls,
    concurrency,
    (stats, extra) => {
      sendProgress(stats, false, null, extra);
    },
    () => _bulkCancel,
    ingestTab,
    async (captchaUrl) => {
      console.log(`[fast-bulk] asking user to solve captcha: ${captchaUrl}`);
      return await handleCaptcha(captchaUrl);
    }
  );

  const totalElapsed = ((performance.now() - t0) / 1000).toFixed(1);
  const avgTotal = timing.fetchCount > 0
    ? ((performance.now() - t0) / timing.fetchCount).toFixed(0)
    : 0;
  const avgFetch = timing.fetchCount > 0 ? (timing.fetch / timing.fetchCount).toFixed(0) : 0;
  const avgIngest = timing.ingestCount > 0 ? (timing.ingest / timing.ingestCount).toFixed(0) : 0;

  console.log(`[fast-bulk] complete in ${totalElapsed}s:`);
  console.log(`  total avg: ${avgTotal}ms/URL`);
  console.log(`  fetch: ${(timing.fetch / 1000).toFixed(2)}s total, ${avgFetch}ms avg, ${timing.fetchCount} fetches`);
  console.log(`  ingest: ${(timing.ingest / 1000).toFixed(2)}s total, ${avgIngest}ms avg, ${timing.ingestCount} ingests`);
  console.log(`  ok: ${_lastStats.ok}, skip: ${_lastStats.skip}, err: ${_lastStats.err}`);
  console.log(`  fallback: ${timing.fallback}, captcha: ${timing.captcha}`);

  // Финал — с реальными stats из последнего прогресса
  sendProgress(_lastStats, true, null, { timing });
  await updateBadge();
}

async function startBulkIngest(urls, delayMs, concurrency) {
  _bulkCancel = false;
  const total = urls.length;
  const nWorkers = Math.max(1, Math.min(concurrency || 3, total));
  const queue = [...urls];
  const stats = { done: 0, ok: 0, skip: 0, err: 0 };
  const t0 = performance.now();

  const sendProgress = (finished = false, error = null) => {
    const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
    chrome.runtime.sendMessage({
      type: "bulk-progress",
      done: stats.done, total,
      ok: stats.ok, skip: stats.skip, err: stats.err,
      finished, error, elapsed,
    }).catch(() => {});
  };

  console.log(`[bulk] start: ${total} URLs, ${nWorkers} workers, delay=${delayMs}ms`);
  sendProgress();

  await Promise.all(
    Array.from({ length: nWorkers }, (_, i) =>
      _bulkWorker(i + 1, queue, delayMs, stats, sendProgress)
    )
  );

  const totalElapsed = ((performance.now() - t0) / 1000).toFixed(1);
  const avg = stats.done > 0 ? ((performance.now() - t0) / stats.done).toFixed(0) : 0;
  console.log(
    `[bulk] complete: ${stats.ok} ok, ${stats.skip} skip, ${stats.err} err ` +
    `in ${totalElapsed}s (avg ${avg}ms/URL, ${nWorkers} workers)`
  );
  sendProgress(true);
  await updateBadge();
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
  if (msg && msg.type === "bulk-ingest") {
    if (msg.fastMode) {
      startBulkIngestFast(msg.urls || [], msg.concurrency || 5);
    } else {
      startBulkIngest(
        msg.urls || [],
        msg.delay || 0,
        msg.concurrency || 5
      );
    }
    sendResponse({ ok: true });
    return false;
  }
  if (msg && msg.type === "bulk-cancel") {
    _bulkCancel = true;
    sendResponse({ ok: true });
    return false;
  }
  if (msg && msg.type === "reload-native") {
    // Dev: принудительно закрыть порт — Chrome убьёт host-процесс,
    // следующий apiCall поднимет его заново со свежим кодом.
    disconnectNative();
    sendResponse({ ok: true });
    return false;
  }
});
