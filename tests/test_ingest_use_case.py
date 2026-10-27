"""Тесты IngestHtmlUseCase на FakeRepo + FakeParser.

Проверяют use case без SQLite и без BeautifulSoup — вся оркестрация
тестируется изолированно. Это и есть главный выигрыш от вынесения
логики из daemon/server/cli в application.
"""
from __future__ import annotations

import gzip
from typing import Any

import pytest

from ozon_parser.application.ingest import (
    IngestHtmlUseCase,
    IngestResult,
    _resolve_sku,
)


# Минимальный валидный payload: проходит is_usable_ozon_copy.
# ВАЖНО: len(VALID_HTML) должно быть >= MIN_HTML_LEN (500) из
# domain/detection.py + содержать "ozon.ru". SKU "777" в JSON-LD — 3
# цифры, _SKU_HTML_RE (\d{6,}) его НЕ матчит — это позволяет
# test_execute_no_sku проверять no_sku при parser, не отдающем sku.
VALID_HTML = (
    '<!DOCTYPE html>\n<html lang="ru"><head>\n  <title>T - Ozon</title>\n'
    '  <meta property="og:title" content="T">\n'
    '  <script type="application/ld+json">{"@context":"http://schema.org",'
    '"@type":"Product","name":"T","sku":"777",'
    '"description":"D","image":"https://ir.ozone.ru/x.jpg",'
    '"offers":{"price":"500","priceCurrency":"RUB",'
    '"url":"https://www.ozon.ru/product/777/"}}</script>\n'
    '</head><body>\n  <div id="__ozon" data-server-rendered="true">\n'
    '    <h1>T</h1>\n    <div id="section-characteristics">'
    '<dl><dt>K</dt><dd>V</dd></dl></div>\n'
    '    <!-- Filler text: гарантирует len(VALID_HTML) > MIN_HTML_LEN. -->\n'
    '    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>\n'
    '    <p>Sed do eiusmod tempor incididunt ut labore et dolore magna.</p>\n'
    '    <p>Ut enim ad minim veniam, quis nostrud exercitation ullamco.</p>\n'
    '  </div>\n</body></html>'
)


class FakeRepo:
    """In-memory ProductRepository. Без SQLite."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.upsert_calls: list[dict[str, Any]] = []

    def upsert(self, **kwargs: Any) -> bool:
        self.upsert_calls.append(kwargs)
        sku = kwargs["sku"]
        is_new = sku not in self.items
        self.items[sku] = kwargs
        return is_new

    def get(self, sku: str) -> dict[str, Any] | None:
        return self.items.get(sku)

    def iter_all(self, **kwargs: Any):
        yield from self.items.values()

    def count(self) -> int:
        return len(self.items)


class FakeParser:
    """HtmlParser, возвращающий заданные (md, info) или бросающий."""

    def __init__(
        self,
        result: tuple[str, dict[str, Any]] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._result = result
        self._exc = exc
        self.calls: list[tuple[str, bool]] = []

    def parse(
        self, html: str, *, include_images: bool = False
    ) -> tuple[str, dict[str, Any]]:
        self.calls.append((html, include_images))
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def notify_ingest(
        self, info: dict, *, is_new: bool, store_count: int | None = None
    ) -> bool:
        self.calls.append(
            {"info": info, "is_new": is_new, "store_count": store_count}
        )
        return True


# --- _resolve_sku ----------------------------------------------------


def test_resolve_sku_from_info() -> None:
    assert _resolve_sku({"sku": "123"}, "anything") == "123"


def test_resolve_sku_from_url_no_trailing_slash() -> None:
    info = {"url": "https://www.ozon.ru/product/foo-456"}
    assert _resolve_sku(info, "") == "456"


def test_resolve_sku_from_html() -> None:
    # SKU Ozon — длинные; regex требует \d{6,} (см. daemon.py legacy).
    html = '{"sku": "789123", "title": "X"}'
    assert _resolve_sku({}, html) == "789123"


def test_resolve_sku_empty() -> None:
    assert _resolve_sku({}, "no sku here") == ""


# --- execute: успешные пути -------------------------------------------


def test_execute_added() -> None:
    repo = FakeRepo()
    parser = FakeParser(
        result=("# MD\n", {"sku": "777", "title": "T", "url": "https://ozon.ru/p/777"})
    )
    uc = IngestHtmlUseCase(parser=parser, repository=repo)
    res = uc.execute(VALID_HTML)
    assert res.status == "added"
    assert res.sku == "777"
    assert res.copy_type == "source"
    assert res.quality > 0
    assert res.raw_html_size > 0
    assert len(repo.items) == 1


def test_execute_updated() -> None:
    repo = FakeRepo()
    parser = FakeParser(
        result=("# MD\n", {"sku": "777", "title": "T"})
    )
    uc = IngestHtmlUseCase(parser=parser, repository=repo)
    uc.execute(VALID_HTML)
    res = uc.execute(VALID_HTML)
    assert res.status == "updated"


def test_execute_url_hint_used_when_info_has_no_url() -> None:
    repo = FakeRepo()
    parser = FakeParser(result=("# MD\n", {"sku": "777", "title": "T"}))
    uc = IngestHtmlUseCase(parser=parser, repository=repo)
    uc.execute(VALID_HTML, url_hint="https://fallback.example/p/777")
    assert repo.items["777"]["url"] == "https://fallback.example/p/777"


def test_execute_store_raw_html_false_skips_gzip() -> None:
    repo = FakeRepo()
    parser = FakeParser(result=("# MD\n", {"sku": "777"}))
    uc = IngestHtmlUseCase(
        parser=parser, repository=repo, store_raw_html=False
    )
    res = uc.execute(VALID_HTML)
    assert res.status == "added"
    assert res.raw_html_size == 0
    assert repo.items["777"]["raw_html"] is None


def test_execute_uses_parse_func_when_provided() -> None:
    """parse_func перекрывает self._parser.parse (для ProcessPoolExecutor)."""
    repo = FakeRepo()
    called: list[tuple[str, bool]] = []

    def fake_parse(html: str, include_images: bool):
        called.append((html, include_images))
        return ("# MD\n", {"sku": "777", "title": "T"})

    uc = IngestHtmlUseCase(
        parser=FakeParser(result=("# OTHER\n", {"sku": "999"})),
        repository=repo,
        parse_func=fake_parse,
    )
    res = uc.execute(VALID_HTML)
    assert res.status == "added"
    assert res.sku == "777"  # из fake_parse, не из parser'а
    assert len(called) == 1
    assert called[0][0] == VALID_HTML


def test_execute_include_images_passed_to_parser() -> None:
    repo = FakeRepo()
    parser = FakeParser(result=("# MD\n", {"sku": "777"}))
    uc = IngestHtmlUseCase(
        parser=parser, repository=repo, include_images=True
    )
    uc.execute(VALID_HTML)
    assert parser.calls[0][1] is True


def test_execute_raw_html_is_valid_gzip() -> None:
    repo = FakeRepo()
    parser = FakeParser(result=("# MD\n", {"sku": "777"}))
    uc = IngestHtmlUseCase(parser=parser, repository=repo)
    uc.execute(VALID_HTML)
    raw = repo.items["777"]["raw_html"]
    assert isinstance(raw, bytes)
    assert gzip.decompress(raw).decode("utf-8") == VALID_HTML


# --- execute: notifier ------------------------------------------------


def test_execute_notifier_called() -> None:
    repo = FakeRepo()
    parser = FakeParser(result=("# MD\n", {"sku": "777", "title": "T"}))
    notifier = FakeNotifier()
    uc = IngestHtmlUseCase(
        parser=parser, repository=repo, notifier=notifier
    )
    uc.execute(VALID_HTML)
    assert len(notifier.calls) == 1
    assert notifier.calls[0]["is_new"] is True
    assert notifier.calls[0]["store_count"] == 1


def test_execute_notifier_error_does_not_break_ingest() -> None:
    repo = FakeRepo()
    parser = FakeParser(result=("# MD\n", {"sku": "777"}))

    class BoomNotifier:
        def notify_ingest(self, *a, **kw):
            raise RuntimeError("notifier boom")

    uc = IngestHtmlUseCase(
        parser=parser, repository=repo, notifier=BoomNotifier()
    )
    res = uc.execute(VALID_HTML)
    assert res.status == "added"
    assert len(repo.items) == 1


# --- execute: негативные пути ----------------------------------------


def test_execute_skipped_for_short_payload() -> None:
    repo = FakeRepo()
    parser = FakeParser(result=("# MD\n", {"sku": "1"}))
    uc = IngestHtmlUseCase(parser=parser, repository=repo)
    res = uc.execute("<html>too short</html>")
    assert res.status == "skipped"
    assert parser.calls == []  # parse не вызывался
    assert len(repo.items) == 0


def test_execute_error_on_parser_exception() -> None:
    repo = FakeRepo()
    parser = FakeParser(exc=RuntimeError("parse boom"))
    uc = IngestHtmlUseCase(parser=parser, repository=repo)
    res = uc.execute(VALID_HTML)
    assert res.status == "error"
    assert "parse boom" in res.error
    assert len(repo.items) == 0


def test_execute_no_sku() -> None:
    repo = FakeRepo()
    parser = FakeParser(result=("# MD\n", {"title": "T"}))
    uc = IngestHtmlUseCase(parser=parser, repository=repo)
    res = uc.execute(VALID_HTML)
    assert res.status == "no_sku"
    assert res.info == {"title": "T"}
    assert len(repo.items) == 0


# --- IngestResult.to_dict ---------------------------------------------


def test_result_to_dict() -> None:
    r = IngestResult(
        status="added", sku="1", copy_type="source", quality=9,
        raw_html_size=42, payload_len=1000,
    )
    d = r.to_dict()
    assert d["status"] == "added"
    assert d["raw_html_size"] == 42
    assert "info" not in d  # info не утекает в HTTP-ответ


def test_result_to_dict_error() -> None:
    r = IngestResult(status="error", error="boom")
    d = r.to_dict()
    assert d["status"] == "error"
    assert d["error"] == "boom"
