"""Тесты эвристики quality_score."""

from __future__ import annotations

from ozon_parser.quality import quality_score


def test_empty() -> None:
    assert quality_score({}) == 0


def test_full() -> None:
    info = {
        "title": "T",
        "price": "100",
        "seller_name": "S",
        "rating": "4.9",
        "characteristics": [["k", "v"]],
        "description": "D",
        "images": ["i"],
    }
    assert quality_score(info) == 9


def test_price_card_counts_as_price() -> None:
    assert quality_score({"price_card": "899"}) == 2


def test_partial() -> None:
    assert quality_score({"title": "T"}) == 1
    assert quality_score({"title": "T", "rating": "4.5"}) == 2
    assert quality_score({"seller_name": "S"}) == 2
