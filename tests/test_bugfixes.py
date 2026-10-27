"""Регрессионные тесты на багфиксы Этапа 1.

B1: iter_all(skus=[]) / flush --only "," / {"only": []} не должны
возвращать всю коллекцию вместо пустого результата.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from ozon_parser.cli import main
from ozon_parser.server import make_server
from ozon_parser.store import Store


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(tmp_path / "_no.toml"))


def _seed(db: Path, n: int = 2) -> None:
    with Store(db) as s:
        for i in range(1, n + 1):
            s.upsert(
                sku=str(i), url="", info={"title": f"Item{i}"},
                markdown=f"# Item{i}\n", quality=9, copy_type="source",
            )


# --- B1a: iter_all ----------------------------------------------------


def test_iter_all_empty_skus_returns_nothing(tmp_path: Path) -> None:
    """iter_all(skus=[]) должен вернуть пусто, не «всё»."""
    with Store(tmp_path / "c.db") as s:
        for i in range(1, 4):
            s.upsert(
                sku=str(i), url="", info={"title": f"T{i}"},
                markdown="", quality=9, copy_type="source",
            )
        assert list(s.iter_all(skus=[])) == []


def test_iter_all_none_skus_returns_all(tmp_path: Path) -> None:
    """skus=None (по умолчанию) — все записи."""
    with Store(tmp_path / "c.db") as s:
        for i in range(1, 4):
            s.upsert(
                sku=str(i), url="", info={"title": f"T{i}"},
                markdown="", quality=9, copy_type="source",
            )
        assert len(list(s.iter_all(skus=None))) == 3


# --- B1b: CLI ---------------------------------------------------------


def test_flush_only_empty_returns_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """flush --only "," (пустой список) → пустой вывод, не вся коллекция."""
    db = tmp_path / "c.db"
    _seed(db, 2)
    rc = main(["--db", str(db), "flush", "--only", ","])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_flush_only_omitted_returns_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Без --only — вся коллекция."""
    db = tmp_path / "c.db"
    _seed(db, 2)
    rc = main(["--db", str(db), "flush"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## 1 — Item1" in out
    assert "## 2 — Item2" in out


# --- B1c: HTTP --------------------------------------------------------


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
    conn.close()
    return r.status, data


def test_flush_http_only_empty_returns_empty(server) -> None:
    """POST /flush {"only": []} → {"status": "empty"}, не вся коллекция."""
    port, store = server
    for i in range(1, 3):
        store.upsert(
            sku=str(i), url="", info={"title": f"T{i}"},
            markdown="# T\n", quality=9, copy_type="source",
        )
    body = json.dumps(
        {"format": "markdown", "to": "response", "only": []}
    ).encode("utf-8")
    st, data = _req(
        port, "POST", "/flush", body=body,
        headers={"Content-Type": "application/json"},
    )
    assert st == 200
    j = json.loads(data)
    assert j.get("status") == "empty"
    assert j.get("items") == 0
