"""Тесты daemon.py — без реального клипборда.

Все тесты передают text/html явно в `poll_once(...)` — это ключевая
возможность для тестов. CLI-команда `once` тестируется с monkeypatch
`read_text_clipboard` / `read_html_clipboard`.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ozon_parser.cli import main
from ozon_parser.daemon import collect_candidates, poll_once, run_forever
from ozon_parser.store import Store


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый тест использует пустой конфиг через env var OZON_PARSER_CONFIG."""
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(tmp_path / "_no_config_.toml"))


SYNTHETIC_SOURCE = """<!DOCTYPE html>
<html lang="ru"><head>
  <title>T - Ozon</title>
  <meta property="og:title" content="T">
  <script type="application/ld+json">{"@context":"http://schema.org","@type":"Product","name":"T","brand":"B","sku":"777","description":"D","image":"https://ir.ozone.ru/x.jpg","aggregateRating":{"ratingValue":"4.7","reviewCount":"10"},"offers":{"price":"500","priceCurrency":"RUB","url":"https://www.ozon.ru/product/777/"}}</script>
</head><body>
  <div id="__ozon" data-server-rendered="true">
    <h1>T</h1>
    <div id="section-characteristics"><dl><dt>K</dt><dd>V</dd></dl></div>
  </div>
  <div class="client-state">
    <div id="state-webPrice-1-default-1" data-state='{"cardPrice":"400","price":"500"}'></div>
    <div id="state-webCurrentSeller-1-default-1" data-state='{"sellerCell":{"centerBlock":{"title":{"text":"S"}}}}'></div>
  </div>
</body></html>"""

SYNTHETIC_RENDERED = SYNTHETIC_SOURCE.replace(' data-server-rendered="true"', "")


# --- collect_candidates ---------------------------------------------------


def test_candidates_empty() -> None:
    assert collect_candidates("", None) == []


def test_candidates_garbage() -> None:
    assert collect_candidates("hello", None) == []


def test_candidates_rich_and_plain_both_source() -> None:
    c = collect_candidates(SYNTHETIC_SOURCE, SYNTHETIC_SOURCE)
    assert len(c) == 2
    assert c[0][0] == "rich-html"
    assert c[0][2] == "source"
    assert c[1][0] == "plain-html"
    assert c[1][2] == "source"


def test_candidates_source_beats_rendered() -> None:
    c = collect_candidates(SYNTHETIC_RENDERED, SYNTHETIC_SOURCE)
    assert c[0][2] == "source"


# --- poll_once ------------------------------------------------------------


def test_poll_once_empty(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        res = poll_once(s, text="", html=None)
        assert res["status"] == "skipped"
        assert s.count() == 0


def test_poll_once_garbage(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        res = poll_once(s, text="hello world", html=None)
        assert res["status"] == "skipped"


def test_poll_once_added(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        res = poll_once(s, text=SYNTHETIC_SOURCE, html=None)
        assert res["status"] == "added"
        assert res["sku"] == "777"
        assert res["copy_type"] == "source"
        assert s.count() == 1
        assert s.get("777") is not None


def test_poll_once_deduped(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        r1 = poll_once(s, text=SYNTHETIC_SOURCE, html=None)
        r2 = poll_once(
            s, text=SYNTHETIC_SOURCE, html=None, last_hash=r1["hash"]
        )
        assert r1["status"] == "added"
        assert r2["status"] == "deduped"
        assert s.count() == 1


def test_poll_once_updated_price(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        r1 = poll_once(s, text=SYNTHETIC_SOURCE, html=None)
        modified = SYNTHETIC_SOURCE.replace('"price":"500"', '"price":"600"')
        r2 = poll_once(s, text=modified, html=None, last_hash=r1["hash"])
        assert r2["status"] == "updated"
        assert r2["sku"] == "777"
        assert s.count() == 1
        assert s.get("777")["price"] == "600"


def test_poll_once_rich_html_preferred(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        res = poll_once(s, text="", html=SYNTHETIC_SOURCE)
        assert res["status"] == "added"
        assert res["copy_type"] == "source"


def test_poll_once_stores_raw_html_gzip(tmp_path: Path) -> None:
    import gzip

    with Store(tmp_path / "c.db") as s:
        res = poll_once(s, text=SYNTHETIC_SOURCE, html=None, store_raw_html=True)
        assert res["status"] == "added"
        assert res["raw_html_size"] > 0

        row = s.get("777")
        assert row is not None
        assert row["raw_html"] is not None
        assert gzip.decompress(row["raw_html"]).decode("utf-8") == SYNTHETIC_SOURCE
        assert row["html_hash"] != ""


def test_poll_once_no_raw_html_when_disabled(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        res = poll_once(s, text=SYNTHETIC_SOURCE, html=None, store_raw_html=False)
        assert res["status"] == "added"
        assert res["raw_html_size"] == 0

        row = s.get("777")
        assert row is not None
        assert row["raw_html"] is None
        # html_hash считается по исходному HTML независимо от raw_html —
        # это «отпечаток распарсенного», полезен для будущего change-detection.
        assert row["html_hash"] != ""


# --- run_forever ----------------------------------------------------------


def test_run_forever_stops_on_event(tmp_path: Path) -> None:
    with Store(tmp_path / "c.db") as s:
        stop = threading.Event()

        def stopper() -> None:
            time.sleep(0.2)
            stop.set()

        th = threading.Thread(target=stopper, daemon=True)
        th.start()
        t0 = time.monotonic()
        run_forever(s, interval=0.05, stop_event=stop)
        elapsed = time.monotonic() - t0
        th.join(timeout=1.0)
        assert stop.is_set()
        assert elapsed < 2.0


# --- CLI: once (monkeypatch клипборд) -------------------------------------


def test_cli_once_added(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ozon_parser import daemon as d

    monkeypatch.setattr(d, "read_text_clipboard", lambda: SYNTHETIC_SOURCE)
    monkeypatch.setattr(d, "read_html_clipboard", lambda: None)
    db = tmp_path / "c.db"
    assert main(["--db", str(db), "once"]) == 0
    err = capsys.readouterr().err
    assert "added" in err
    assert "777" in err
    with Store(db) as s:
        assert s.count() == 1


def test_cli_once_empty(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ozon_parser import daemon as d

    monkeypatch.setattr(d, "read_text_clipboard", lambda: "")
    monkeypatch.setattr(d, "read_html_clipboard", lambda: None)
    db = tmp_path / "c.db"
    assert main(["--db", str(db), "once"]) == 1
    err = capsys.readouterr().err.lower()
    assert "not ozon" in err or "skipped" in err
