"""Clipboard backends с выбором по ``sys.platform``.

Публичный API:

- ``get_backend() -> ClipboardBackend`` — синглтон для текущей платформы.
  Windows → ``WinBackend``, macOS → ``MacBackend``, Linux → ``LinuxBackend``,
  иначе (или при ошибке импорта) → ``PyperclipBackend`` (только text).
- ``reset_backend()`` — сбрасывает кэш (для тестов).

Бэкенды импортируются лениво, чтобы не тянуть pywin32 на Linux и т.п.
"""

from __future__ import annotations

import logging
import sys

from ozon_parser.clipboard.base import ClipboardBackend

LOG = logging.getLogger("ozon_parser.clipboard")

_backend: ClipboardBackend | None = None


class PyperclipBackend:
    """Fallback: только plain-text через pyperclip."""

    name = "pyperclip"

    def read_text(self) -> str:
        try:
            import pyperclip
            return pyperclip.paste() or ""
        except Exception as e:
            LOG.debug("pyperclip.paste: %s", e)
            return ""

    def read_html(self) -> str | None:
        return None


def _build_backend() -> ClipboardBackend:
    if sys.platform == "win32":
        try:
            from ozon_parser.clipboard.win import WinBackend
            return WinBackend()
        except Exception as e:
            LOG.warning("win backend unavailable (%s) — falling back", e)
            return PyperclipBackend()
    if sys.platform == "darwin":
        try:
            from ozon_parser.clipboard.mac import MacBackend
            return MacBackend()
        except Exception as e:
            LOG.warning("mac backend unavailable (%s) — falling back", e)
            return PyperclipBackend()
    if sys.platform.startswith("linux"):
        try:
            from ozon_parser.clipboard.linux import LinuxBackend
            return LinuxBackend()
        except Exception as e:
            LOG.warning("linux backend unavailable (%s) — falling back", e)
            return PyperclipBackend()
    LOG.warning("unknown platform %r — using pyperclip fallback", sys.platform)
    return PyperclipBackend()


def get_backend() -> ClipboardBackend:
    """Возвращает синглтон-бэкенд для текущей платформы."""
    global _backend
    if _backend is None:
        _backend = _build_backend()
        LOG.debug("clipboard backend: %s", _backend.name)
    return _backend


def reset_backend() -> None:
    """Сбрасывает кэш (нужно для тестов)."""
    global _backend
    _backend = None


__all__ = ["ClipboardBackend", "PyperclipBackend", "get_backend", "reset_backend"]
