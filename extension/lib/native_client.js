// Native Messaging клиент. Держит persistent port, шлёт JSON.
// Chrome/Yandex запускают host-процесс при connectNative и убивают
// при disconnect. Мы держим один порт между вызовами.

// 10 с: нативный host теперь параллелит парсинг через ProcessPoolExecutor.
// Первый ingest после старта host'а включает spawn процессов (~1-2 с),
// последующие — 200-500 мс. 5 с было бы впритык.
const NATIVE_TIMEOUT_MS = 10000;

let _nativePort = null;

export function _getNativePort() {
  if (_nativePort) return _nativePort;
  try {
    _nativePort = chrome.runtime.connectNative("com.ozon_parser");
    _nativePort.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError;
      if (err) {
        console.warn("[ozon] native host disconnected:", err.message);
      }
      _nativePort = null;
    });
    return _nativePort;
  } catch (e) {
    _nativePort = null;
    return null;
  }
}

export async function nativeCall(action, params) {
  const port = _getNativePort();
  if (!port) return null;
  return new Promise((resolve) => {
    let done = false;
    const id = (crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : String(Date.now()) + "-" + Math.random();
    const finish = (val) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { port.onMessage.removeListener(handler); } catch (_) {}
      resolve(val);
    };
    const handler = (msg) => {
      if (msg && msg.id === id) finish(msg);
    };
    const timer = setTimeout(() => finish(null), NATIVE_TIMEOUT_MS);
    port.onMessage.addListener(handler);
    try {
      port.postMessage({ action, ...params, id });
    } catch (e) {
      _nativePort = null;
      finish(null);
    }
  });
}

export function disconnectNative() {
  if (_nativePort) {
    try { _nativePort.disconnect(); } catch (_) {}
    _nativePort = null;
  }
}
