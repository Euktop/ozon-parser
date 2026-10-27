"""Тесты команд reparse и export-fixture, а также хранения raw_html."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from ozon_parser.cli import main
from ozon_parser.store import Store


# HTML, из которого парсер извлечёт SKU=777 и quality_score=9.
# Включает всё, за что quality_score даёт баллы:
#   title (JSON-LD) +1, price (JSON-LD) +2, seller (webCurrentSeller) +2,
#   rating (JSON-LD) +1, characteristics (section-characteristics) +1,
#   description (JSON-LD) +1, images (JSON-LD) +1 = 9.
HTML_WITH_SKU = """<!DOCTYPE html>
<html><head>
<script type="application/ld+json">{
  "@context":"http://schema.org",
  "@type":"Product",
  "name":"T",
  "sku":"777",
  "brand":{"name":"B"},
  "description":"A detailed description of the product",
  "image":"https://ir.ozone.ru/img.jpg",
  "aggregateRating":{"ratingValue":"4.5","reviewCount":"100"},
  "offers":{"price":"500","priceCurrency":"RUB","url":"https://ozon.ru/p/777"}
}</script>
</head><body>
<div id="__ozon" data-server-rendered="true">
  <h1>T</h1>
  <div id="section-characteristics"><dl><dt>K</dt><dd>V</dd></dl></div>
</div>
<div class="client-state">
  <div id="state-webCurrentSeller-1-default-1" data-state='{"sellerCell":{"centerBlock":{"title":{"text":"S"}}}}'></div>
</div>
</body></html>"""


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(tmp_path / "_no.toml"))


def _seed(db: Path, *, sku: str = "777", copy_type: str = "source", raw: bytes | None = None) -> None:
    if raw is None:
        raw = gzip.compress(HTML_WITH_SKU.encode("utf-8"))
    with Store(db) as s:
        s.upsert(
            sku=sku,
            url="https://ozon.ru/p/777",
            info={"title": "T", "sku": sku, "price": "500"},
            markdown="# OLD\n",
            quality=3,
            copy_type=copy_type,
            raw_html=raw,
        )


# --- reparse -------------------------------------------------------------


def test_reparse_updates_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db)
    # Перед reparse: markdown старый, quality 3
    with Store(db) as s:
        before = s.get("777")
        assert before is not None
        assert before["markdown"] == "# OLD\n"
        assert before["quality"] == 3

    assert main(["--db", str(db), "reparse"]) == 0
    err = capsys.readouterr().err
    assert "reparsed" in err

    with Store(db) as s:
        after = s.get("777")
        assert after is not None
        assert after["markdown"] != "# OLD\n"  # пересобран парсером
        assert after["quality"] == 9  # полный набор полей → 9/9
        assert after["raw_html"] is not None  # сохранён


def test_reparse_no_raw_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    # Не используем _seed: он кладёт gzip-raw, а COALESCE в upsert
    # сохраняет старый raw_html при raw_html=None — тест не увидел бы
    # «no raw_html stored». Кладём запись сразу без raw_html.
    with Store(db) as s:
        s.upsert(
            sku="777", url="", info={"title": "T"}, markdown="# OLD\n",
            quality=3, copy_type="source", raw_html=None, html_text=None,
        )

    rc = main(["--db", str(db), "reparse"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "no raw_html stored" in err
    assert "re-copy required" in err



def test_reparse_only_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, sku="111")
    _seed(db, sku="222")
    _seed(db, sku="333")

    assert main(["--db", str(db), "reparse", "--only", "111,333"]) == 0
    with Store(db) as s:
        assert s.get("222")["markdown"] == "# OLD\n"
        assert s.get("111")["markdown"] != "# OLD\n"
        assert s.get("333")["markdown"] != "# OLD\n"


# --- export-fixture ------------------------------------------------------


def test_export_fixture_writes_source_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, copy_type="source")
    out_dir = tmp_path / "fixtures"
    assert (
        main(["--db", str(db), "export-fixture", "777", "--to", str(out_dir)])
        == 0
    )
    target = out_dir / "source.html"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == HTML_WITH_SKU
    err = capsys.readouterr().err
    assert "exported" in err
    assert "source" in err


def test_export_fixture_rendered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    rendered_html = HTML_WITH_SKU.replace(' data-server-rendered="true"', "")
    raw = gzip.compress(rendered_html.encode("utf-8"))
    _seed(db, copy_type="rendered", raw=raw)
    out_dir = tmp_path / "fixtures"
    assert (
        main(["--db", str(db), "export-fixture", "777", "--to", str(out_dir)])
        == 0
    )
    target = out_dir / "rendered.html"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == rendered_html


def test_export_fixture_force_overwrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db)
    out_dir = tmp_path / "fixtures"
    out_dir.mkdir()
    target = out_dir / "source.html"
    target.write_text("OLD", encoding="utf-8")

    # Без --force — отказ
    rc = main(["--db", str(db), "export-fixture", "777", "--to", str(out_dir)])
    assert rc == 2
    assert target.read_text(encoding="utf-8") == "OLD"

    # С --force — перезапись
    rc = main(
        [
            "--db", str(db), "export-fixture", "777",
            "--to", str(out_dir), "--force",
        ]
    )
    assert rc == 0
    assert target.read_text(encoding="utf-8") == HTML_WITH_SKU


def test_export_fixture_no_raw_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    with Store(db) as s:
        s.upsert(
            sku="777", url="", info={"title": "T"}, markdown="# T\n",
            quality=1, copy_type="source", raw_html=None,
        )
    out_dir = tmp_path / "fixtures"
    rc = main(["--db", str(db), "export-fixture", "777", "--to", str(out_dir)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no raw_html" in err


def test_export_fixture_unknown_sku(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    out_dir = tmp_path / "fixtures"
    rc = main(["--db", str(db), "export-fixture", "999", "--to", str(out_dir)])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_export_fixture_latest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, sku="111", copy_type="source")
    out_dir = tmp_path / "fixtures"
    assert (
        main(["--db", str(db), "export-fixture", "--latest", "--to", str(out_dir)])
        == 0
    )
    assert (out_dir / "source.html").exists()


def test_export_fixture_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, sku="111", copy_type="source")
    _seed(db, sku="222", copy_type="rendered")
    out_dir = tmp_path / "fixtures"
    assert (
        main(["--db", str(db), "export-fixture", "--all", "--to", str(out_dir)])
        == 0
    )
    names = {f.name for f in out_dir.glob("*.html")}
    assert "111-source.html" in names
    assert "222-rendered.html" in names


def test_export_fixture_no_sku_no_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    out_dir = tmp_path / "fixtures"
    rc = main(["--db", str(db), "export-fixture", "--to", str(out_dir)])
    assert rc == 2
    assert "specify SKU, --latest, or --all" in capsys.readouterr().err
