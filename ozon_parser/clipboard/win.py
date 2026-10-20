"""Windows clipboard backend: pywin32 + CF_HTML.

CF_HTML — формат Windows Clipboard, в котором браузеры (Chrome, Edge,
Firefox) кладут rich-HTML. Данные начинаются с текстового заголовка
(``Version:0.9``, ``StartHTML:...``, ``EndHTML:...``) и продолжаются
HTML-фрагментом. См. спецификацию Microsoft «HTML Clipboard Format».

pywin32 импортируется только на Windows. На других платформах этот
модуль не используется (``get_backend()`` его не загружает), поэтому
безопасно держать его в пакете.
"""

from __future__ import annotations

import logging
import re
import sys

import pyperclip

LOG = logging.getLogger("ozon_parser.clipboard.win")

CF_HTML = "HTML Format"

_win32clipboard = None
if sys.platform == "win32":
    try:
        import win32clipboard as _win32clipboard  # type: ignore[no-redef]
    except Exception as e:  # pragma: no cover — зависит от окружения
        LOG.info("pywin32 не установлен (%s) — rich HTML читаться не будет", e)

_START_HTML_RE = re.compile(r"StartHTML:(\d+)")


def _strip_cf_html_header(raw: str) -> str:
    """Отрезает заголовок CF_HTML по смещению StartHTML."""
    m = _START_HTML_RE.search(raw)
    if not m:
        return raw
    try:
        return raw[int(m.group(1)):]
    except (ValueError, IndexError):
        return raw


def read_text() -> str:
    """Plain-text из клипборда Windows."""
    try:
        return pyperclip.paste() or ""
    except Exception as e:
        LOG.debug("pyperclip.paste: %s", e)
        return ""


def read_html() -> str | None:
    """Rich HTML из клипборда Windows (CF_HTML). None, если pywin32 нет."""
    if _win32clipboard is None:
        return None
    try:
        _win32clipboard.OpenClipboard()
        try:
            fmt = _win32clipboard.RegisterClipboardFormat(CF_HTML)
            if not _win32clipboard.IsClipboardFormatAvailable(fmt):
                return None
            raw = _win32clipboard.GetClipboardData(fmt)
        finally:
            _win32clipboard.CloseClipboard()
    except Exception as e:
        LOG.debug("read_html: %s", e)
        return None

    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw.decode("cp1251", "ignore")

    return _strip_cf_html_header(raw)


class WinBackend:
    """Бэкенд для Windows."""

    name = "win32"

    def read_text(self) -> str:
        return read_text()

    def read_html(self) -> str | None:
        return read_html()
