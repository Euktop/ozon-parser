"""Native Messaging host: stdio-цикл, dispatch в use cases.

Chrome Native Messaging protocol:
  - stdin:  4 байта little-endian длины + UTF-8 JSON
  - stdout: 4 байта little-endian длины + UTF-8 JSON
  - stderr: свободно для логов (Chrome игнорирует)

Это inbound adapter, аналог server.py по роли. Ядро (domain/application)
не знает, HTTP это или stdio — здесь только маршрутизация сообщений в
use case и сериализация результата обратно.

Действия (action в JSON):
  - health          → {ok, count}
  - items.list      → {items: [...]}
  - items.get       → {item: dict|None}
  - items.delete    → {deleted: bool, sku}
  - ingest          → {status, sku, quality, ...}  (IngestHtmlUseCase)
  - flush           → {text, format, count}

Сообщения с полем id — эхо в ответе (для матчинга на клиенте).
"""
from __future__ import annotations

import json
import logging
import os
import struct
import sys
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, BinaryIO, Callable

from ozon_parser.application.ingest import IngestHtmlUseCase
from ozon_parser.infrastructure.parsing.ozon import OzonHtmlParser
from ozon_parser.render import render_collection
from ozon_parser.store import Store, default_db_path

LOG = logging.getLogger("ozon_parser.native_host")

# Совпадает с server.MAX_BODY_BYTES. Chrome режет сообщения >1 MB по умолчанию,
# но 32 MB — верхняя граница для тех, кто увеличил лимит в браузере.
MAX_MESSAGE_SIZE = 32 * 1024 * 1024


def _read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Читает 4-byte length + JSON. None на EOF или truncated."""
    raw_len = stream.read(4)
    if len(raw_len) < 4:
        return None
    (length,) = struct.unpack("<I", raw_len)
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(f"message too large: {length} bytes")
    data = stream.read(length)
    if len(data) < length:
        return None
    return json.loads(data.decode("utf-8"))


def _write_message(stream: BinaryIO, obj: dict[str, Any]) -> None:
    """Пишет 4-byte length + JSON + flush (Chrome ждёт полный фрейм)."""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(body)))
    stream.write(body)
    stream.flush()


def _parse_helper(html: str, include_images: bool) -> tuple[str, dict[str, Any]]:
    """Top-level wrapper для ProcessPoolExecutor.

    Должна быть picklable: только так ``submit(_parse_helper, ...)``
    сможет отправить работу в дочерний процесс.
    """
    from ozon_parser.parser import parse_product
    return parse_product(html, include_images=include_images)


def _handle(
    msg: dict[str, Any],
    store: Store,
    parse_func: Callable[[str, bool], tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Маршрутизация одного сообщения. Без исключений — ошибки в {"error": ...}."""
    action = msg.get("action")

    if action == "health":
        return {"ok": True, "count": store.count()}

    if action == "items.list":
        return {"items": store.list()}

    if action == "items.get":
        sku = str(msg.get("sku") or "").strip()
        if not sku:
            return {"error": "missing sku"}
        return {"item": store.get(sku)}

    if action == "items.delete":
        sku = str(msg.get("sku") or "").strip()
        if not sku:
            return {"error": "missing sku"}
        return {"deleted": store.delete(sku), "sku": sku}

    if action == "items.clear":
        # Bulk clear: один вызов вместо N × items.delete — атомарно и быстро.
        return {"cleared": store.clear()}

    if action == "ingest":
        html = msg.get("html") or ""
        if not isinstance(html, str):
            return {"error": "html must be a string"}
        uc = IngestHtmlUseCase(
            parser=OzonHtmlParser(),
            repository=store,
            store_raw_html=bool(msg.get("store_raw_html", True)),
            include_images=bool(msg.get("include_images", False)),
            parse_func=parse_func,
        )
        result = uc.execute(html, url_hint=str(msg.get("url") or ""))
        out = result.to_dict()
        out["title"] = (result.info.get("title") or "")[:120]
        return out

    if action == "flush":
        fmt = str(msg.get("format") or "markdown")
        items = list(store.iter_all(include_raw=False))
        try:
            text = render_collection(items, fmt)
        except ValueError as e:
            return {"error": str(e)}
        return {"text": text, "format": fmt, "count": len(items)}

    return {"error": f"unknown action: {action!r}"}


def main() -> int:
    """Точка входа для `ozon-parser-native` / PyInstaller bundle.

    Параллелизм:
      - stdin читается в одном потоке (единственный читатель pipe).
      - Каждое сообщение → ThreadPoolExecutor (I/O: JSON, SQLite, gzip).
      - Парсинг HTML → ProcessPoolExecutor (реальный CPU-параллелизм,
        обход GIL: каждый процесс на своём ядре).
      - Ответы в stdout под глобальным lock (pipe не потокобезопасен).

    Store остаётся в главном процессе: SQLite `check_same_thread=False` +
    `RLock` уже thread-safe, commit'ы короткие (~50 мс) и сериализуются
    только на запись.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = default_db_path()
    store = Store(db_path)

    cpu = os.cpu_count() or 2
    # Оставляем 1 ядро браузеру + главному stdin-циклу.
    proc_workers = max(1, min(6, cpu - 1))
    # Потоков больше, чем процессов: пока N потоков ждут .result() из пула,
    # остальные обслуживают I/O (SQLite, JSON, gzip).
    thread_workers = max(4, proc_workers * 2)
    LOG.warning(
        "[native] parallel: proc_workers=%d thread_workers=%d (cpu=%d)",
        proc_workers, thread_workers, cpu,
    )

    proc_pool = ProcessPoolExecutor(max_workers=proc_workers)
    thread_pool = ThreadPoolExecutor(max_workers=thread_workers)
    write_lock = threading.Lock()

    def _parse_func(html: str, include_images: bool) -> tuple[str, dict[str, Any]]:
        # submit → реальный CPU-параллелизм (parse_product в дочернем процессе).
        # .result() отпускает GIL этого потока: другие потоки продолжают работу.
        return proc_pool.submit(_parse_helper, html, include_images).result(timeout=60)

    def _dispatch(msg: dict[str, Any]) -> dict[str, Any]:
        try:
            return _handle(msg, store, parse_func=_parse_func)
        except Exception as e:
            LOG.exception("handle failed")
            return {"error": str(e)}

    def _submit_and_reply(msg: dict[str, Any]) -> None:
        fut = thread_pool.submit(_dispatch, msg)

        def _reply(f) -> None:
            try:
                resp = f.result()
            except Exception as e:
                resp = {"error": f"dispatch: {e}"}
            if "id" in msg:
                resp["id"] = msg["id"]
            with write_lock:
                try:
                    _write_message(sys.stdout.buffer, resp)
                except Exception as e:
                    LOG.warning("write failed: %s", e)

        fut.add_done_callback(_reply)

    try:
        while True:
            try:
                msg = _read_message(sys.stdin.buffer)
            except (ValueError, json.JSONDecodeError) as e:
                LOG.warning("bad message: %s", e)
                break
            if msg is None:
                break
            _submit_and_reply(msg)
    finally:
        # Дождаться in-flight потоков/процессов, потом закрыть БД.
        thread_pool.shutdown(wait=True)
        proc_pool.shutdown(wait=True)
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
