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
import struct
import sys
from typing import Any, BinaryIO

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


def _handle(msg: dict[str, Any], store: Store) -> dict[str, Any]:
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
    """Точка входа для `ozon-parser-native` / PyInstaller bundle."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = default_db_path()
    store = Store(db_path)
    try:
        while True:
            try:
                msg = _read_message(sys.stdin.buffer)
            except (ValueError, json.JSONDecodeError) as e:
                LOG.warning("bad message: %s", e)
                break
            if msg is None:
                break
            try:
                resp = _handle(msg, store)
            except Exception as e:
                LOG.exception("handle failed")
                resp = {"error": str(e)}
            if "id" in msg:
                resp["id"] = msg["id"]
            _write_message(sys.stdout.buffer, resp)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
