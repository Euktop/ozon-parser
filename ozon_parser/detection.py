"""Backward-compat shim. Реальная реализация — ozon_parser.domain.detection.

Оставлено для обратной совместимости на время миграции (см. ADR-3
в плане этапа 3). Будет удалено на Stage 5 после того, как все импорты
переедут на новый путь.
"""
from ozon_parser.domain.detection import (  # noqa: F401
    detect_copy_type,
    is_usable_ozon_copy,
    looks_like_ozon_product_page,
)

__all__ = [
    "detect_copy_type",
    "is_usable_ozon_copy",
    "looks_like_ozon_product_page",
]
