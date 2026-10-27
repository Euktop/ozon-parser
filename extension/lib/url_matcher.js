// Общие проверки URL для Ozon.
// Вынесено из background.js, чтобы использовать в popup / content-script.

export function isOzonUrl(url) {
  return typeof url === "string" && url.includes("ozon.ru");
}

export function isOzonPdp(url) {
  return (
    typeof url === "string" &&
    /^https?:\/\/(www\.)?ozon\.ru\/product\//.test(url)
  );
}
