"""Рендер коллекции в разные форматы: markdown, jsonl, csv.

Используется CLI-командой `flush` и (в будущем) HTTP-эндпоинтом `POST /flush`.
Никакого I/O здесь нет: только преобразование list[dict] → str.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any


CSV_FIELDS = [
    "sku",
    "title",
    "brand",
    "price",
    "rating",
    "seller",
    "copy_type",
    "quality",
    "url",
    "created_at",
    "updated_at",
]


def _jsonable(item: dict[str, Any]) -> dict[str, Any]:
    """Готовим запись к JSON: исключаем raw_html (bytes | None).

    raw_html — сырой HTML, он не должен утекать в JSONL/CSV.
    Фильтруем по имени ключа, а не по типу: страхует и от bytes,
    и от str, и от None.

    Поле `json` — строка с сериализованным info. Раскрываем его
    в `info` (dict), чтобы потребитель JSONL не парсил второй раз.
    """
    out = {k: v for k, v in item.items() if k != "raw_html"}
    raw_json = out.pop("json", None)
    if isinstance(raw_json, str):
        try:
            out["info"] = json.loads(raw_json)
        except (ValueError, TypeError):
            out["info"] = None
    return out


def render_collection_markdown(items: list[dict[str, Any]]) -> str:
    """Markdown-сборник с TOC и заголовками второго уровня на каждый SKU."""
    if not items:
        return ""

    today = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append(f"# Ozon collection — {today} ({len(items)} items)")
    lines.append("")

    for it in items:
        title = it.get("title") or it["sku"]
        lines.append(f"- [{title} ({it['sku']})](#sku-{it['sku']})")
    lines.append("")

    for it in items:
        title = it.get("title") or it["sku"]
        lines.append("---")
        lines.append("")
        lines.append(f'<a id="sku-{it["sku"]}"></a>')
        lines.append(f"## {it['sku']} — {title}")
        lines.append("")
        md = (it.get("markdown") or "").rstrip()
        if md:
            lines.append(md)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_collection_jsonl(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    return "".join(
        json.dumps(_jsonable(it), ensure_ascii=False, separators=(",", ":")) + "\n"
        for it in items
    )


def render_collection_csv(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for it in items:
        w.writerow({k: ("" if it.get(k) is None else it.get(k)) for k in CSV_FIELDS})
    return buf.getvalue()


def render_collection(items: list[dict[str, Any]], fmt: str) -> str:
    if fmt == "markdown":
        return render_collection_markdown(items)
    if fmt == "jsonl":
        return render_collection_jsonl(items)
    if fmt == "csv":
        return render_collection_csv(items)
    raise ValueError(f"unknown format: {fmt}")


# ============================================================================
#  per-SKU export (`flush --per-sku`)
# ============================================================================


def slugify(text: str, max_len: int = 60) -> str:
    """Готовит slug для имени файла. Сохраняет кириллицу и цифры.

    - lower, whitespace → '-'
    - выкидывает всё, кроме a-z, 0-9, '-', '_', а-я, ё
    - коллапсит повторные '-'
    - обрезает до max_len
    """
    t = str(text or "").lower().strip()
    t = re.sub(r"\s+", "-", t)
    t = re.sub(r"[^a-z0-9\-_а-яё]", "", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t[:max_len].rstrip("-")


def _yaml_str(value: str) -> str:
    """Single-quoted YAML string; `'` → `''`."""
    return "'" + str(value).replace("'", "''") + "'"


def render_item_markdown(item: dict[str, Any]) -> str:
    """Markdown одного товара с YAML frontmatter.

    Используется в `flush --per-sku`. Формат совместим с Obsidian/Hugo:
    frontmatter — машиночитаемые метаданные, тело — рендер из `markdown`.
    """
    fm: list[str] = ["---"]
    fm.append(f"sku: {_yaml_str(item.get('sku') or '')}")
    fm.append(f"title: {_yaml_str(item.get('title') or '')}")
    if item.get("url"):
        fm.append(f"url: {_yaml_str(item['url'])}")
    if item.get("brand"):
        fm.append(f"brand: {_yaml_str(item['brand'])}")
    if item.get("price"):
        fm.append(f"price: {_yaml_str(item['price'])}")
    if item.get("rating"):
        fm.append(f"rating: {_yaml_str(item['rating'])}")
    if item.get("seller"):
        fm.append(f"seller: {_yaml_str(item['seller'])}")
    if item.get("copy_type"):
        fm.append(f"copy_type: {_yaml_str(item['copy_type'])}")
    if item.get("quality") is not None:
        fm.append(f"quality: {int(item.get('quality') or 0)}")
    if item.get("created_at"):
        fm.append(f"collected_at: {_yaml_str(item['created_at'])}")
    if item.get("updated_at"):
        fm.append(f"updated_at: {_yaml_str(item['updated_at'])}")
    fm.append("---")
    fm.append("")

    body = (item.get("markdown") or "").rstrip()
    return "\n".join(fm) + body + "\n"


def per_sku_file_name(item: dict[str, Any]) -> str:
    """Имя файла для `flush --per-sku`: {sku}-{slug}.md.

    Если slug пуст — {sku}.md. Если и sku пуст — 'unknown-...'.
    """
    sku = str(item.get("sku") or "unknown").strip() or "unknown"
    slug = slugify(item.get("title") or "")
    if slug:
        return f"{sku}-{slug}.md"
    return f"{sku}.md"
