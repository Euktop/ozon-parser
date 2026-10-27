// Фасад: Native (stdio) → HTTP (fallback).
// Вызывающий код (popup, background) не знает, какой транспорт выбран.

import { nativeCall } from "./native_client.js";
import { httpCall } from "./http_client.js";

export async function apiCall(action, params = {}) {
  const native = await nativeCall(action, params);
  if (native !== null) return native;
  return await httpCall(action, params);
}
