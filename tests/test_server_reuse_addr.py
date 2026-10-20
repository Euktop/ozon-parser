"""B10: SO_REUSEADDR hazard на Windows.

На Windows ThreadingHTTPServer.allow_reuse_address = True позволяет
ДВУМ живым процессам bind'иться на один порт. После 2-3 `service install`
без `uninstall` накапливаются зомби в LISTENING — запросы идут только
к первому, второй висит молча.

На Unix SO_REUSEADDR семантически разрешает bind только после
TIME_WAIT — это то, что нужно для перезапуска сервера без паузы.

Фикс: subclass с allow_reuse_address = (sys.platform != "win32").
"""
from __future__ import annotations

import sys

from ozon_parser.server import _ThreadingHTTPServer


def test_reuse_address_matches_platform() -> None:
    """На Windows — False (иначе двойной bind), на остальных — True."""
    expected = sys.platform != "win32"
    assert _ThreadingHTTPServer.allow_reuse_address is expected
