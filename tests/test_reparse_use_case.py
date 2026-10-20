"""Тесты ReparseUseCase на FakeRepo + FakeParser.

Проверяют use case без SQLite и без BeautifulSoup — вся оркестрация
reparse тестируется изолированно (2-фазный streaming, error aggregation,
progress callback).
"""
from __future__ import annotations

import gzip
from typing import Any

from ozon_parser.application.reparse import (
    ReparseResult,
    ReparseUseCase,
)


class FakeRepo:
    """In-memory ProductRepository с поддержкой iter_with_raw."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.upsert_calls: list[dict[str, Any]] = []

    def seed(
        self,
        sku: str,
        *,
        raw_html: bytes | None = b"raw",
        updated_at: str = "2026-01-01T00:00:00Z",
        url: str = "",
        copy_type: str = "source",
    ) -> None:
        self.items[sku] = {
            "sku": sku,
            "raw_html": raw_html,
            "updated_at": updated_at,
            "url": url,
            "copy_type": copy_type,
        }

    def upsert(self, **kwargs: Any) -> bool:
        self.upsert_calls.append(kwargs)
        self.items[kwargs["sku"]].update(kwargs)
        return False

    def get(self, sku: str) -> dict[str, Any] | None:
        return self.items.get(sku)

    def iter_all(
        self,
        *,
        include_raw: bool = True,
        skus: list[str] | None = None,
    ):
        for it in self.items.values():
            if skus is not None and it["sku"] not in skus:
                continue
            if include_raw:
                yield it
            else:
                yield {k: v for k, v in it.items() if k != "raw_html"}

    def iter_with_raw(self, *, skus: set[str] | None = None):
        for it in self.items.values():
            if it.get("raw_html") is None:
                continue
            if skus is not None and it["sku"] not in skus:
                continue
            yield it

    def count(self) -> int:
        return len(self.items)


class FakeParser:
    def __init__(self, result=None, exc=None) -> None:
        self._result = result or ("# MD\n", {"title": "T"})
        self._exc = exc
        self.calls: list[str] = []

    def parse(self, html, *, include_images=False):
        self.calls.append(html)
        if self._exc:
            raise self._exc
        return self._result


def _gz(s: str) -> bytes:
    return gzip.compress(s.encode("utf-8"))


# --- успешные пути -----------------------------------------------


def test_reparse_empty_repo() -> None:
    repo = FakeRepo()
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute()
    assert isinstance(res, ReparseResult)
    assert res.reparsed == 0
    assert res.failed == 0
    assert res.no_raw == 0


def test_reparse_single_item() -> None:
    repo = FakeRepo()
    repo.seed("777", raw_html=_gz("<html>x</html>"))
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute()
    assert res.reparsed == 1
    assert res.failed == 0
    assert len(repo.upsert_calls) == 1
    assert repo.upsert_calls[0]["sku"] == "777"


def test_reparse_multiple_items() -> None:
    repo = FakeRepo()
    for sku in ("111", "222", "333"):
        repo.seed(sku, raw_html=_gz(f"<html>{sku}</html>"))
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute()
    assert res.reparsed == 3
    assert len(repo.upsert_calls) == 3


# --- фильтры -----------------------------------------------------


def test_reparse_only_filter() -> None:
    repo = FakeRepo()
    for sku in ("111", "222", "333"):
        repo.seed(sku, raw_html=_gz("x"))
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute(only={"111", "333"})
    assert res.reparsed == 2
    assert {c["sku"] for c in repo.upsert_calls} == {"111", "333"}


def test_reparse_since_filter() -> None:
    repo = FakeRepo()
    repo.seed("111", raw_html=_gz("x"), updated_at="2026-01-01T00:00:00Z")
    repo.seed("222", raw_html=_gz("y"), updated_at="2026-06-01T00:00:00Z")
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute(since="2026-03-01")
    assert res.reparsed == 1
    assert repo.upsert_calls[0]["sku"] == "222"


def test_reparse_no_match() -> None:
    repo = FakeRepo()
    repo.seed("777", raw_html=_gz("x"))
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute(only={"999"})
    assert res.reparsed == 0
    assert res.failed == 0
    assert len(repo.upsert_calls) == 0


# --- отсутствие raw_html ----------------------------------------


def test_reparse_no_raw_html_reported() -> None:
    repo = FakeRepo()
    repo.seed("777", raw_html=None)
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute()
    assert res.reparsed == 0
    assert res.no_raw == 1


# --- ошибки ------------------------------------------------------


def test_reparse_gunzip_failed() -> None:
    repo = FakeRepo()
    repo.seed("777", raw_html=b"not gzip")
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute()
    assert res.failed == 1
    assert res.reparsed == 0
    assert len(res.errors) == 1
    assert res.errors[0][0] == "777"
    assert "gunzip" in res.errors[0][1]


def test_reparse_parse_failed() -> None:
    repo = FakeRepo()
    repo.seed("777", raw_html=_gz("x"))
    uc = ReparseUseCase(
        parser=FakeParser(exc=RuntimeError("boom")), repository=repo
    )
    res = uc.execute()
    assert res.failed == 1
    assert "parse" in res.errors[0][1]


def test_reparse_one_failed_others_proceed() -> None:
    repo = FakeRepo()
    repo.seed("111", raw_html=_gz("ok1"))
    repo.seed("222", raw_html=b"broken")
    repo.seed("333", raw_html=_gz("ok3"))
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    res = uc.execute()
    assert res.reparsed == 2
    assert res.failed == 1
    assert res.errors[0][0] == "222"


# --- on_progress callback ---------------------------------------


def test_reparse_progress_on_success() -> None:
    repo = FakeRepo()
    repo.seed("777", raw_html=_gz("x"))
    calls: list[tuple[str, str]] = []
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    uc.execute(on_progress=lambda sku, msg: calls.append((sku, msg)))
    assert len(calls) == 1
    assert calls[0][0] == "777"
    assert "reparsed" in calls[0][1]


def test_reparse_progress_on_missing_raw() -> None:
    repo = FakeRepo()
    repo.seed("777", raw_html=None)
    calls: list[tuple[str, str]] = []
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    uc.execute(on_progress=lambda sku, msg: calls.append((sku, msg)))
    assert any("no raw_html" in msg for _, msg in calls)


def test_reparse_progress_on_error() -> None:
    repo = FakeRepo()
    repo.seed("777", raw_html=b"not gzip")
    calls: list[tuple[str, str]] = []
    uc = ReparseUseCase(parser=FakeParser(), repository=repo)
    uc.execute(on_progress=lambda sku, msg: calls.append((sku, msg)))
    assert any("gunzip failed" in msg for _, msg in calls)
