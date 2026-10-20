"""Тесты определения типа копии.

Две группы:

1. Синтетические тесты на минимальных строках — работают всегда,
   без фикстур, проверяют саму логику `detect_copy_type`.
2. Регрессия на реальных файлах `tests/fixtures/source.html` и
   `tests/fixtures/rendered.html` — `skip`, пока файлов нет.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ozon_parser.detection import (
    detect_copy_type,
    is_usable_ozon_copy,
    looks_like_ozon_product_page,
)

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = FIXTURES / "source.html"
RENDERED = FIXTURES / "rendered.html"


def _load(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"fixture not found: {path}")
    return path.read_text(encoding="utf-8")


# --- синтетика: не требует фикстур ----------------------------------------


def test_minimal_source() -> None:
    html = '<div data-server-rendered="true" id="__ozon"><div>x</div></div>'
    assert detect_copy_type(html) == "source"


def test_minimal_rendered() -> None:
    html = '<div id="__ozon"><div class="client-state"></div></div>'
    assert detect_copy_type(html) == "rendered"


def test_layoutpage_rendered() -> None:
    html = '<div id="layoutPage"><div>x</div></div>'
    assert detect_copy_type(html) == "rendered"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "hello",
        "<html><body>not ozon</body></html>",
        "<div>no id at all</div>",
    ],
)
def test_garbage_is_unknown(payload: str) -> None:
    assert detect_copy_type(payload) == "unknown"


# --- регрессия на реальных фикстурах (skip если файлов нет) ---------------


def test_real_source_is_source() -> None:
    assert detect_copy_type(_load(SOURCE)) == "source"


def test_real_rendered_is_rendered() -> None:
    assert detect_copy_type(_load(RENDERED)) == "rendered"


def test_real_source_and_rendered_differ() -> None:
    assert detect_copy_type(_load(SOURCE)) != detect_copy_type(_load(RENDERED))


def test_real_source_looks_like_pdp() -> None:
    assert looks_like_ozon_product_page(_load(SOURCE))


def test_real_rendered_looks_like_pdp() -> None:
    assert looks_like_ozon_product_page(_load(RENDERED))


def test_real_source_is_usable() -> None:
    assert is_usable_ozon_copy(_load(SOURCE))


def test_real_rendered_is_usable() -> None:
    assert is_usable_ozon_copy(_load(RENDERED))
