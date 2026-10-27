"""Фоновый сбор Ozon PDP из буфера обмена.

Ядро новой архитектуры: daemon читает клипборд, распознаёт Ozon PDP,
парсит, складывает в SQLite. Буфер обмена НЕ затирается — это
ключевое отличие от legacy ozon_clip.py.

Чтение клипборда вынесено в пакет `ozon_parser/clipboard/`:
Windows — pywin32 CF_HTML, macOS — pbpaste + osascript,
Linux — wl-paste / xclip / xsel, fallback — pyperclip.
`read_text_clipboard` / `read_html_clipboard` — тонкие шимы над бэкендом
(сохранены как публичные имена для обратной совместимости и тестов).

Дедупликация: сравниваем хэш текущего (text, html) с последним
обработанным. Это отсекает повторные poll'ы одного и того же
содержимого, но НЕ мешает переобработать тот же PDP, если между
копиями в буфер попадало что-то другое (обновление записи).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any, Callable

from ozon_parser.application.ingest import IngestHtmlUseCase
from ozon_parser.clipboard import get_backend
from ozon_parser.domain.detection import detect_copy_type, is_usable_ozon_copy
from ozon_parser.infrastructure.parsing.ozon import OzonHtmlParser
from ozon_parser.store import Store

LOG = logging.getLogger("ozon_parser.daemon")

POLL_INTERVAL = 1.0


# ============================================================================
#  clipboard (thin shims over ozon_parser.clipboard)
# ============================================================================


def read_text_clipboard() -> str:
    """Читает plain-text из клипборда через выбранный бэкенд."""
    return get_backend().read_text()


def read_html_clipboard() -> str | None:
    """Читает rich HTML из клипборда (если бэкенд поддерживает)."""
    return get_backend().read_html()


# ============================================================================
#  pure helpers
# ============================================================================


def _sig(text: str, html: str | None) -> str:
    return hashlib.sha1(
        ((text or "") + "\u0000" + (html or "")).encode("utf-8", "ignore")
    ).hexdigest()


def collect_candidates(
    text: str, html: str | None
) -> list[tuple[str, str, str]]:
    """[(source, payload, copy_type), ...] в порядке приоритета.

    source > rendered; rich-html > plain-html.
    """
    order_copy = {"source": 0, "rendered": 1, "unknown": 2}
    order_fmt = {"rich-html": 0, "plain-html": 1}
    out: list[tuple[str, str, str]] = []
    if html and is_usable_ozon_copy(html):
        out.append(("rich-html", html, detect_copy_type(html)))
    if text and is_usable_ozon_copy(text):
        out.append(("plain-html", text, detect_copy_type(text)))
    out.sort(key=lambda c: (order_copy.get(c[2], 3), order_fmt.get(c[0], 2)))
    return out


# ============================================================================
#  poll_once — один проход
# ============================================================================


def poll_once(
    store: Store,
    *,
    last_hash: str | None = None,
    text: str | None = None,
    html: str | None = None,
    include_images: bool = False,
    store_raw_html: bool = True,
    use_case: IngestHtmlUseCase | None = None,
) -> dict[str, Any]:
    """Один проход по буферу.

    `text`/`html` = None → читаем из клипборда. Иначе — используется
    переданное (для тестов, не трогающих реальный буфер).

    `use_case` — инъекция для тестов; если None — создаётся IngestHtmlUseCase
    с OzonHtmlParser и переданными store_raw_html / include_images.

    Транспортная логика (чтение клипборда, дедуп по hash, выбор rich/plain,
    on_ingest callback) остаётся здесь. Оркестрация parse+quality+SKU+upsert —
    в use case.

    Возвращает dict:
        status: 'added' | 'updated' | 'deduped' | 'skipped' | 'no_sku' | 'error'
        hash:   sha1 текущего (text, html)
        payload_len: max(len(text), len(html))
        info, sku, copy_type, quality — если был успешный parse
    """
    if text is None:
        text = read_text_clipboard()
    if html is None:
        html = read_html_clipboard()

    text = text or ""
    sig = _sig(text, html)
    payload_len = max(len(text), len(html or ""))

    if last_hash is not None and sig == last_hash:
        return {"status": "deduped", "hash": sig, "payload_len": payload_len}

    candidates = collect_candidates(text, html)
    if not candidates:
        return {"status": "skipped", "hash": sig, "payload_len": payload_len}

    payload = candidates[0][1]

    if use_case is None:
        use_case = IngestHtmlUseCase(
            parser=OzonHtmlParser(),
            repository=store,
            store_raw_html=store_raw_html,
            include_images=include_images,
        )
    result = use_case.execute(payload)

    out = result.to_dict()
    out["hash"] = sig
    out["raw_html_size"] = result.raw_html_size
    if result.info:
        out["info"] = result.info
    return out


# ============================================================================
#  run_forever
# ============================================================================


def _log_poll_result(res: dict[str, Any]) -> None:
    st = res.get("status")
    if st in ("added", "updated"):
        info = res.get("info") or {}
        LOG.info(
            "[%s] %s — %s (quality %d/9, %s)",
            st,
            res.get("sku"),
            (info.get("title") or "")[:60],
            res.get("quality") or 0,
            res.get("copy_type") or "?",
        )
    elif st == "no_sku":
        LOG.warning("распознан Ozon PDP, но SKU не определён — пропущено")
    elif st == "skipped":
        n = res.get("payload_len") or 0
        if n > 500:
            LOG.info("в буфере %d симв., но не Ozon PDP — пропущено", n)
        else:
            LOG.debug("буфер не Ozon PDP — пропущено (len=%d)", n)
    elif st == "deduped":
        LOG.debug("буфер не изменился — пропущено")
    elif st == "error":
        LOG.warning("parse error: %s", res.get("error") or "?")


def run_forever(
    store: Store,
    *,
    interval: float = POLL_INTERVAL,
    stop_event: threading.Event | None = None,
    include_images: bool = False,
    store_raw_html: bool = True,
    on_ingest: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Бесконечный цикл poll_once(). Останавливается по stop_event.

    `on_ingest` — callback для side-effects после успешного добавления/
    обновления (например, toast). Вызывается только для status in
    ('added', 'updated'). Исключения из callback'а логируются и не
    прерывают цикл.
    """
    if stop_event is None:
        stop_event = threading.Event()
    last_hash: str | None = None
    LOG.info("daemon started, interval=%.2fs", interval)
    try:
        while not stop_event.is_set():
            try:
                res = poll_once(
                    store,
                    last_hash=last_hash,
                    include_images=include_images,
                    store_raw_html=store_raw_html,
                )
            except Exception as e:
                LOG.exception("poll_once failed: %s", e)
                res = None
            if res is not None:
                last_hash = res.get("hash", last_hash)
                _log_poll_result(res)
                if (
                    on_ingest is not None
                    and res.get("status") in ("added", "updated")
                ):
                    try:
                        on_ingest(res)
                    except Exception as e:
                        LOG.exception("on_ingest callback failed: %s", e)
            stop_event.wait(interval)
    finally:
        LOG.info("daemon stopped")
