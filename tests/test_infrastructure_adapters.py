"""Integration-тесты адаптеров infrastructure → domain ports.

Не unit: проверяют, что реальные адаптеры работают вместе с use case
без моков. Это доказательство, что Protocol-контракт соблюдён.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ozon_parser.application.ingest import IngestHtmlUseCase
from ozon_parser.infrastructure.notifier import DesktopNotifier
from ozon_parser.infrastructure.parsing.ozon import OzonHtmlParser
from ozon_parser.infrastructure.persistence.sqlite import (
    SqliteProductRepository,
)


# Валидный source-payload: len > MIN_HTML_LEN (500), ozon.ru, SKU 6 цифр
# (чтобы _SKU_HTML_RE мог сработать как fallback), все поля для quality.
VALID_HTML = (
    '<!DOCTYPE html>\n<html lang="ru"><head>\n  <title>T - Ozon</title>\n'
    '  <meta property="og:title" content="T">\n'
    '  <script type="application/ld+json">{"@context":"http://schema.org",'
    '"@type":"Product","name":"Test Product","sku":"777777",'
    '"description":"A test description for quality score",'
    '"image":"https://ir.ozone.ru/x.jpg",'
    '"aggregateRating":{"ratingValue":"4.7","reviewCount":"10"},'
    '"offers":{"price":"500","priceCurrency":"RUB",'
    '"url":"https://www.ozon.ru/product/777777/"}}</script>\n'
    '</head><body>\n  <div id="__ozon" data-server-rendered="true">\n'
    '    <h1>Test Product</h1>\n    <div id="section-characteristics">'
    '<dl><dt>K</dt><dd>V</dd></dl></div>\n'
    '    <!-- Filler to satisfy MIN_HTML_LEN=500 -->\n'
    '    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>\n'
    '    <p>Sed do eiusmod tempor incididunt ut labore et dolore magna.</p>\n'
    '    <p>Ut enim ad minim veniam, quis nostrud exercitation ullamco.</p>\n'
    '  </div>\n</body></html>'
)


# --- SqliteProductRepository --------------------------------------


def test_sqlite_repository_is_store_subclass() -> None:
    """Explicit adapter: issubclass, а не alias — читаемые traceback'и."""
    from ozon_parser.store import Store
    assert issubclass(SqliteProductRepository, Store)


def test_sqlite_repository_basic_flow(tmp_path: Path) -> None:
    repo = SqliteProductRepository(tmp_path / "c.db")
    try:
        is_new = repo.upsert(
            sku="123456", url="", info={"title": "T"},
            markdown="", quality=1, copy_type="source",
        )
        assert is_new is True
        assert repo.count() == 1
        assert repo.get("123456")["title"] == "T"
    finally:
        repo.close()


# --- OzonHtmlParser -----------------------------------------------


def test_ozon_html_parser_parses_valid_html() -> None:
    parser = OzonHtmlParser()
    md, info = parser.parse(VALID_HTML)
    assert isinstance(md, str)
    assert isinstance(info, dict)
    assert info.get("title") == "Test Product"
    assert info.get("sku") == "777777"


# --- DesktopNotifier ----------------------------------------------


def test_desktop_notifier_delegates_to_free_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ozon_parser.infrastructure import notifier as n

    calls: list[tuple[dict[str, Any], bool, int | None]] = []

    def fake_notify(info, *, is_new, store_count=None):
        calls.append((info, is_new, store_count))
        return True

    monkeypatch.setattr(n, "_notify_ingest", fake_notify)
    notifier = DesktopNotifier()
    assert notifier.notify_ingest({"title": "T"}, is_new=True) is True
    assert calls == [({"title": "T"}, True, None)]


# --- End-to-end: IngestHtmlUseCase + real adapters ----------------


def test_use_case_with_real_adapters(tmp_path: Path) -> None:
    """Самый важный тест: use case + реальные адаптеры работают вместе.

    Это гарантия, что Stage 5 (замена daemon/server/cli на use case)
    имеет работающий путь: HTML → SqliteProductRepository.
    """
    repo = SqliteProductRepository(tmp_path / "c.db")
    try:
        uc = IngestHtmlUseCase(
            parser=OzonHtmlParser(), repository=repo,
        )
        res = uc.execute(VALID_HTML)
        assert res.status == "added"
        assert res.sku == "777777"
        assert res.copy_type == "source"
        assert res.quality >= 5
        assert repo.count() == 1

        # Повторный ingest — updated, не дубль
        res2 = uc.execute(VALID_HTML)
        assert res2.status == "updated"
        assert repo.count() == 1
    finally:
        repo.close()
