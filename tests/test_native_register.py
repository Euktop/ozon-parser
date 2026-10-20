"""Тесты native/manifest.py и native/register.py.

Реальный registry не трогаем — winreg мокается. На Unix пишем в tmp_path
через monkeypatch Path.home.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ozon_parser.native import manifest as M
from ozon_parser.native import register as R


# --- manifest ----------------------------------------------------------


def test_build_manifest_structure(tmp_path: Path) -> None:
    host = tmp_path / "host.exe"
    host.write_bytes(b"")
    m = M.build_manifest(host, "abcdefghijklmnop")
    assert m["name"] == M.HOST_NAME
    assert m["type"] == "stdio"
    assert m["path"] == str(host.resolve())
    assert m["allowed_origins"] == ["chrome-extension://abcdefghijklmnop/"]


def test_manifest_json_valid(tmp_path: Path) -> None:
    s = M.manifest_json(tmp_path / "h", "xid")
    parsed = json.loads(s)
    assert parsed["name"] == M.HOST_NAME


# --- register: Linux ---------------------------------------------------


def test_register_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    res = R.register(Path("/host"), "ext-id-123")
    assert res.ok
    assert "chrome" in res.registered
    target = tmp_path / ".config/google-chrome/NativeMessagingHosts/com.ozon_parser.json"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == ["chrome-extension://ext-id-123/"]


def test_register_linux_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    R.register(Path("/host1"), "ext-id")
    R.register(Path("/host2"), "ext-id")
    target = tmp_path / ".config/google-chrome/NativeMessagingHosts/com.ozon_parser.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    # Path.resolve() использует семантику текущей ОС: на Windows "/host2"
    # даёт "D:\\host2" (мок sys.platform=linux не меняет Path). На реальном
    # Linux — "/host2". Проверяем суффикс, работает в обоих случаях.
    assert data["path"].endswith("host2")


def test_unregister_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    R.register(Path("/host"), "ext-id")
    res = R.unregister()
    assert res.ok
    assert "chrome" in res.removed
    target = tmp_path / ".config/google-chrome/NativeMessagingHosts/com.ozon_parser.json"
    assert not target.exists()


# --- register: macOS --------------------------------------------------


def test_register_darwin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    res = R.register(Path("/host"), "ext-id")
    assert res.ok
    target = (
        tmp_path / "Library/Application Support/Google/Chrome/"
        "NativeMessagingHosts/com.ozon_parser.json"
    )
    assert target.exists()


# --- register: Windows ------------------------------------------------


def test_register_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    registered: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        R, "_register_registry",
        lambda key, manifest: registered.append((key, manifest)),
    )
    res = R.register(Path("/host.exe"), "ext-id")

    assert res.ok
    # Все 6 браузеров зарегистрированы
    assert {n for n, _ in [(n, k) for n, k in M.BROWSERS["win32"]]} == set(res.registered)
    assert len(registered) == len(M.BROWSERS["win32"])
    # Один общий manifest в LOCALAPPDATA
    manifest_path = tmp_path / "ozon-parser/native/com.ozon_parser.json"
    assert manifest_path.exists()
    # Все key'и смотрят на этот manifest
    for _key, path in registered:
        assert path == manifest_path


# --- unsupported -------------------------------------------------------


def test_register_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "plan9")
    res = R.register(Path("/host"), "id")
    assert not res.ok
    assert "unsupported" in res.message


def test_unregister_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "plan9")
    res = R.unregister()
    assert not res.ok
    assert "unsupported" in res.message
