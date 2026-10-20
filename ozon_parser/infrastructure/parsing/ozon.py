"""OzonHtmlParser — HtmlParser на BeautifulSoup.

Тонкая обёртка над free-function `parse_product`. Причина: use case
принимает объект с методом `.parse()`, а не free-function. Это позволяет
в тестах подсовывать FakeParser без monkeypatch free-function.
"""
from __future__ import annotations

from typing import Any

from ozon_parser.parser import parse_product


class OzonHtmlParser:
    """Adapter: parse_product → HtmlParser.parse."""

    def parse(
        self, html: str, *, include_images: bool = False
    ) -> tuple[str, dict[str, Any]]:
        return parse_product(html, include_images=include_images)
