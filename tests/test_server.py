"""Тесты HTTP-сервера. Сервер поднимается в потоке на свободном порту."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from ozon_parser.server import make_server
from ozon_parser.store import Store


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
  </div>
</body></html>"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    store = Store(tmp_path / "c.db")
    port = _free_port()
    httpd = make_server(store, "127.0.0.1", port)
    th = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    # ждём, пока сервер реально начнёт отвечать
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


def _req(port: int, method: str, path: str, body: bytes | None = None,
         headers: dict | None = None) -> tuple[int, bytes, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    hdrs = {k.lower(): v for k, v in resp.getheaders()}
    conn.close()
    return resp.status, data, hdrs


def test_health(server) -> None:
    port, _ = server
    st, data, _ = _req(port, "GET", "/health")
    assert st == 200
    assert json.loads(data) == {"ok": True}


def test_options_cors(server) -> None:
    port, _ = server
    st, _, hdrs = _req(port, "OPTIONS", "/ingest")
    assert st == 204
    # Allow-Origin сужен до ozon.ru: bookmarklet работает, злой сайт
    # получает CORS-блок на fetch к localhost.
    assert hdrs.get("access-control-allow-origin") == "https://www.ozon.ru"


def test_ingest_rejects_evil_origin(server) -> None:
    """CSRF-защита: чужой Origin → 403, ничего не записано."""
    port, store = server
    body = SYNTHETIC_SOURCE.encode("utf-8")
    st, data, _ = _req(
        port, "POST", "/ingest", body=body,
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert st == 403
    assert b"forbidden origin" in data
    assert store.count() == 0


def test_ingest_rejects_null_origin(server) -> None:
    """Sandboxed iframe присылает Origin: null — отклоняем."""
    port, store = server
    body = SYNTHETIC_SOURCE.encode("utf-8")
    st, data, _ = _req(
        port, "POST", "/ingest", body=body,
        headers={"Content-Type": "text/plain", "Origin": "null"},
    )
    assert st == 403
    assert store.count() == 0


def test_ingest_allows_missing_origin(server) -> None:
    """curl / скрипты: нет Origin — разрешено (CSRF невозможен)."""
    port, store = server
    body = SYNTHETIC_SOURCE.encode("utf-8")
    st, data, _ = _req(port, "POST", "/ingest", body=body,
                       headers={"Content-Type": "text/plain"})
    assert st == 200
    assert json.loads(data)["status"] == "added"


def test_ingest_allows_ozon_origin(server) -> None:
    """Bookmarklet: Origin = ozon.ru — разрешено."""
    port, store = server
    body = SYNTHETIC_SOURCE.encode("utf-8")
    st, data, _ = _req(
        port, "POST", "/ingest", body=body,
        headers={"Content-Type": "text/plain", "Origin": "https://www.ozon.ru"},
    )
    assert st == 200
    assert json.loads(data)["status"] == "added"


def test_ingest_allows_extension_origin(server) -> None:
    """Extension fallback: chrome-extension://<ID> — разрешено."""
    port, store = server
    body = SYNTHETIC_SOURCE.encode("utf-8")
    st, data, _ = _req(
        port, "POST", "/ingest", body=body,
        headers={
            "Content-Type": "text/plain",
            "Origin": "chrome-extension://bmdjfjhckhfifhdnohiojamggfnlbcgf",
        },
    )
    assert st == 200


def test_flush_rejects_evil_origin(server) -> None:
    port, _ = server
    body = json.dumps({"format": "markdown"}).encode("utf-8")
    st, data, _ = _req(
        port, "POST", "/flush", body=body,
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    assert st == 403
    assert b"forbidden origin" in data


def test_ingest_source(server) -> None:
    port, store = server
    body = SYNTHETIC_SOURCE.encode("utf-8")
    st, data, _ = _req(port, "POST", "/ingest?url=https%3A%2F%2Fozon.ru%2Fx",
                       body=body, headers={"Content-Type": "text/plain"})
    assert st == 200
    j = json.loads(data)
    assert j["status"] == "added"
    assert j["sku"] == "777"
    assert j["copy_type"] == "source"
    assert j["quality"] >= 5
    assert store.count() == 1


def test_ingest_garbage(server) -> None:
    port, store = server
    st, data, _ = _req(port, "POST", "/ingest", body=b"hello",
                       headers={"Content-Type": "text/plain"})
    assert st == 400
    j = json.loads(data)
    assert j["status"] == "skipped"
    assert store.count() == 0


def test_ingest_update(server) -> None:
    port, store = server
    body = SYNTHETIC_SOURCE.encode("utf-8")
    _req(port, "POST", "/ingest", body=body)
    modified = SYNTHETIC_SOURCE.replace('"price":"500"', '"price":"600"')
    st, data, _ = _req(port, "POST", "/ingest", body=modified.encode("utf-8"))
    assert st == 200
    j = json.loads(data)
    assert j["status"] == "updated"
    assert store.count() == 1
    assert store.get("777")["price"] == "600"


def test_items_list_and_get(server) -> None:
    port, _ = server
    _req(port, "POST", "/ingest", body=SYNTHETIC_SOURCE.encode("utf-8"))
    st, data, _ = _req(port, "GET", "/items")
    assert st == 200
    j = json.loads(data)
    assert len(j["items"]) == 1
    assert j["items"][0]["sku"] == "777"

    st, data, _ = _req(port, "GET", "/items/777")
    assert st == 200
    md = data.decode("utf-8")
    assert md.startswith("# ")


def test_items_missing(server) -> None:
    port, _ = server
    st, data, _ = _req(port, "GET", "/items/does-not-exist")
    assert st == 404
    assert json.loads(data)["error"] == "not found"


def test_delete(server) -> None:
    port, store = server
    _req(port, "POST", "/ingest", body=SYNTHETIC_SOURCE.encode("utf-8"))
    st, data, _ = _req(port, "DELETE", "/items/777")
    assert st == 200
    assert json.loads(data)["deleted"] is True
    assert store.count() == 0


def test_delete_all(server) -> None:
    """DELETE /items (без sku) очищает коллекцию одним вызовом."""
    port, store = server
    _req(port, "POST", "/ingest", body=SYNTHETIC_SOURCE.encode("utf-8"))
    st, data, _ = _req(port, "DELETE", "/items")
    assert st == 200
    assert json.loads(data)["cleared"] == 1
    assert store.count() == 0


def test_flush_response(server) -> None:
    port, _ = server
    _req(port, "POST", "/ingest", body=SYNTHETIC_SOURCE.encode("utf-8"))
    body = json.dumps({"format": "markdown", "to": "response"}).encode("utf-8")
    st, data, _ = _req(port, "POST", "/flush", body=body,
                       headers={"Content-Type": "application/json"})
    assert st == 200
    assert data.decode("utf-8").startswith("# Ozon collection")


def test_flush_empty(server) -> None:
    port, _ = server
    body = json.dumps({"format": "markdown"}).encode("utf-8")
    st, data, _ = _req(port, "POST", "/flush", body=body,
                       headers={"Content-Type": "application/json"})
    assert st == 200
    j = json.loads(data)
    assert j["status"] == "empty"


def test_flush_file_rejected(server, tmp_path: Path) -> None:
    """HTTP /flush не пишет на диск сервера: to=file → 400.

    Запись через HTTP /flush {to:"file", path:"..."} была вектором
    arbitrary file write (например, .bat в Startup folder). Extension
    получает текст в ответе и сохраняет через chrome.downloads — без
    доступа к ФС сервера. CLI (ozon-parser flush -t FILE) делает запись
    как раньше — это отдельный код-путь (_write_output в cli.py).
    """
    port, _ = server
    _req(port, "POST", "/ingest", body=SYNTHETIC_SOURCE.encode("utf-8"))
    out = tmp_path / "out.md"
    body = json.dumps({"format": "markdown", "to": "file", "path": str(out)}).encode("utf-8")
    st, data, _ = _req(port, "POST", "/flush", body=body,
                       headers={"Content-Type": "application/json"})
    assert st == 400
    assert not out.exists()
    assert b"unsupported target" in data


def test_flush_bad_json(server) -> None:
    port, _ = server
    st, data, _ = _req(port, "POST", "/flush", body=b"not-json",
                       headers={"Content-Type": "application/json"})
    assert st == 400


def test_config_endpoint_no_config(server) -> None:
    """Без config — пустой dict, не 404.

    has_auth убран вместе с auth (0.3.0). /config отдаёт то, что
    передано в make_server(config=...), без служебных полей.
    """
    port, _ = server
    st, data, _ = _req(port, "GET", "/config")
    assert st == 200
    assert json.loads(data) == {}


def test_config_endpoint_with_config(tmp_path: Path) -> None:
    """С config — отдаётся как есть."""
    from ozon_parser.server import make_server

    store = Store(tmp_path / "c.db")
    port = _free_port()
    cfg = {
        "http_port": 9999,
        "poll_interval_ms": 500,
        "store_raw_html": False,
        "default_format": "jsonl",
    }
    httpd = make_server(store, "127.0.0.1", port, config=cfg)
    th = threading.Thread(
        target=httpd.serve_forever,
        kwargs={"poll_interval": 0.05},
        daemon=True,
    )
    th.start()
    for _ in range(40):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                break
        except OSError:
            time.sleep(0.02)
    try:
        st, data, _ = _req(port, "GET", "/config")
        assert st == 200
        j = json.loads(data)
        assert j["http_port"] == 9999
        assert j["default_format"] == "jsonl"
        assert j["store_raw_html"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=2.0)
        store.close()
