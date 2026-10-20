// Options page logic. Читает настройки из chrome.storage.sync
// (порт) и GET /config у локального сервера (для отображения
// актуальных значений TOML-конфига).

const DEFAULT_PORT = 8765;

async function loadSettings() {
  const { port, autoIngest, notifyAutoIngest } =
    await chrome.storage.sync.get({
      port: DEFAULT_PORT,
      autoIngest: false,
      notifyAutoIngest: true,
    });
  document.getElementById("port").value = port;
  document.getElementById("auto").checked = !!autoIngest;
  document.getElementById("notify-auto").checked = !!notifyAutoIngest;
}

async function saveSettings() {
  const portVal = parseInt(document.getElementById("port").value, 10);
  const autoVal = document.getElementById("auto").checked;
  const notifyAutoVal = document.getElementById("notify-auto").checked;
  if (!Number.isFinite(portVal) || portVal < 1 || portVal > 65535) {
    setStatus("Порт должен быть 1..65535", true);
    return;
  }
  await chrome.storage.sync.set({
    port: portVal,
    autoIngest: autoVal,
    notifyAutoIngest: notifyAutoVal,
  });
  setStatus("Сохранено", false);
  await probeConfig();
  setTimeout(() => setStatus("", false), 2000);
  if (autoVal) {
    const note = notifyAutoVal ? "с уведомлениями" : "тихо (badge)";
    setStatus(`Сохранено; auto-ingest включён ${note}`, false);
  }
}

function setStatus(text, isError) {
  const el = document.getElementById("status");
  el.textContent = text;
  el.classList.toggle("error", !!isError);
}

async function probeConfig() {
  const el = document.getElementById("config-info");
  // Через background.js: native → HTTP fallback. Popup и Options не
  // ходят в сеть напрямую — им не важно, есть ли serve.
  const resp = await new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { type: "api", action: "health", params: {} },
      (r) => resolve(r || { error: "no response" })
    );
  });
  if (resp && resp.ok) {
    el.textContent = `Native: работает. Товаров: ${resp.count}.`;
    el.style.color = "#090";
  } else {
    el.textContent = `Нет связи с ядром (${resp?.error || "не установлено"}). Переустанови ozon-parser-setup.exe.`;
    el.style.color = "#c00";
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  await probeConfig();
  document.getElementById("save").addEventListener("click", saveSettings);
});
