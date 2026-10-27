"""Тесты SQLite-хранилища.

Все тесты используют `tmp_path` — реальная БД в %LOCALAPPDATA% не трогается.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from ozon_parser.store import Store, default_db_path


def _make(tmp_path: Path) -> Store:
    return Store(tmp_path / "collection.db")


def _info(title: str = "T", price: str = "100", **extra) -> dict:
    base = {
        "title": title,
        "brand": "B",
        "price": price,
        "rating": "4.9",
        "seller_name": "S",
        "characteristics": [["k", "v"]],
    }
    base.update(extra)
    return base


def test_upsert_insert(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        is_new = s.upsert(
            sku="123",
            url="https://ozon.ru/p/123",
            info=_info(),
            markdown="# T\n",
            quality=9,
            copy_type="source",
        )
        assert is_new is True
        assert s.count() == 1


def test_upsert_update_preserves_created_at(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        s.upsert(
            sku="123", url="u1", info=_info("old"), markdown="m1",
            quality=5, copy_type="rendered",
        )
        first = s.get("123")
        assert first is not None

        is_new = s.upsert(
            sku="123", url="u2", info=_info("new"), markdown="m2",
            quality=9, copy_type="source",
        )
        assert is_new is False
        assert s.count() == 1

        second = s.get("123")
        assert second is not None
        assert second["title"] == "new"
        assert second["markdown"] == "m2"
        assert second["copy_type"] == "source"
        assert second["quality"] == 9
        assert second["created_at"] == first["created_at"]
        assert second["updated_at"] >= first["updated_at"]


def test_get_missing(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        assert s.get("999") is None


def test_list_returns_summaries(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        s.upsert(sku="a", url="", info=_info("A"), markdown="ma", quality=9, copy_type="source")
        s.upsert(sku="b", url="", info=_info("B"), markdown="mb", quality=9, copy_type="source")
        rows = s.list()
        assert len(rows) == 2
        assert {r["sku"] for r in rows} == {"a", "b"}
        for r in rows:
            assert "markdown" not in r
            assert "json" not in r


def test_delete(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        s.upsert(sku="a", url="", info=_info(), markdown="", quality=9, copy_type="source")
        assert s.delete("a") is True
        assert s.delete("a") is False
        assert s.count() == 0


def test_clear(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        for i in range(3):
            s.upsert(sku=f"sku{i}", url="", info=_info(), markdown="", quality=9, copy_type="source")
        assert s.clear() == 3
        assert s.count() == 0


def test_json_roundtrip(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        info = {
            "title": "T",
            "characteristics": [["a", "b"], ["c", "d"]],
            "nested": {"x": 1, "y": [1, 2, 3]},
        }
        s.upsert(sku="1", url="", info=info, markdown="", quality=5, copy_type="source")
        row = s.get("1")
        assert row is not None
        assert json.loads(row["json"]) == info


def test_empty_sku_rejected(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        with pytest.raises(ValueError):
            s.upsert(sku="", url="", info={}, markdown="", quality=0, copy_type="unknown")


def test_raw_html_and_hash(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        raw = b"<html>payload</html>"
        html_text = "<html>payload</html>"
        s.upsert(
            sku="1", url="", info=_info(), markdown="",
            quality=5, copy_type="source", raw_html=raw,
            html_text=html_text,
        )
        row = s.get("1")
        assert row is not None
        assert row["html_hash"] is not None
        assert row["html_hash"] == hashlib.sha1(html_text.encode()).hexdigest()
        assert row["raw_html"] == raw


def test_upsert_html_hash_stable_for_same_html(tmp_path: Path) -> None:
    """html_hash считается по исходному HTML, а не по gzip-байтам.

    gzip.compress пишет mtime в заголовок: одинаковый контент в разные
    секунды даёт разные байты и разный sha1.
    """
    import gzip
    import hashlib as _hashlib
    with _make(tmp_path) as s:
        s.upsert(
            sku="1", url="", info=_info(), markdown="",
            quality=1, copy_type="source",
            raw_html=gzip.compress(b"same", mtime=0), html_text="same",
        )
        h1 = s.get("1")["html_hash"]
        s.upsert(
            sku="1", url="", info=_info(), markdown="",
            quality=1, copy_type="source",
            raw_html=gzip.compress(b"same", mtime=12345), html_text="same",
        )
        h2 = s.get("1")["html_hash"]
        assert h1 == h2 == _hashlib.sha1(b"same").hexdigest()


def test_upsert_preserves_raw_html_when_none(tmp_path: Path) -> None:
    """store_raw_html=false не должен стирать ранее сохранённый raw_html."""
    import gzip
    with _make(tmp_path) as s:
        s.upsert(
            sku="1", url="", info=_info(), markdown="",
            quality=1, copy_type="source",
            raw_html=gzip.compress(b"body"), html_text="body",
        )
        assert s.get("1")["raw_html"] is not None
        s.upsert(
            sku="1", url="", info=_info(), markdown="",
            quality=1, copy_type="source",
            raw_html=None, html_text=None,
        )
        row = s.get("1")
        assert row["raw_html"] is not None
        assert row["html_hash"] is not None


def test_iter_all_yields_full_records(tmp_path: Path) -> None:
    with _make(tmp_path) as s:
        s.upsert(sku="a", url="", info=_info("A"), markdown="ma", quality=9, copy_type="source")
        s.upsert(sku="b", url="", info=_info("B"), markdown="mb", quality=9, copy_type="source")
        items = list(s.iter_all())
        assert len(items) == 2
        assert all("markdown" in i for i in items)


def test_default_db_path_under_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only path check")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = default_db_path()
    assert p.parent.name == "ozon-parser"
    assert p.name == "collection.db"


def test_store_is_thread_safe(tmp_path: Path) -> None:
    """Регрессия: ThreadingHTTPServer использует новый поток на каждый запрос.

    Без `check_same_thread=False` + `threading.RLock` в Store это падает
    с `sqlite3.ProgrammingError: SQLite objects created in a thread can
    only be used in that same thread`.
    """
    import threading

    with Store(tmp_path / "c.db") as s:
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                for i in range(10):
                    s.upsert(
                        sku=f"sku-{n}-{i}",
                        url="",
                        info={"title": f"t{n}"},
                        markdown="",
                        quality=5,
                        copy_type="source",
                    )
            except BaseException as e:  # pragma: no cover — не должно случаться
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"thread errors: {errors[0]!r}"
        assert s.count() == 40
        # iter_all тоже должен работать после конкурентных записей
        assert len(list(s.iter_all())) == 40
