"""Регрессионные тесты на багфиксы Этапа 1: B5, B6, B7, B8, B9."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ozon_parser.cli import main
from ozon_parser.config import load_config
from ozon_parser.application.ingest import _sku_from_url
from ozon_parser.service import _probe_serve_health
from ozon_parser.store import Store


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(tmp_path / "_no.toml"))


# --- B5: warnings при неверных типах -------------------------------


def test_load_wrong_type_logs_warning(tmp_path: Path, caplog) -> None:
    f = tmp_path / "c.toml"
    f.write_text('[input]\nhttp_port = "not-int"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="ozon_parser.config"):
        c = load_config(f)
    assert c.http_port == 8765
    assert any("not-int" in r.message for r in caplog.records)


def test_load_bool_for_int_logs_warning(tmp_path: Path, caplog) -> None:
    f = tmp_path / "c.toml"
    f.write_text("[parse]\nmin_quality_score = true\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="ozon_parser.config"):
        c = load_config(f)
    assert c.min_quality_score == 4
    assert any("bool" in r.message for r in caplog.records)


def test_load_missing_field_no_warning(tmp_path: Path, caplog) -> None:
    """Отсутствующее поле — не ошибка типа, не логируем."""
    f = tmp_path / "c.toml"
    f.write_text("[input]\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="ozon_parser.config"):
        load_config(f)
    assert not any("is not" in r.message for r in caplog.records)


# --- B6: iter_with_raw ---------------------------------------------


def test_iter_with_raw_only_rows_with_raw(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        s.upsert(
            sku="a", url="", info={"title": "A"}, markdown="", quality=1,
            copy_type="source", raw_html=b"raw-a",
        )
        s.upsert(
            sku="b", url="", info={"title": "B"}, markdown="", quality=1,
            copy_type="source", raw_html=None,
        )
        s.upsert(
            sku="c", url="", info={"title": "C"}, markdown="", quality=1,
            copy_type="source", raw_html=b"raw-c",
        )
        rows = list(s.iter_with_raw())
        assert {r["sku"] for r in rows} == {"a", "c"}


def test_iter_with_raw_with_skus_filter(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        for sku in ("a", "b", "c"):
            s.upsert(
                sku=sku, url="", info={"title": sku.upper()}, markdown="",
                quality=1, copy_type="source", raw_html=b"raw",
            )
        rows = list(s.iter_with_raw(skus={"a"}))
        assert [r["sku"] for r in rows] == ["a"]


def test_iter_with_raw_empty_skus_returns_nothing(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        s.upsert(
            sku="a", url="", info={"title": "A"}, markdown="", quality=1,
            copy_type="source", raw_html=b"raw",
        )
        assert list(s.iter_with_raw(skus=set())) == []


# --- B7: валидация --host ------------------------------------------


def test_bookmarklet_rejects_invalid_host() -> None:
    with pytest.raises(SystemExit):
        main(["bookmarklet", "--host", "not a host!", "--to", "stdout"])


# --- B8: health-check проверяет тело --------------------------------


class _FakeResp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> None:
        return None


def test_probe_health_ok_body_true(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **kw: _FakeResp(200, b'{"ok": true}'),
    )
    assert _probe_serve_health(8765) is True


def test_probe_health_wrong_body_false(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **kw: _FakeResp(200, b'{"ok": false}'),
    )
    assert _probe_serve_health(8765) is False


def test_probe_health_non_json_false(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **kw: _FakeResp(200, b"not json"),
    )
    assert _probe_serve_health(8765) is False


# --- B9: SKU из URL без trailing slash -----------------------------


def test_sku_url_trailing_slash() -> None:
    assert _sku_from_url("https://www.ozon.ru/product/foo-123/") == "123"


def test_sku_url_no_trailing_slash() -> None:
    assert _sku_from_url("https://www.ozon.ru/product/foo-123") == "123"


def test_sku_url_with_query() -> None:
    assert _sku_from_url("https://www.ozon.ru/product/foo-123?ref=x") == "123"


def test_sku_url_with_fragment() -> None:
    assert _sku_from_url("https://www.ozon.ru/product/foo-123#reviews") == "123"
