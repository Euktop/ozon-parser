"""Backward-compat shim. Реальная реализация — ozon_parser.domain.quality.

Оставлено для обратной совместимости на время миграции (см. ADR-3
в плане этапа 3). Будет удалено на Stage 5.
"""
from ozon_parser.domain.quality import quality_score  # noqa: F401

__all__ = ["quality_score"]
