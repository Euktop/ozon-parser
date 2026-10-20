"""Тесты clipboard-бэкендов.

Реальный клипборд не трогается: тесты работают либо с чистыми функциями
(парсинг заголовков), либо с monkeypatched subprocess / pyperclip.

Тесты в ``test_daemon.py`` по-прежнему monkeypatch'ат
``ozon_parser.daemon.read_text_clipboard`` / ``read_html_clipboard`` —
это тонкие шимы над ``get_backend()``. Здесь проверяем сам роутинг
и парсеры.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import ozon_parser.clipboard as cb


# --- routing ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Перед каждым тестом сбрасываем закэшированный бэкенд."""
    monkeypatch.setattr(cb, "_backend", None)


def test_windows_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    b = cb.get_backend()
    assert b.name == "win32"


def test_macos_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    b = cb.get_backend()
    assert b.name == "darwin"


def test_linux_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    b = cb.get_backend()
    assert b.name == "linux"


def test_unknown_platform_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "plan9")
    b = cb.get_backend()
    assert b.name == "pyperclip"


def test_get_backend_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    b1 = cb.get_backend()
    b2 = cb.get_backend()
    assert b1 is b2


def test_reset_backend_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    b1 = cb.get_backend()
    cb.reset_backend()
    b2 = cb.get_backend()
    assert b1 is not b2


def test_pyperclip_backend_read_html_none() -> None:
    b = cb.PyperclipBackend()
    assert b.read_html() is None


# --- win ------------------------------------------------------------------


def test_win_strip_header_with_valid_offset() -> None:
    from ozon_parser.clipboard.win import _strip_cf_html_header
    body = "<html><body>hello</body></html>"
    prefix_template = "StartHTML:{off:010d}|"
    real_off = len(prefix_template.format(off=0))
    raw = prefix_template.format(off=real_off) + body
    assert _strip_cf_html_header(raw) == body


def test_win_strip_header_no_header() -> None:
    from ozon_parser.clipboard.win import _strip_cf_html_header
    raw = "<html><body>x</body></html>"
    assert _strip_cf_html_header(raw) == raw


# --- mac ------------------------------------------------------------------


def test_mac_decode_apple_data_utf8() -> None:
    from ozon_parser.clipboard.mac import _decode_apple_data
    # "<h1" в hex = 3C 68 31
    assert _decode_apple_data("«data HTML3C6831»") == "<h1"


def test_mac_decode_apple_data_invalid() -> None:
    from ozon_parser.clipboard.mac import _decode_apple_data
    assert _decode_apple_data("no data here") is None
    assert _decode_apple_data("«data HTMLZZZ»") is None  # не hex — regex не матчит


# --- linux ----------------------------------------------------------------


def test_linux_prefers_wl_paste(monkeypatch: pytest.MonkeyPatch) -> None:
    from ozon_parser.clipboard import linux as L
    monkeypatch.setattr(L, "_which", lambda n: "/usr/bin/" + n if n == "wl-paste" else None)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"hello", stderr=b"")

    monkeypatch.setattr(L.subprocess, "run", fake_run)
    assert L.read_text() == "hello"
    assert calls[0][0] == "wl-paste"


def test_linux_falls_back_to_xclip(monkeypatch: pytest.MonkeyPatch) -> None:
    from ozon_parser.clipboard import linux as L
    monkeypatch.setattr(L, "_which", lambda n: "/usr/bin/" + n if n == "xclip" else None)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=b"world", stderr=b"")

    monkeypatch.setattr(L.subprocess, "run", fake_run)
    assert L.read_text() == "world"


def test_linux_html_none_when_no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from ozon_parser.clipboard import linux as L
    monkeypatch.setattr(L, "_which", lambda n: None)
    assert L.read_html() is None


# --- daemon integration ---------------------------------------------------


def test_daemon_uses_clipboard_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """daemon.read_text_clipboard() / read_html_clipboard() делегируют в бэкенд."""
    from ozon_parser import daemon as d

    class FakeBackend:
        name = "fake"

        def read_text(self) -> str:
            return "TXT"

        def read_html(self) -> str | None:
            return "<HTML>"

    monkeypatch.setattr(d, "get_backend", lambda: FakeBackend())
    assert d.read_text_clipboard() == "TXT"
    assert d.read_html_clipboard() == "<HTML>"
