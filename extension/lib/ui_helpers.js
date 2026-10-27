// DOM-хелперы для UI-страниц расширения (popup, options, devtools).
// Чистые функции без побочных эффектов — легко тестировать в jsdom.

/**
 * Создать DOM-элемент.
 * @param {string} tag — имя тега
 * @param {object} attrs — атрибуты (class, onclick, title, ...)
 * @param {...(string|Node)} children — дети (строки → text nodes)
 */
export function el(tag, attrs = {}, ...children) {
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

/** Установить текст в #status (класс .error при isError). */
export function setStatus(text, isError = false) {
  const node = document.getElementById("status");
  if (!node) return;
  node.textContent = text || "";
  node.classList.toggle("error", !!isError);
}

/** Установить число в #count. */
export function setCount(n) {
  const node = document.getElementById("count");
  if (!node) return;
  node.textContent = String(n);
}
