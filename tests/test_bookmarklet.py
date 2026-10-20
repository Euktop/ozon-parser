"""Тесты генератора букмарклета."""

from __future__ import annotations

from ozon_parser.bookmarklet import generate_bookmarklet


def test_generate_basic() -> None:
    js = generate_bookmarklet("http://127.0.0.1:8765")
    assert js.startswith("javascript:")
    assert "http://127.0.0.1:8765" in js
    assert "/ingest" in js
    assert "location.href" in js
    assert "fetch" in js


def test_generate_trims_trailing_slash() -> None:
    js = generate_bookmarklet("http://127.0.0.1:8765/")
    assert "http://127.0.0.1:8765/" not in js
    assert "http://127.0.0.1:8765" in js


def test_generate_uses_post() -> None:
    js = generate_bookmarklet("http://localhost:9000")
    assert "method:'POST'" in js
