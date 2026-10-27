// Определение Chromium-браузера по User-Agent.
//
// Yandex Browser маскируется под Chrome, но добавляет "YaBrowser/x.y.z".
// Аналогично: Edge ("Edg/"), Opera ("OPR/"), Vivaldi ("Vivaldi/").
// Brave не отражается в UA (privacy), его можно определить по
// navigator.brave.isBrave() — но это асинхронно, не нужно для диагностики.
//
// Возвращаемое значение: "yandex" | "edge" | "opera" | "vivaldi" |
// "chrome" | "unknown".

export function detectBrowser() {
  const ua = (navigator && navigator.userAgent) || "";
  if (ua.includes("YaBrowser/")) return "yandex";
  if (ua.includes("Edg/")) return "edge";
  if (ua.includes("OPR/") || ua.includes("Opera/")) return "opera";
  if (ua.includes("Vivaldi/")) return "vivaldi";
  if (ua.includes("Chrome/")) return "chrome";
  return "unknown";
}

export function browserDisplayName(id) {
  const names = {
    yandex: "Яндекс Браузер",
    edge: "Microsoft Edge",
    opera: "Opera",
    vivaldi: "Vivaldi",
    chrome: "Google Chrome",
    unknown: "Unknown Chromium",
  };
  return names[id] || id;
}
