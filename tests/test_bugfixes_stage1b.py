"""Регрессионные тесты на багфиксы Этапа 1, батч 2 (B2, B3, B4, S1).

S2 (constant-time token compare) поведенчески покрыт в test_auth.py —
отдельного теста нет, timing-attack нестабилен в CI.
"""
from __future__ import annotations

import http.client
import logging
import socket
import threading
import time
from pathlib import Path

import pytest

from ozon_parser.cli import main
from ozon_parser.server import make_server
from ozon_parser.store import Store


# Payload должен проходить is_usable_ozon_copy: len >= MIN_HTML_LEN (500)
# и содержать подстроку "ozon.ru" (см. ozon_parser/detection.py).
SYNTH_SOURCE = """<!DOCTYPE html>
<html lang="ru"><head>
  <title>T - Ozon</title>
  <meta property="og:title" content="T">
  <script type="application/ld+json">{"@context":"http://schema.org","@type":"Product","name":"T","brand":"B","sku":"777","description":"D","image":"https://ir.ozone.ru/x.jpg","aggregateRating":{"ratingValue":"4.7","reviewCount":"10"},"offers":{"price":"500","priceCurrency":"RUB","url":"https://www.ozon.ru/product/777/"}}</script>
</head><body>
  <div id="__ozon" data-server-rendered="true">
    <h1>T</h1>
    <div id="section-characteristics"><dl><dt>K</dt><dd>V</dd></dl></div>
  </div>
</body></html>"""


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(tmp_path / "_no.toml"))


def test_powershell_with_braces_in_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """str.format() раньше валил KeyError: '5 шт' на title с {5 шт}."""
    from ozon_parser import notifier as N

    captured: dict = {}

    def fake_popen(args, **kw):
        captured["args"] = args
        return None

    monkeypatch.setattr(N.subprocess, "Popen", fake_popen)
    N._powershell("Набор {5 шт}", "тело")
    script = captured["args"][-1]
    assert "{5 шт}" in script
    assert "тело" in script


def test_cmd_once_error_status_reports_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parse_product упал → status=error → exit 2 + «parse error», не «не Ozon PDP».

    После Stage 5 daemon не импортирует parse_product напрямую — он зовёт
    IngestHtmlUseCase(parser=OzonHtmlParser()). Патчим на уровне адаптера:
    ozon_parser.infrastructure.parsing.ozon.parse_product.
    """
    from ozon_parser import daemon as d
    from ozon_parser.infrastructure.parsing import ozon as ozon_mod

    def boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(d, "read_text_clipboard", lambda: SYNTH_SOURCE)
    monkeypatch.setattr(d, "read_html_clipboard", lambda: None)
    monkeypatch.setattr(ozon_mod, "parse_product", boom)

    db = tmp_path / "c.db"
    rc = main(["--db", str(db), "once"])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "parse error" in err
    assert "boom" in err
    assert "не ozon" not in err


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    store = Store(tmp_path / "c.db")
    port = _free_port()
    httpd = make_server(store, "127.0.0.1", port)
    th = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    th.start()
    for _ in range(40):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                break
        except OSError:
            time.sleep(0.02)
    yield port, store
    httpd.shutdown()
    httpd.server_close()
    th.join(timeout=2.0)
    store.close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    conn.close()
    return r.status, data, hdrs


def test_preflight_allows_content_type(server) -> None:
    """Access-Control-Allow-Headers должен включать Content-Type.

    X-Ozon-Token убран вместе с auth (0.3.0), CORS-сужение до ozon.ru
    остаётся. Content-Type нужен для POST /ingest и /flush с JSON-body.
    """
    port, _ = server
    st, _, hdrs = _req(port, "OPTIONS", "/ingest")
    assert st == 204
    allow = hdrs.get("access-control-allow-headers", "")
    assert "content-type" in allow.lower()
