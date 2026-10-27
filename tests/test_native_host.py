"""Тесты Native Messaging host: framing + dispatch.

Framing (4-byte length + JSON) проверяется на BytesIO — без реальных
stdin/stdout. Dispatch — через Store на tmp_path, без сети и Chrome.
"""
from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest

from ozon_parser.presentation.native_host import (
    MAX_MESSAGE_SIZE,
    _handle,
    _read_message,
    _write_message,
)
from ozon_parser.store import Store


# Валидный payload: len > MIN_HTML_LEN (500) + "ozon.ru", SKU 777 из JSON-LD.
VALID_HTML = (
    '<!DOCTYPE html>\n<html lang="ru"><head>\n  <title>T - Ozon</title>\n'
    '  <meta property="og:title" content="T">\n'
    '  <script type="application/ld+json">{"@context":"http://schema.org",'
    '"@type":"Product","name":"T","sku":"777",'
    '"description":"D","image":"https://ir.ozone.ru/x.jpg",'
    '"aggregateRating":{"ratingValue":"4.7","reviewCount":"10"},'
    '"offers":{"price":"500","priceCurrency":"RUB",'
    '"url":"https://www.ozon.ru/product/777/"}}</script>\n'
    '</head><body>\n  <div id="__ozon" data-server-rendered="true">\n'
    '    <h1>T</h1>\n    <div id="section-characteristics">'
    '<dl><dt>K</dt><dd>V</dd></dl></div>\n'
    '    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>\n'
    '    <p>Sed do eiusmod tempor incididunt ut labore et dolore magna.</p>\n'
    '    <p>Ut enim ad minim veniam, quis nostrud exercitation ullamco.</p>\n'
    '  </div>\n</body></html>'
)


# --- framing -----------------------------------------------------------


def _framed(obj: dict) -> bytes:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def test_read_message_valid() -> None:
    bio = io.BytesIO(_framed({"action": "health", "id": 1}))
    assert _read_message(bio) == {"action": "health", "id": 1}


def test_read_message_eof() -> None:
    assert _read_message(io.BytesIO(b"")) is None


def test_read_message_truncated_length() -> None:
    # Меньше 4 байт — фрейм ещё не начался.
    assert _read_message(io.BytesIO(b"\x01\x02")) is None


def test_read_message_truncated_body() -> None:
    # Длина 16, тела нет.
    assert _read_message(io.BytesIO(b"\x10\x00\x00\x00")) is None


def test_read_message_too_large() -> None:
    bio = io.BytesIO(struct.pack("<I", MAX_MESSAGE_SIZE + 1))
    with pytest.raises(ValueError, match="too large"):
        _read_message(bio)


def test_write_read_roundtrip() -> None:
    buf = io.BytesIO()
    _write_message(buf, {"ok": True, "n": 42, "unicode": "Привет"})
    buf.seek(0)
    assert _read_message(buf) == {"ok": True, "n": 42, "unicode": "Привет"}


# --- dispatch ----------------------------------------------------------


def test_handle_health(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        r = _handle({"action": "health"}, s)
        assert r["ok"] is True
        assert r["count"] == 0


def test_handle_items_list_empty(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        assert _handle({"action": "items.list"}, s)["items"] == []


def test_handle_items_list_after_upsert(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        s.upsert(sku="1", url="", info={"title": "T"},
                 markdown="", quality=1, copy_type="source")
        items = _handle({"action": "items.list"}, s)["items"]
        assert len(items) == 1
        assert items[0]["sku"] == "1"


def test_handle_items_get_missing(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        assert _handle({"action": "items.get", "sku": "none"}, s)["item"] is None


def test_handle_items_get_missing_sku(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        r = _handle({"action": "items.get"}, s)
        assert r.get("error") == "missing sku"


def test_handle_items_delete(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        s.upsert(sku="1", url="", info={"title": "T"},
                 markdown="", quality=1, copy_type="source")
        r = _handle({"action": "items.delete", "sku": "1"}, s)
        assert r["deleted"] is True
        assert s.count() == 0


def test_handle_items_clear(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        for i in range(3):
            s.upsert(sku=str(i), url="", info={"title": f"T{i}"},
                     markdown="", quality=1, copy_type="source")
        r = _handle({"action": "items.clear"}, s)
        assert r["cleared"] == 3
        assert s.count() == 0


def test_handle_ingest_added(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        r = _handle(
            {"action": "ingest", "html": VALID_HTML,
             "url": "https://www.ozon.ru/product/777/"},
            s,
        )
        assert r["status"] == "added"
        assert r["sku"] == "777"
        assert s.count() == 1


def test_handle_ingest_with_parse_func(tmp_path: Path) -> None:
    """parse_func прокидывается в use case (эмуляция ProcessPoolExecutor)."""
    with Store(tmp_path / "c.db") as s:
        called: list[str] = []

        def fake_parse(html: str, include_images: bool):
            called.append(html)
            return ("# MD\n", {"sku": "777", "title": "T"})

        r = _handle(
            {"action": "ingest", "html": VALID_HTML},
            s,
            parse_func=fake_parse,
        )
        assert r["status"] == "added"
        assert r["sku"] == "777"
        assert len(called) == 1


def test_handle_ingest_skipped(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        r = _handle({"action": "ingest", "html": "short"}, s)
        assert r["status"] == "skipped"


def test_handle_flush(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        s.upsert(sku="1", url="", info={"title": "T"},
                 markdown="# T\n", quality=9, copy_type="source")
        r = _handle({"action": "flush", "format": "markdown"}, s)
        assert "# Ozon collection" in r["text"]
        assert r["count"] == 1


def test_handle_flush_bad_format(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        r = _handle({"action": "flush", "format": "bogus"}, s)
        assert "error" in r


def test_handle_unknown_action(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        r = _handle({"action": "bogus"}, s)
        assert "unknown action" in r["error"]
