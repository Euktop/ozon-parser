// Страница массового импорта ссылок.
// Отправляет список в background.js, слушает прогресс.

const startBtn = document.getElementById("start");
const cancelBtn = document.getElementById("cancel");
const statusEl = document.getElementById("status");
const urlsEl = document.getElementById("urls");
const delayEl = document.getElementById("delay");

let running = false;

// Загружаем ссылки, если они были собраны кнопкой "Собрать ссылки"
chrome.storage.session.get({ bulk_urls: [] }, (data) => {
  if (data.bulk_urls && data.bulk_urls.length > 0) {
    urlsEl.value = data.bulk_urls.join("\n");
    setStatus(`Загружено ${data.bulk_urls.length} ссылок с текущей страницы`);
    // Очищаем, чтобы при следующем открытии не дублировались
    chrome.storage.session.remove("bulk_urls");
  }
});

function setStatus(text, isError = false) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("error", !!isError);
}

function parseUrls(text) {
  const lines = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  const urls = [];
  for (const line of lines) {
    // Достаём все ozon.ru/product/... из строки (могут быть в markdown-ссылках)
    const matches = line.match(/https?:\/\/(?:www\.)?ozon\.ru\/product\/[^\s)\]"']+/g);
    if (matches) {
      urls.push(...matches);
    } else {
      // Если это просто «голый» URL без протокола
      if (/^(?:www\.)?ozon\.ru\/product\//.test(line)) {
        urls.push("https://" + line);
      }
    }
  }
  // Уникальные, сохраняя порядок
  return Array.from(new Set(urls));
}

startBtn.addEventListener("click", async () => {
  if (running) return;
  const urls = parseUrls(urlsEl.value);
  if (urls.length === 0) {
    setStatus("Нет валидных ссылок Ozon PDP", true);
    return;
  }
  const delay = parseInt(delayEl.value, 10) || 0;
  const concurrencyEl = document.getElementById("concurrency");
  const concurrency = parseInt(concurrencyEl?.value, 10) || 3;
  const fastMode = document.getElementById("fast-mode").checked;

  running = true;
  startBtn.disabled = true;
  cancelBtn.disabled = false;
  setStatus(`Запуск: ${urls.length} ссылок, ${concurrency} потоков...`);

  chrome.runtime.sendMessage(
    { type: "bulk-ingest", urls, delay, concurrency, fastMode },
    (resp) => {
      // Первый ответ — подтверждение запуска
      if (chrome.runtime.lastError) {
        setStatus(chrome.runtime.lastError.message, true);
        running = false;
        startBtn.disabled = false;
        cancelBtn.disabled = true;
      } else if (resp && resp.error) {
        setStatus(resp.error, true);
        running = false;
        startBtn.disabled = false;
        cancelBtn.disabled = true;
      }
    }
  );
});

cancelBtn.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "bulk-cancel" }, () => {
    setStatus("Отмена...");
  });
});

// Слушаем прогресс от background
chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "bulk-progress") return;
  const { done, total, ok, skip, err, finished, error, elapsed, waiting, detail } = msg;
  const elapsedStr = elapsed ? ` · ${elapsed}с` : "";
  const detailStr = detail ? ` · ${detail}` : "";
  if (waiting) {
    setStatus(
      `⚠️ Ждём прохождения капчи в открытой вкладке... (${done}/${total} обработано)`,
      true
    );
    return;
  }
  if (error) {
    setStatus(error, true);
  } else if (finished) {
    const avg = done > 0 ? Math.round((parseFloat(elapsed) * 1000) / done) : 0;
    setStatus(
      `Готово: ${ok} добавлено, ${skip} пропущено, ${err} ошибок ` +
      `(${done}/${total}) за ${elapsed}с · ${avg}мс/URL` + detailStr
    );
    running = false;
    startBtn.disabled = false;
    cancelBtn.disabled = true;
  } else {
    setStatus(
      `Обработка ${done}/${total}${elapsedStr}${detailStr}: ` +
      `${ok} ok, ${skip} skip, ${err} err`
    );
  }
});
