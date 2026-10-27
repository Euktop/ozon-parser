"""Определение типа копии Ozon PDP.

Ключевая идея: `data-server-rendered="true"` присутствует только
в исходном HTML (Ctrl+U → Ctrl+A → Ctrl+C) и удаляется Vue после
гидратации. Поэтому этот атрибут — надёжный маркер source-копии.

- source   — Ctrl+U → Ctrl+A → Ctrl+C. `data-server-rendered="true"` есть.
- rendered — Ctrl+A на странице / Inspect узла. Корень `#__ozon` есть,
             `data-server-rendered` уже нет (удалён после гидратации).
- unknown  — не похоже на Ozon PDP.

Проверено на реальной паре файлов (source / rendered) одного и того же
товара Ozon: source имеет `data-server-rendered="true"`, rendered — нет.
"""

from __future__ import annotations

MIN_HTML_LEN = 500

STRONG_MARKERS = (
    'id="__ozon"',
    'id="layoutpage"',
    'data-server-rendered="true"',
    'id="section-characteristics"',
    'data-widget="webproductmainwidget"',
    'data-widget="webpdpgrid"',
    'class="client-state"',
    "window.__nuxt__.state",
)

WEAK_MARKERS = (
    'data-widget="webprice"',
    'data-widget="webgallery"',
    'data-widget="webaddtocart"',
    'data-widget="webproductheading"',
    'data-widget="webshortcharacteristics"',
)


def looks_like_ozon_product_page(text: str) -> bool:
    """True, если payload похож на страницу товара Ozon."""
    if not text or len(text) < MIN_HTML_LEN:
        return False
    low = text.lower()
    if "ozon.ru" not in low:
        return False
    if not any(t in low for t in ("<html", "<!doctype", "<div", "<script")):
        return False
    # 2+ weak маркера легко набирается на любой странице Ozon (рекомендации
    # содержат webprice/webgallery). Требуем либо strong, либо 3+ weak.
    return any(m in low for m in STRONG_MARKERS) or sum(
        1 for m in WEAK_MARKERS if m in low
    ) >= 3


def detect_copy_type(text: str) -> str:
    """'source' | 'rendered' | 'unknown'."""
    if not text:
        return "unknown"
    low = text.lower()

    has_root = ('id="__ozon"' in low) or ('id="layoutpage"' in low)
    if not has_root:
        return "unknown"

    if 'data-server-rendered="true"' in low:
        return "source"

    return "rendered"


def is_usable_ozon_copy(text: str) -> bool:
    """Копия пригодна к обработке (source или rendered, но не unknown)."""
    if not looks_like_ozon_product_page(text):
        return False
    return detect_copy_type(text) != "unknown"
