"""Порты (интерфейсы) Clean Architecture.

Domain не зависит от конкретных классов — только от этих Protocol'ов.
Infrastructure реализует их; Application использует.

Protocol, а не abc.ABC: structural subtyping позволяет FakeRepo в тестах
без наследования — достаточно, чтобы совпадали методы. Это упрощает
тестирование use case без SQLite и без BeautifulSoup.
"""
from __future__ import annotations

from typing import Any, Iterator, Protocol


class HtmlParser(Protocol):
    """Превращает HTML в (markdown, info)."""

    def parse(
        self, html: str, *, include_images: bool = False
    ) -> tuple[str, dict[str, Any]]:
        ...


class ProductRepository(Protocol):
    """Хранилище товаров. Дедуп по SKU."""

    def upsert(
        self,
        *,
        sku: str,
        url: str,
        info: dict[str, Any],
        markdown: str,
        quality: int,
        copy_type: str,
        raw_html: bytes | None = None,
        html_text: str | None = None,
    ) -> bool:
        """True — новая запись, False — обновление."""
        ...

    def get(self, sku: str) -> dict[str, Any] | None:
        ...

    def iter_all(
        self,
        *,
        include_raw: bool = True,
        skus: list[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        ...

    def iter_with_raw(
        self, *, skus: set[str] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Стриминг записей с raw_html по одной (для reparse).

        В отличие от `iter_all(include_raw=True)`, не тянет все raw_html
        в память сразу: пиковая память — один raw_html.
        """
        ...

    def count(self) -> int:
        ...


class Notifier(Protocol):
    """Desktop-уведомления. Best-effort, не бросает."""

    def notify_ingest(
        self,
        info: dict[str, Any],
        *,
        is_new: bool,
        store_count: int | None = None,
    ) -> bool:
        ...
