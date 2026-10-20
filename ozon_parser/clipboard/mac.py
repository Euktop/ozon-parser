"""macOS clipboard backend: pbpaste + osascript.

- plain-text: ``pbpaste`` (есть в любой macOS).
- rich HTML: ``osascript -e 'the clipboard as «class HTML»'`` возвращает
  бинарные данные в hex-представлении вида ``«data HTML3C68746D6C3E...»``.
  Декодируем hex в байты и интерпретируем как UTF-8 (иногда UTF-16LE —
  пробуем оба). Если osascript падает — ``None``, работаем только с text.
"""

from __future__ import annotations

import logging
import re
import subprocess

LOG = logging.getLogger("ozon_parser.clipboard.mac")

_APPLE_DATA_RE = re.compile(r"«data HTML([0-9A-Fa-f]+)»")


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        LOG.debug("run %s: %s", cmd, e)
        return None


def _decode_apple_data(text: str) -> str | None:
    """Декодирует ``«data HTML<hex>»`` → bytes → str (utf-8 или utf-16le)."""
    m = _APPLE_DATA_RE.search(text)
    if not m:
        return None
    try:
        raw = bytes.fromhex(m.group(1))
    except ValueError:
        return None
    for enc in ("utf-8", "utf-16le"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def read_text() -> str:
    r = _run(["pbpaste"])
    if r is None or r.returncode != 0:
        return ""
    return r.stdout.decode("utf-8", "ignore")


def read_html() -> str | None:
    r = _run(["osascript", "-e", "the clipboard as «class HTML»"])
    if r is None or r.returncode != 0:
        return None
    return _decode_apple_data(r.stdout.decode("utf-8", "ignore"))


class MacBackend:
    """Бэкенд для macOS."""

    name = "darwin"

    def read_text(self) -> str:
        return read_text()

    def read_html(self) -> str | None:
        return read_html()
