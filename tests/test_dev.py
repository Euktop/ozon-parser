"""Тесты dev-режима: register отказ от frozen .exe."""
from __future__ import annotations

import sys

import pytest

from ozon_parser.dev import cmd_dev_register


def test_dev_register_refuses_frozen(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Frozen .exe — это прод-сборка, dev-режим требует editable install."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    class _Args:
        pass

    rc = cmd_dev_register(_Args())
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "frozen" in err


def test_dev_help_lists_register_serve(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`dev --help` показывает register и serve — sanity на CLI wiring."""
    from ozon_parser.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["dev", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "register" in out
    assert "serve" in out
