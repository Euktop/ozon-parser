// Bulk-фетчер: worker pool с параллельным fetch и ingest.
//
// Архитектура:
//   - N воркеров (concurrency), каждый берёт URL из очереди.
//   - Один воркер: fetchOne → [fallback | captcha | ingest].
//   - Всё параллельно: пока worker1 ждёт fetch, worker2 уже ingest'ит.
//   - Капча сериализуется через mutex: только один воркер ведёт пользователя.
//
// Замеры (31 URL): последовательный вариант был 9.2с, worker-pool
// с параллельным fetch+ingest даёт ~1.5-2с.

import { apiCall } from "./api_client.js";

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

/** Детектор капчи/челленджа Ozon по финальному URL и HTML. */
export function detectCaptcha(finalUrl, html) {
  const u = (finalUrl || "").toLowerCase();
  if (u.includes("/captcha") || u.includes("challenge") || u.includes("/safety/")) {
    return true;
  }
  if (!html) return false;
  if (html.length < 30000) return true; // PDP всегда ~570-650 КБ

  const lower = html.toLowerCase();
  if (lower.includes('data-widget="webchallengepage"')) return true;
  if (lower.includes("защита от роботов")) return true;
  if (lower.includes("подтвердите, что вы не робот")) return true;
  if (lower.includes("anti-bot challenge")) return true;
  return false;
}

/** Один fetch в контексте Ozon-вкладки. Возвращает {status, finalUrl, html, dtFetch}. */
async function _fetchOne(tabId, url) {
  const t0 = performance.now();
  try {
    const res = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: async (u) => {
        try {
          const r = await fetch(u, { credentials: "include" });
          return {
            status: r.status,
            finalUrl: r.url,
            redirected: r.redirected,
            html: r.ok ? await r.text() : null,
          };
        } catch (e) {
          return { status: 0, html: null, error: String(e) };
        }
      },
      args: [url],
    });
    const out = res[0]?.result || { status: 0, html: null, error: "no result" };
    return { ...out, dtFetch: performance.now() - t0 };
  } catch (e) {
    return {
      status: 0, html: null, error: String(e),
      dtFetch: performance.now() - t0,
    };
  }
}

/** Fallback: навигация в отдельной фоновой вкладке. */
async function _fallbackNavigate(url, ingestTab, isCancelled) {
  if (isCancelled()) return "cancel";
  let tab;
  try {
    tab = await chrome.tabs.create({ url, active: false });
  } catch (e) {
    return "err";
  }
  await _waitForTabComplete(tab.id, 15000);
  if (isCancelled()) {
    try { await chrome.tabs.remove(tab.id); } catch (_) {}
    return "cancel";
  }
  let cur;
  try {
    cur = await chrome.tabs.get(tab.id);
  } catch (e) {
    try { await chrome.tabs.remove(tab.id); } catch (_) {}
    return "err";
  }
  if (detectCaptcha(cur.url, "")) {
    try { await chrome.tabs.remove(tab.id); } catch (_) {}
    return "captcha";
  }
  const res = await ingestTab(cur);
  try { await chrome.tabs.remove(tab.id); } catch (_) {}
  if (res && (res.status === "added" || res.status === "updated")) return "ok";
  if (res && res.status === "skipped") return "skip";
  return "err";
}

/**
 * Запустить bulk-ingest в worker pool.
 *
 * @param {string[]} urls
 * @param {number} concurrency
 * @param {function} onStats — (stats, extra) => void
 *   stats: {done, ok, skip, err}
 *   extra: {total, elapsed, timing}
 * @param {function} isCancelled — () => bool
 * @param {function} ingestTab — fallback
 * @param {function} onCaptcha — async (url) => {html} | null
 */
export async function runFastBulk(
  urls,
  concurrency,
  onStats,
  isCancelled,
  ingestTab,
  onCaptcha
) {
  let tabs = await chrome.tabs.query({ url: "*://*.ozon.ru/*" });
  let tab = tabs[0];
  const weCreatedTab = !tab;
  if (!tab) {
    tab = await chrome.tabs.create({ url: "https://www.ozon.ru/", active: false });
    await _waitForTabComplete(tab.id, 15000);
  }

  const total = urls.length;
  const queue = [...urls];
  const stats = { done: 0, ok: 0, skip: 0, err: 0 };
  const timing = {
    fetch: 0, ingest: 0,
    fetchCount: 0, ingestCount: 0,
    fallback: 0, captcha: 0,
  };
  const t0 = performance.now();

  // Mutex: только один воркер за раз ведёт пользователя через капчу.
  // Остальные ждут и пробуют refetch после — куки WAF уже обновлены.
  let _captchaLock = Promise.resolve();
  const _withCaptchaLock = async (fn) => {
    const prev = _captchaLock;
    let release;
    _captchaLock = new Promise((r) => { release = r; });
    await prev;
    try {
      return await fn();
    } finally {
      release();
    }
  };

  const _logIngest = (url, res, dtIngest) => {
    const sku = (url.match(/\/product\/[^/]*-?(\d+)/) || [])[1] || "?";
    const status = res?.status || res?.error || "null";
    console.log(`[fast-bulk] ${sku} → ${status} (${dtIngest.toFixed(0)}ms)`);
  };

  const processUrl = async (url) => {
    const tf = performance.now();
    const fetchRes = await _fetchOne(tab.id, url);
    timing.fetch += fetchRes.dtFetch;
    timing.fetchCount++;

    // 1) fetch упал (403/network) → fallback навигацией
    if (!fetchRes.html) {
      timing.fallback++;
      console.log(`[fast-bulk] fetch failed ${url.substring(0, 80)}... (status=${fetchRes.status}) → fallback`);
      const r = await _fallbackNavigate(url, ingestTab, isCancelled);
      if (r === "ok") return "ok";
      if (r === "skip") return "skip";
      if (r === "cancel") return "cancel";
      if (r === "captcha") {
        timing.captcha++;
        return await _withCaptchaLock(async () => {
          const solved = await onCaptcha(url);
          if (solved && solved.html) {
            const ti = performance.now();
            const res = await apiCall("ingest", { html: solved.html, url });
            timing.ingest += performance.now() - ti;
            timing.ingestCount++;
            _logIngest(url, res, performance.now() - ti);
            return _classify(res);
          }
          return "err";
        });
      }
      return "err";
    }

    // 2) fetch вернул HTML, но это капча
    if (detectCaptcha(fetchRes.finalUrl || url, fetchRes.html)) {
      timing.captcha++;
      console.log(`[fast-bulk] captcha on ${url.substring(0, 80)}... (len=${fetchRes.html.length})`);
      return await _withCaptchaLock(async () => {
        const refetch = await _fetchOne(tab.id, url);
        if (refetch.html && !detectCaptcha(refetch.finalUrl || url, refetch.html)) {
          const ti = performance.now();
          const res = await apiCall("ingest", { html: refetch.html, url });
          timing.ingest += performance.now() - ti;
          timing.ingestCount++;
          _logIngest(url, res, performance.now() - ti);
          return _classify(res);
        }
        const solved = await onCaptcha(url);
        if (solved && solved.html) {
          const ti = performance.now();
          const res = await apiCall("ingest", { html: solved.html, url });
          timing.ingest += performance.now() - ti;
          timing.ingestCount++;
          _logIngest(url, res, performance.now() - ti);
          return _classify(res);
        }
        return "err";
      });
    }

    // 3) Нормальный ingest
    const ti = performance.now();
    const res = await apiCall("ingest", { html: fetchRes.html, url });
    const dtIngest = performance.now() - ti;
    timing.ingest += dtIngest;
    timing.ingestCount++;
    _logIngest(url, res, dtIngest);
    return _classify(res);
  };

  const _classify = (res) => {
    if (res && (res.status === "added" || res.status === "updated")) return "ok";
    if (res && res.status === "skipped") return "skip";
    return "err";
  };

  const worker = async (workerId) => {
    while (queue.length > 0 && !isCancelled()) {
      const url = queue.shift();
      if (!url) break;
      try {
        const r = await processUrl(url);
        if (r === "cancel") return;
        stats.done++;
        if (r === "ok") stats.ok++;
        else if (r === "skip") stats.skip++;
        else stats.err++;
      } catch (e) {
        console.warn(`[fast-bulk-w${workerId}] throw:`, e);
        stats.done++;
        stats.err++;
      }
      onStats(stats, {
        total,
        elapsed: (performance.now() - t0) / 1000,
        timing: { ...timing },
      });
    }
  };

  try {
    await Promise.all(
      Array.from({ length: Math.max(1, concurrency) }, (_, i) => worker(i + 1))
    );
  } finally {
    if (weCreatedTab) {
      try { await chrome.tabs.remove(tab.id); } catch (_) {}
    }
  }

  return timing;
}
