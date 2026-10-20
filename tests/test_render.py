"""Тесты render: slugify, YAML frontmatter, per-sku filename."""

from __future__ import annotations

from ozon_parser.render import (
    per_sku_file_name,
    render_collection,
    render_item_markdown,
    slugify,
)


# --- slugify ---------------------------------------------------------------


def test_slugify_basic() -> None:
    assert slugify("Hello World") == "hello-world"


def test_slugify_cyrillic() -> None:
    assert slugify("Привет Мир") == "привет-мир"


def test_slugify_strips_special() -> None:
    assert slugify("Кабель Type-C / GQbox (1м)") == "кабель-type-c-gqbox-1м"


def test_slugify_collapses_dashes() -> None:
    assert slugify("a---b") == "a-b"


def test_slugify_truncates() -> None:
    assert len(slugify("a" * 200, max_len=10)) == 10


def test_slugify_empty() -> None:
    assert slugify("") == ""
    assert slugify("   ") == ""
    assert slugify("!!!") == ""


def test_slugify_keeps_yo() -> None:
    assert slugify("Ёлка") == "ёлка"


# --- per_sku_file_name -----------------------------------------------------


def test_per_sku_file_name_with_slug() -> None:
    item = {"sku": "123", "title": "Кабель Type-C"}
    assert per_sku_file_name(item) == "123-кабель-type-c.md"


def test_per_sku_file_name_no_title() -> None:
    assert per_sku_file_name({"sku": "123", "title": ""}) == "123.md"


def test_per_sku_file_name_no_sku() -> None:
    name = per_sku_file_name({"sku": "", "title": "T"})
    assert name.startswith("unknown-")


def test_per_sku_file_name_special_only_title() -> None:
    assert per_sku_file_name({"sku": "1", "title": "!!!"}) == "1.md"


# --- render_item_markdown --------------------------------------------------


def test_render_item_markdown_has_frontmatter() -> None:
    item = {
        "sku": "123",
        "title": "T",
        "url": "https://ozon.ru/p/123",
        "brand": "B",
        "price": "100",
        "rating": "4.5",
        "seller": "S",
        "copy_type": "source",
        "quality": 9,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "markdown": "# T\n\nbody\n",
    }
    out = render_item_markdown(item)
    assert out.startswith("---\n")
    assert "\n---\n" in out
    assert "sku: '123'" in out
    assert "title: 'T'" in out
    assert "quality: 9" in out
    assert "copy_type: 'source'" in out
    assert "collected_at: '2026-01-01T00:00:00Z'" in out
    assert out.endswith("\n")
    assert "# T" in out


def test_render_item_markdown_escapes_single_quote() -> None:
    item = {"sku": "1", "title": "it's a title", "markdown": ""}
    out = render_item_markdown(item)
    assert "title: 'it''s a title'" in out


def test_render_item_markdown_no_optional_fields() -> None:
    item = {"sku": "1", "title": "T", "markdown": ""}
    out = render_item_markdown(item)
    assert "sku: '1'" in out
    assert "title: 'T'" in out
    # Нет optional
    assert "url:" not in out
    assert "brand:" not in out


def test_render_collection_still_works() -> None:
    items = [{"sku": "1", "title": "T", "markdown": "# T\n"}]
    out = render_collection(items, "markdown")
    assert out.startswith("# Ozon collection")
