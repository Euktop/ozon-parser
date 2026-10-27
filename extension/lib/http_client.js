// HTTP fallback клиент. Используется, если Native host не зарегистрирован.
// Работает только при запущенном `ozon-parser serve`.

const DEFAULT_PORT = 8765;

async function getSettings() {
  return chrome.storage.sync.get({
    port: DEFAULT_PORT,
  });
}

export async function httpCall(action, params) {
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
  if (action === "items.clear") {
    const r = await fetch(`${base}/items`, { method: "DELETE" });
    if (!r.ok) return { error: `DELETE /items → ${r.status}` };
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
