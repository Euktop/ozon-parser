"""Тесты парсера.

Группы:

1. Синтетические: минимальный source-подобный HTML с виджетами
   и одиночные экстракторы — работают всегда.
2. Регрессия `_clean_desc_node`: раньше `copy()` шарил `contents`
   и `decompose()` мутировал оригинальный soup.
3. Реальные фикстуры `tests/fixtures/source.html` / `rendered.html` —
   `skip`, пока файлов нет.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from ozon_parser.parser import (
    collect_product,
    extract_description_dom,
    extract_full_characteristics_dom,
    extract_hashtags_dom,
    extract_jsonld,
    extract_og,
    extract_widget_states,
    parse_product,
    render_markdown,
)

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = FIXTURES / "source.html"
RENDERED = FIXTURES / "rendered.html"


def _load(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"fixture not found: {path}")
    return path.read_text(encoding="utf-8")


SYNTHETIC_BODY = """<!DOCTYPE html>
<html lang="ru">
<head>
  <title>Test Product - Ozon</title>
  <meta property="og:title" content="Test Product">
  <meta property="og:description" content="OG description">
  <meta property="og:image" content="https://ir.ozone.ru/cover.jpg">
  <script type="application/ld+json">{"@context":"http://schema.org","@type":"Product","name":"Test Product","brand":"BrandX","sku":"12345","description":"Long description from JSON-LD","image":"https://ir.ozone.ru/cover.jpg","aggregateRating":{"@type":"AggregateRating","ratingValue":"4.8","reviewCount":"100"},"offers":{"@type":"Offer","price":"999","priceCurrency":"RUB","url":"https://www.ozon.ru/product/12345/"}}</script>
</head>
<body>
  <div id="__ozon" data-server-rendered="true">
    <h1>Test Product</h1>
    <div id="section-characteristics">
      <dl><dt>Type</dt><dd>Cable</dd></dl>
      <dl><dt>Артикул</dt><dd>12345</dd></dl>
      <dl><dt>Length</dt><dd>1 m</dd></dl>
    </div>
    <div data-widget="webDescription">
      <div id="section-description"><p>DOM description text</p></div>
    </div>
    <div data-widget="webHashtags">
      <span>#typec</span> <span>#cable</span>
    </div>
  </div>
  <div class="client-state">
    <div id="state-webGallery-1-default-1" data-state='{"images":[{"src":"https://ir.ozone.ru/1.jpg"},{"src":"https://ir.ozone.ru/2.jpg"}]}'></div>
    <div id="state-webPrice-1-default-1" data-state='{"cardPrice":"899 \u20bd","price":"999 \u20bd","originalPrice":"1999 \u20bd"}'></div>
    <div id="state-webReviewProductScore-1-default-1" data-state='{"totalScore":4.8,"reviewsCount":100}'></div>
    <div id="state-webProductHeading-1-default-1" data-state='{"title":"Test Product from widget"}'></div>
    <div id="state-webDetailSKU-1-default-1" data-state='{"copyText":"12345"}'></div>
    <div id="state-webCurrentSeller-1-default-1" data-state='{"sellerCell":{"centerBlock":{"title":{"text":"SellerX"}},"common":{"action":{"link":"https://seller.x"}}},"rating":{"title":{"text":"4.5"}}}'></div>
  </div>
</body>
</html>"""


SYNTHETIC_RENDERED = SYNTHETIC_BODY.replace(' data-server-rendered="true"', "")


# --- синтетика -----------------------------------------------------------


def test_extract_widget_states() -> None:
    soup = BeautifulSoup(SYNTHETIC_BODY, "html.parser")
    w = extract_widget_states(soup)
    assert "webGallery" in w
    assert "webPrice" in w
    assert "webCurrentSeller" in w
    assert w["webGallery"]["data"]["images"][0]["src"].startswith("https://")


def test_extract_jsonld() -> None:
    soup = BeautifulSoup(SYNTHETIC_BODY, "html.parser")
    j = extract_jsonld(soup)
    assert j.get("name") == "Test Product"
    assert j.get("sku") == "12345"


def test_extract_og() -> None:
    soup = BeautifulSoup(SYNTHETIC_BODY, "html.parser")
    og = extract_og(soup)
    assert og["title"] == "Test Product"
    assert og["image"].startswith("https://")


def test_extract_full_characteristics() -> None:
    soup = BeautifulSoup(SYNTHETIC_BODY, "html.parser")
    chars = extract_full_characteristics_dom(soup)
    keys = [k for k, _ in chars]
    assert "Type" in keys
    assert "Length" in keys
    # `Артикул` — в chars, но в collect_product он отфильтруется
    assert "Артикул" in keys


def test_extract_hashtags() -> None:
    soup = BeautifulSoup(SYNTHETIC_BODY, "html.parser")
    tags = extract_hashtags_dom(soup)
    assert "#typec" in tags
    assert "#cable" in tags


def test_parse_product_source() -> None:
    md, info = parse_product(SYNTHETIC_BODY)
    assert info["title"] == "Test Product from widget"
    assert info["sku"] == "12345"
    assert info["brand"] == "BrandX"
    assert info["price"] == "999 \u20bd"
    assert info["price_card"] == "899 \u20bd"
    assert info["price_original"] == "1999 \u20bd"
    assert info["rating"] == "4.8"
    assert info["reviews_count"] == "100"
    assert info["seller_name"] == "SellerX"
    assert info["seller_rating"] == "4.5"
    assert len(info["images"]) == 2
    # Артикул отфильтрован
    assert all(k != "Артикул" for k, _ in info["characteristics"])
    # Длинное JSON-LD описание сохраняется, если оно длиннее DOM
    assert "Long description from JSON-LD" in info["description"]
    assert md.startswith("# Test Product from widget")


def test_parse_product_survives_non_dict_widget_data() -> None:
    """client-state data может прийти списком/строкой — парсер не падает."""
    html = (
        '<html><body><div id="__ozon" data-server-rendered="true">'
        '<h1>X</h1></div>'
        '<div class="client-state">'
        '<div id="state-webPrice-1-default-1" data-state=\'["oops"]\'></div>'
        '<div id="state-webCurrentSeller-1-default-1" data-state=\'"str"\'></div>'
        '<div id="state-webGallery-1-default-1" data-state=\'[1,2,3]\'></div>'
        '</div></body></html>'
    )
    md, info = parse_product(html)
    assert info["title"] == "X"
    assert md.startswith("# X")


def test_parse_product_rendered_same_fields() -> None:
    _, src = parse_product(SYNTHETIC_BODY)
    _, rnd = parse_product(SYNTHETIC_RENDERED)
    for key in ("title", "sku", "price", "price_card", "rating",
                "seller_name", "brand", "reviews_count"):
        assert src[key] == rnd[key], key


def test_render_markdown_no_images() -> None:
    md, info = parse_product(SYNTHETIC_BODY, include_images=False)
    assert "![" not in md
    assert info["images"]


def test_render_markdown_with_images() -> None:
    md = render_markdown(
        {
            "title": "T",
            "images": ["https://a/1.jpg", "https://a/2.jpg"],
            "description": "D",
        },
        include_images=True,
    )
    assert "![T](https://a/1.jpg)" in md
    assert "## Галерея" in md


@pytest.mark.parametrize("subdir", ["ozon_parser", "tests"])
def test_no_invalid_escape_sequences(subdir: str) -> None:
    """Защита от невалидных escape-последовательностей в docstring.

    Python 3.12+ печатает SyntaxWarning на обратных слэшах в не-raw
    строках, а в будущих версиях это станет SyntaxError. Компилируем
    все .py файлы проекта и валим тест при первом warning'е.
    """
    root = Path(__file__).parent.parent / subdir
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        for f in sorted(root.rglob("*.py")):
            src = f.read_text(encoding="utf-8")
            try:
                compile(src, str(f), "exec")
            except SyntaxWarning as e:
                pytest.fail(f"invalid escape sequence in {f}: {e}")


# --- регрессия: _clean_desc_node не мутирует soup -------------------------


def test_clean_desc_node_does_not_mutate_soup() -> None:
    # Вкладываем characteristics ВНУТРЬ description, чтобы гарантированно
    # задеть bug из legacy: copy() шарил contents, decompose() ломал оригинал.
    html = (
        '<div data-widget="webDescription">'
        '<div id="section-description">'
        '<p>Desc text</p>'
        '<div id="section-characteristics"><dl><dt>k</dt><dd>v</dd></dl></div>'
        '<button>Show more</button>'
        "</div></div>"
    )
    soup = BeautifulSoup(html, "html.parser")
    desc = extract_description_dom(soup)
    # Оригинал не тронут: characteristics на месте.
    assert soup.find(id="section-characteristics") is not None
    # Текст описания получен, служебные узлы вырезаны из рендера.
    assert "Desc text" in desc
    assert "Show more" not in desc


def test_bs_parser_matches_availability() -> None:
    """lxml в deps → _BS_PARSER = lxml; без lxml — fallback на html.parser."""
    from ozon_parser.parser import _BS_PARSER
    try:
        import lxml  # noqa: F401
        has_lxml = True
    except ImportError:
        has_lxml = False
    if has_lxml:
        assert _BS_PARSER == "lxml"
    else:
        assert _BS_PARSER == "html.parser"


# --- реальные фикстуры (skip, если файлов нет) ----------------------------


def test_real_source_parse() -> None:
    html = _load(SOURCE)
    md, info = parse_product(html)
    assert info["title"]
    assert info["sku"]
    assert info["price"] or info["price_card"]
    assert info["rating"]
    assert info["seller_name"]
    assert info["characteristics"]
    assert info["description"]
    assert md.startswith("# ")


def test_real_source_vs_rendered() -> None:
    _, src = parse_product(_load(SOURCE))
    _, rnd = parse_product(_load(RENDERED))
    for key in ("title", "sku", "brand", "rating", "seller_name"):
        assert src[key] == rnd[key], key


def test_real_source_clean_desc_node_safe() -> None:
    """Регрессия: `collect_product` не должен мутировать soup.

    Legacy `_clean_desc_node` использовал `copy(section)` (shallow), который
    шарил `contents` с оригиналом: `decompose()` на дочернем узле удалял его
    и из `soup`. Современная реализация пересобирает дерево через
    `BeautifulSoup(str(section), _BS_PARSER)` — оригинал не трогается.

    Синтетическая регрессия уже есть: `test_clean_desc_node_does_not_mutate_soup`
    (вкладывает `#section-characteristics` внутрь `#section-description`).
    Здесь проверяем **на реальном source.html**: до/после `collect_product`
    строковое представление soup идентично — значит никакая мутация не
    произошла. Не полагаемся на наличие конкретных узлов (`#section-characteristics`
    в этой PDP отсутствует — характеристики приходят из `webShortCharacteristics`).
    """
    html = _load(SOURCE)
    soup = BeautifulSoup(html, "html.parser")
    before = str(soup)
    _ = collect_product(soup)
    after = str(soup)
    assert before == after, "collect_product mutated the original soup"
