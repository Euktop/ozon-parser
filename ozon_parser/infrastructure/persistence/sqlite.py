"""SqliteProductRepository — ProductRepository на SQLite.

Subclass, не alias: явная связь SqliteProductRepository ↔ Store через
issubclass, читаемые traceback'и (Store vs SqliteProductRepository),
стабильная точка входа для use case.

Поведение полностью наследуется от Store. store.py остаётся на месте
(Stage 5 может переместить, если понадобится).
"""
from __future__ import annotations

from ozon_parser.store import Store


class SqliteProductRepository(Store):
    """Explicit adapter: Store как ProductRepository.

    Не добавляет и не переопределяет методов — все 8 методов Store
    (upsert/get/list/count/iter_all/iter_with_raw/delete/clear)
    удовлетворяют Protocol ProductRepository.
    """
