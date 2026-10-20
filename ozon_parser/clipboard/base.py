"""Протокол clipboard-бэкенда.

Каждый бэкенд реализует два метода:

- ``read_text()`` — plain-text из клипборда (всегда, fallback на ``""``).
- ``read_html()`` — rich HTML из клипборда, если ОС/инструменты позволяют
  (иначе ``None``).

Протокол намеренно минимальный: запись в клипборд идёт через ``pyperclip``
в CLI (``flush --to clipboard``), а не через бэкенд. Так проще: чтение —
платформенно-специфично, запись — универсальна.
"""

from __future__ import annotations

from typing import Protocol


class ClipboardBackend(Protocol):
    """Интерфейс, который реализует каждый бэкенд."""

    name: str

    def read_text(self) -> str:
        """Возвращает plain-text из клипборда. Никогда не падает — ``""``."""
        ...

    def read_html(self) -> str | None:
        """Возвращает rich HTML (CF_HTML / text/html) или ``None``."""
        ...
