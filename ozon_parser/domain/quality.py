"""Эвристика полноты распарсенного товара.

Максимум 9. Если score <= 3 и copy_type == 'rendered', вероятно
страница не догрузилась до копирования — стоит предупредить пользователя.
"""

from __future__ import annotations

from typing import Any


def quality_score(info: dict[str, Any]) -> int:
    score = 0
    if info.get("title"):
        score += 1
    if info.get("price") or info.get("price_card"):
        score += 2
    if info.get("seller_name"):
        score += 2
    if info.get("rating"):
        score += 1
    if info.get("characteristics"):
        score += 1
    if info.get("description"):
        score += 1
    if info.get("images"):
        score += 1
    return score
