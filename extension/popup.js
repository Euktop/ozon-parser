// Popup logic: список товаров, удаление, выгрузка, очистка.
//
// Все запросы к Python-ядру идут через background.js (apiCall):
// native messaging (stdio, без порта) → HTTP fallback (:8765). Popup
// не знает, какой транспорт выбран.
//
// Скачивание: apiCall("flush") → text в ответе → Blob →
// chrome.downloads.download({saveAs:true}). Файл падает в «Загрузки»
// браузера, никакого доступа к ФС сервера.

const DEFAULT_PORT = 8765;
const DATE = new Date().toISOString().slice(0, 10);

// Все запросы к ядру — через background.js (native → HTTP fallback).
// popup не знает, через что идёт обмен: native stdio или HTTP.
async function apiCall(action, params = {}) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { type: "api", action, params },
      (resp) => {
        if (chrome.runtime.lastError) {
          resolve({ error: chrome.runtime.lastError.message });
        } else {
          resolve(resp || { error: "no response" });
        }
      }
    );
  });
}

function setStatus(text, isError = false) {
  const el = document.getElementById("status");
  el.textContent = text || "";
  el.classList.toggle("error", !!isError);
}

function setCount(n) {
  document.getElementById("count").textContent = String(n);
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "onclick") e.addEventListener("click", v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const c of children) {
    if (typeof c === "string") e.appendChild(document.createTextNode(c));
    else if (c) e.appendChild(c);
  }
  return e;
}

async function fetchItems() {
  const j = await apiCall("items.list");
  if (!j || j.error) {
    throw new Error(j?.error || "no response from backend");
  }
  return j;
}

async function deleteItem(sku) {
  const j = await apiCall("items.delete", { sku });
  if (!j || j.error) {
    throw new Error(j?.error || "delete failed");
  }
  return j;
}

async function downloadCollection(fmt) {
  const j = await apiCall("flush", { format: fmt });
  if (!j || j.error) {
    throw new Error(j?.error || "flush failed");
  }
  const text = j.text || "";
  const mime = fmt === "jsonl" ? "application/jsonl" : fmt === "csv" ? "text/csv" : "text/markdown";
  const ext = fmt === "markdown" ? "md" : fmt;
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const filename = `ozon-collection-${DATE}.${ext}`;
  await chrome.downloads.download({ url, filename, saveAs: true });
}

function renderList(items) {
  const list = document.getElementById("list");
  list.replaceChildren();
  setCount(items.length);

  if (items.length === 0) {
    list.appendChild(el("div", { class: "empty" },
      "Коллекция пуста. Открой Ozon PDP — она добавится сама."));
    return;
  }

  for (const it of items) {
    const title = it.title || it.sku;
    const meta = `${it.sku} · Q${it.quality ?? 0}/9`;
    const del = el("button", {
      class: "item-del",
      title: "Удалить",
      onclick: async () => {
        try {
          await deleteItem(it.sku);
          await reload();
          setStatus(`Удалён ${it.sku}`);
        } catch (e) {
          setStatus(String(e), true);
        }
      },
    }, "×");
    const main = el("div", { class: "item-main" },
      el("div", { class: "item-title", title },
        title),
      el("div", { class: "item-meta" }, meta));
    list.appendChild(el("div", { class: "item" }, main, del));
  }
}

async function reload() {
  try {
    setStatus("Загрузка...");
    const data = await fetchItems();
    renderList(data.items || []);
    setStatus("");
  } catch (e) {
    document.getElementById("list").replaceChildren();
    setCount(0);
    setStatus(String(e), true);
  }
}

async function clearAll() {
  if (!confirm("Удалить все товары из коллекции? Действие необратимо.")) {
    return;
  }
  try {
    const { items } = await fetchItems();
    let n = 0;
    for (const it of items) {
      await deleteItem(it.sku);
      n++;
    }
    await reload();
    setStatus(`Очищено: ${n}`);
  } catch (e) {
    setStatus(String(e), true);
  }
}

async function ingestActive() {
  const btn = document.getElementById("ingest");
  btn.disabled = true;
  setStatus("Ingest...");
  try {
    const res = await chrome.runtime.sendMessage({ type: "ingest-active" });
    if (!res || !res.status) {
      setStatus("Нет ответа от background", true);
    } else if (res.status === "added" || res.status === "updated") {
      setStatus(`${res.status}: ${res.sku}`);
      await reload();
    } else {
      setStatus(`${res.status}: ${res.error || ""}`, true);
    }
  } catch (e) {
    setStatus(String(e), true);
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("refresh").addEventListener("click", reload);
  document.getElementById("ingest").addEventListener("click", ingestActive);
  document.getElementById("clear").addEventListener("click", clearAll);
  document.getElementById("settings").addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
    window.close();
  });
  document.getElementById("download-md").addEventListener("click", () =>
    downloadCollection("markdown").catch((e) => setStatus(String(e), true)));
  document.getElementById("download-jsonl").addEventListener("click", () =>
    downloadCollection("jsonl").catch((e) => setStatus(String(e), true)));
  document.getElementById("download-csv").addEventListener("click", () =>
    downloadCollection("csv").catch((e) => setStatus(String(e), true)));
  reload();
});
