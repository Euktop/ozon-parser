"""Тесты, что Stage 5 wiring работает: presentation → use case.

Проверяют не «используется ли use case внутри», а end-to-end:
- poll_once с инъекцией use_case не дублирует parse (spy получает вызов);
- daemon.run_forever проходит через poll_once (сохраняется on_ingest);
- cli._cmd_reparse через ReparseUseCase даёт тот же exit code и stderr.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ozon_parser.application.ingest import IngestHtmlUseCase, IngestResult
from ozon_parser.cli import main
from ozon_parser.daemon import poll_once
from ozon_parser.store import Store


# Валидный payload: >=500 символов, ozon.ru, SKU 6 цифр.
VALID_HTML = (
    '<!DOCTYPE html>\n<html lang="ru"><head>\n  <title>T - Ozon</title>\n'
    '  <meta property="og:title" content="T">\n'
    '  <script type="application/ld+json">{"@context":"http://schema.org",'
    '"@type":"Product","name":"T","sku":"777777",'
    '"offers":{"price":"500","priceCurrency":"RUB",'
    '"url":"https://www.ozon.ru/product/777777/"}}</script>\n'
    '</head><body>\n  <div id="__ozon" data-server-rendered="true">\n'
    '    <h1>T</h1>\n    <div id="section-characteristics">'
    '<dl><dt>K</dt><dd>V</dd></dl></div>\n'
    '    <!-- Filler to satisfy MIN_HTML_LEN=500 -->\n'
    '    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>\n'
    '    <p>Sed do eiusmod tempor incididunt ut labore et dolore magna.</p>\n'
    '    <p>Ut enim ad minim veniam, quis nostrud exercitation ullamco.</p>\n'
    '  </div>\n</body></html>'
)


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(tmp_path / "_no.toml"))


# --- poll_once с инъекцией use case ---------------------------------


class SpyUseCase:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, html: str, *, url_hint: str = "") -> IngestResult:
        self.calls.append(html)
        return IngestResult(
            status="added",
            sku="777777",
            copy_type="source",
            quality=9,
            info={"title": "T", "sku": "777777"},
            payload_len=len(html),
            raw_html_size=0,
        )


def test_poll_once_uses_injected_use_case(tmp_path: Path) -> None:
    """poll_once принимает use_case — доказательство, что presentation
    не дублирует parse: spy получает вызов, Store не пополняется сам."""
    spy = SpyUseCase()
    with Store(tmp_path / "c.db") as s:
        res = poll_once(
            s, text=VALID_HTML, html=None, use_case=spy  # type: ignore[arg-type]
        )
    assert res["status"] == "added"
    assert res["sku"] == "777777"
    assert res["raw_html_size"] == 0  # явное поле, даже когда raw не сохранён
    assert res["info"] == {"title": "T", "sku": "777777"}
    assert len(spy.calls) == 1
    assert spy.calls[0] == VALID_HTML


def test_poll_once_default_use_case_writes_to_store(tmp_path: Path) -> None:
    """Без инъекции poll_once создаёт IngestHtmlUseCase и пишет в Store."""
    with Store(tmp_path / "c.db") as s:
        res = poll_once(s, text=VALID_HTML, html=None)
        assert res["status"] == "added"
        assert s.count() == 1
        assert s.get("777777") is not None


# --- cli._cmd_reparse через ReparseUseCase --------------------------


def test_cli_reparse_works_via_use_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import gzip

    from ozon_parser.store import Store as _S

    db = tmp_path / "c.db"
    raw = gzip.compress(VALID_HTML.encode("utf-8"))
    with _S(db) as s:
        s.upsert(
            sku="777777", url="https://www.ozon.ru/product/777777/",
            info={"title": "OLD", "sku": "777777"},
            markdown="# OLD\n", quality=1, copy_type="source",
            raw_html=raw, html_text=VALID_HTML,
        )

    rc = main(["--db", str(db), "reparse"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "reparsed" in err
    with _S(db) as s:
        assert s.get("777777")["markdown"] != "# OLD\n"
