// Обёртка над chrome.runtime.sendMessage для UI-страниц (popup, options).
//
// ВАЖНО: это НЕ тот же apiCall, что в lib/api_client.js.
//   - lib/api_client.js   — внутри background.js, зовёт native/HTTP напрямую.
//   - lib/message_bus.js  — в popup/options, шлёт {type:"api"} в background,
//                            который уже выбирает транспорт.
//
// Попап и options не имеют прямого доступа к native port — они живут
// в отдельном контексте (popup iframe, options page). Единственный
// мост — chrome.runtime.sendMessage.

export function apiCall(action, params = {}) {
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
