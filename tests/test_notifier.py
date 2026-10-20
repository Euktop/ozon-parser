"""Тесты notifier: dispatch по платформе, best-effort на ошибках."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock

import pytest

import ozon_parser.notifier as N


@pytest.fixture(autouse=True)
def _no_real_popen(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Не даём реальному subprocess.Popen сработать в тестах."""
    fake = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", fake)
    return fake


# --- выбор бэкенда ---------------------------------------------------------


def test_pick_backend_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert N._pick_backend() is N._powershell


def test_pick_backend_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert N._pick_backend() is N._osascript


def test_pick_backend_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert N._pick_backend() is N._notify_send


# --- поведение при ошибках ------------------------------------------------


def test_notify_returns_true_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(N, "_pick_backend", lambda: lambda t, m: None)
    assert N.notify("title", "msg") is True


def test_notify_returns_false_on_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(t: str, m: str) -> None:
        raise RuntimeError("no shell")

    monkeypatch.setattr(N, "_pick_backend", lambda: boom)
    assert N.notify("title", "msg") is False


def test_notify_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(t: str, m: str) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(N, "_pick_backend", lambda: boom)
    # не должно падать
    result = N.notify("title", "msg")
    assert result is False


# --- экранирование --------------------------------------------------------


def test_powershell_escapes_single_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_popen(args, **kw):
        captured["args"] = args
        return None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    N._powershell("it's title", "it's body")
    script = captured["args"][-1]
    assert "it''s title" in script
    assert "it''s body" in script


def test_powershell_replaces_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_popen(args, **kw):
        captured["args"] = args
        return None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    N._powershell("t", "line1\nline2")
    script = captured["args"][-1]
    assert "line1 line2" in script


def test_osascript_escapes_double_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_popen(args, **kw):
        captured["args"] = args
        return None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    N._osascript('title "x"', 'body "y"')
    script = captured["args"][-1]
    assert '\\"x\\"' in script
    assert '\\"y\\"' in script


# --- notify_ingest ---------------------------------------------------------


def test_notify_ingest_new(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake(title, msg):
        captured["title"] = title
        captured["msg"] = msg

    monkeypatch.setattr(N, "_pick_backend", lambda: fake)
    assert N.notify_ingest({"title": "T", "sku": "123"}, is_new=True) is True
    assert "Добавлено" in captured["msg"]
    assert "[123]" in captured["msg"]


def test_notify_ingest_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake(title, msg):
        captured["msg"] = msg

    monkeypatch.setattr(N, "_pick_backend", lambda: fake)
    N.notify_ingest({"title": "T", "sku": "1"}, is_new=False)
    assert "Обновлено" in captured["msg"]


def test_notify_ingest_with_store_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake(title, msg):
        captured["msg"] = msg

    monkeypatch.setattr(N, "_pick_backend", lambda: fake)
    N.notify_ingest({"title": "T", "sku": "1"}, is_new=True, store_count=5)
    assert "всего 5" in captured["msg"]
