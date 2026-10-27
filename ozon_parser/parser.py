r"""Парсер Ozon PDP: extractors + collect_product + render_markdown.

Порт логики из legacy ozon_clip.py v7 с двумя правками:

1. `_clean_desc_node` больше не мутирует оригинальный soup.
   Legacy использовал `copy(section)` (shallow), который шарит `contents`
   с оригиналом — `decompose()` на дочернем узле удалял его и из `soup`.
   Здесь пересобираем дерево через `BeautifulSoup(str(section))`.

2. `extract_nuxt_state` не портирован (поле `source_has_nuxt` удалено).

   `_clean_desc_node` пересобирает дерево через `BeautifulSoup(str(section), _BS_PARSER)`.

Единственный экстрактор с версионно-зависимым селектором, который пришлось
поправить: `extract_marketing_labels_dom`. Legacy искал `.b5_7_3-a4`
(ui-kit-badge v3), актуальная разметка Ozon — `.b5_7_10-a4` (v10).
Регулярка `^b5_7_\d+-a4$` покрывает все версии.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

LOG = logging.getLogger("ozon_parser.parser")

WIDGET_ID_RE = re.compile(r"^state-(.+)-(\d+)-([^-]+)-(\d+)$")
HASHTAG_RE = re.compile(r"#[\w\-]+")
LABEL_CLASS_RE = re.compile(r"^b5_7_\d+-a4$")

# lxml — C-парсер, в 3–5 раз быстрее html.parser на больших PDP.
# Fallback на html.parser, если lxml нет (например, минимальная сборка).
try:
    import lxml  # noqa: F401
    _BS_PARSER = "lxml"
except ImportError:  # pragma: no cover
    _BS_PARSER = "html.parser"


# ============================================================================
#  helpers
# ============================================================================


def norm(v: Any) -> str:
    return "" if v is None else re.sub(r"\s+", " ", str(v)).strip()


def clean_key(k: str) -> str:
    return re.sub(r"\s*:+\s*$", "", norm(k)).strip()


def clean_value(v: str) -> str:
    v = norm(v)
    v = re.sub(r"\s+,\s*", ", ", v)
    return v.strip(" ,")


def textrs_first(rs: list | None) -> str:
    if not rs:
        return ""
    parts: list[str] = []
    for item in rs:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "newLine":
            parts.append(" ")
        else:
            parts.append(str(item.get("content") or ""))
    return norm("".join(parts))


def has_currency_symbol(s: str) -> bool:
    return bool(re.search(r"[₽$€¥£₸₴₾₼]", s or ""))


def _pick_text(*candidates: Any) -> str:
    for c in candidates:
        v = norm(c)
        if v:
            return v
    return ""


def _as_dict(value: Any) -> dict:
    """Безопасно приводит client-state data к dict.

    Ozon меняет схему client-state между релизами: `data` может прийти
    списком, строкой или None. Все обращения к `.get(...)` должны идти
    через этот хелпер — иначе `AttributeError` роняет весь `parse_product`.
    """
    return value if isinstance(value, dict) else {}


# ============================================================================
#  extractors
# ============================================================================


def extract_widget_states(soup: BeautifulSoup) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for div in soup.select("div.client-state > div[id^='state-']"):
        id_ = div.get("id", "")
        raw = div.get("data-state")
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        m = WIDGET_ID_RE.match(id_)
        name = m.group(1) if m else id_
        if name in out:
            LOG.warning("duplicate widget %r on page: %s", name, id_)
            continue
        out[name] = {"_id": id_, "data": data}
    return out


def _iter_jsonld_products(data: Any):
    """Все объекты @type=Product в JSON-LD, включая @graph и массивы."""
    if isinstance(data, list):
        for item in data:
            yield from _iter_jsonld_products(item)
        return
    if not isinstance(data, dict):
        return
    t = data.get("@type")
    if t == "Product" or (isinstance(t, list) and "Product" in t):
        yield data
    for key in ("@graph", "mainEntity", "itemListElement"):
        if key in data:
            yield from _iter_jsonld_products(data[key])


def extract_jsonld(soup: BeautifulSoup) -> dict:
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        for product in _iter_jsonld_products(data):
            return product
    return {}


def extract_og(soup: BeautifulSoup) -> dict:
    og = {"title": "", "description": "", "image": ""}
    for m in soup.find_all("meta"):
        k = (m.get("property") or m.get("name") or "").lower()
        v = m.get("content") or ""
        if k == "og:title" and not og["title"]:
            og["title"] = v
        elif k == "og:description" and not og["description"]:
            og["description"] = v
        elif k == "og:image" and not og["image"]:
            og["image"] = v
    return og


def extract_full_characteristics_dom(soup: BeautifulSoup) -> list[tuple[str, str]]:
    section = soup.find(id="section-characteristics")
    if not section:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for dl in section.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            k = clean_key(dt.get_text(" "))
            v = clean_value(dd.get_text(" "))
            if not k or not v:
                continue
            key_norm = k.lower()
            if (key_norm, v) in seen:
                continue
            seen.add((key_norm, v))
            out.append((k, v))
    return out


def _clean_desc_node(section: Tag) -> str:
    """Возвращает очищенный текст описания.

    ВАЖНО: не мутирует `section` (и, следовательно, оригинальный soup).
    Legacy использовал `copy(section)` — shallow copy шарит `contents`
    с оригиналом, поэтому `decompose()` на дочернем узле удалял его
    и из оригинала. Здесь пересобираем независимое дерево.
    """
    node = BeautifulSoup(str(section), _BS_PARSER)
    for bad in node.select("#section-characteristics, button"):
        bad.decompose()
    for h in node.find_all(["h1", "h2"]):
        if norm(h.get_text(" ")).lower() in ("описание",):
            h.decompose()
    for br in node.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for el in node.find_all(["p", "h3", "h4", "li"]):
        el.insert_before(NavigableString("\n\n"))
    text = node.get_text(" ", strip=False)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_description_dom(soup: BeautifulSoup) -> str:
    nodes: list[Tag] = []
    for container in soup.select('div[data-widget="webDescription"]'):
        sd = container.find(id="section-description")
        if sd:
            nodes.append(sd)
    if not nodes:
        single = soup.find(id="section-description")
        if single:
            nodes = [single]
    if not nodes:
        return ""
    parts: list[str] = []
    for n in nodes:
        cleaned = _clean_desc_node(n)
        if cleaned:
            parts.append(cleaned)
    return "\n\n".join(parts)


def extract_hashtags_dom(soup: BeautifulSoup) -> list[str]:
    node = soup.find(attrs={"data-widget": "webHashtags"})
    if not node:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in node.find_all(string=HASHTAG_RE):
        for tag in HASHTAG_RE.findall(str(s)):
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
    return out


def extract_marketing_labels_dom(soup: BeautifulSoup) -> list[str]:
    node = soup.find(attrs={"data-widget": "webMarketingLabels"})
    if not node:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for el in node.find_all(class_=LABEL_CLASS_RE):
        txt = norm(el.get_text(" ", strip=True))
        if txt and txt not in seen:
            seen.add(txt)
            out.append(txt)
    return out


def extract_breadcrumbs_dom(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Fallback, если нет `state-breadCrumbs`. Класс `ol.ce8_7`
    версионно-зависим — primary источник всегда виджет."""
    ol = soup.select_one("ol.ce8_7")
    if not ol:
        return []
    out: list[tuple[str, str]] = []
    for li in ol.find_all("li", recursive=False):
        a = li.find("a")
        if a:
            out.append((norm(a.get_text(" ", strip=True)), a.get("href", "")))
        else:
            t = norm(li.get_text(" ", strip=True))
            if t:
                out.append((t, ""))
    return out


# ============================================================================
#  merging
# ============================================================================


def collect_product(soup: BeautifulSoup) -> dict[str, Any]:
    widgets = extract_widget_states(soup)
    jsonld = extract_jsonld(soup)
    og = extract_og(soup)

    p: dict[str, Any] = {
        "title": "",
        "brand": "",
        "sku": "",
        "description": "",
        "images": [],
        "videos_count": 0,
        "rating": "",
        "rating_text": "",
        "reviews_count": "",
        "questions_count": "",
        "price": "",
        "price_card": "",
        "price_original": "",
        "currency": "",
        "stock_hint": "",
        "promo_title": "",
        "promo_end": "",
        "seller_name": "",
        "seller_link": "",
        "seller_rating": "",
        "seller_orders": "",
        "breadcrumbs": [],
        "characteristics": [],
        "hashtags": [],
        "marketing_labels": [],
        "installment_text": "",
        "user_media_count": 0,
        "user_media_links": [],
        "url": "",
    }

    # --- 1) JSON-LD ---
    p["title"] = norm(jsonld.get("name")) or norm(og["title"])
    brand = jsonld.get("brand")
    if isinstance(brand, dict):
        p["brand"] = norm(brand.get("name"))
    elif isinstance(brand, list) and brand:
        first = brand[0]
        p["brand"] = (
            norm(first.get("name")) if isinstance(first, dict) else norm(first)
        )
    else:
        p["brand"] = norm(brand)
    p["sku"] = str(jsonld.get("sku") or "")
    p["description"] = norm(jsonld.get("description")) or norm(og["description"])
    img = jsonld.get("image") or og["image"]
    if isinstance(img, list):
        img = img[0] if img else ""
    if img:
        p["images"].append(img)

    agg = jsonld.get("aggregateRating") or {}
    if isinstance(agg, list) and agg:
        agg = agg[0]
    if isinstance(agg, dict):
        p["rating"] = norm(agg.get("ratingValue"))
        p["reviews_count"] = norm(agg.get("reviewCount"))

    offers = jsonld.get("offers") or {}
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if isinstance(offers, dict):
        p["price"] = norm(offers.get("price"))
        p["currency"] = norm(offers.get("priceCurrency"))
        p["url"] = norm(offers.get("url"))

    # --- 2) client-state widgets ---
    gal = _as_dict(widgets.get("webGallery", {}).get("data"))
    if gal:
        imgs = [
            i["src"]
            for i in (gal.get("images") or [])
            if isinstance(i, dict) and i.get("src")
        ]
        if imgs:
            p["images"] = imgs
        if gal.get("videos"):
            p["videos_count"] = len(gal["videos"])

    price_w = _as_dict(widgets.get("webPrice", {}).get("data"))
    if price_w:
        p["price_card"] = norm(price_w.get("cardPrice")) or p["price_card"]
        p["price"] = norm(price_w.get("price")) or p["price"]
        p["price_original"] = (
            norm(price_w.get("originalPrice")) or p["price_original"]
        )

    score = _as_dict(widgets.get("webReviewProductScore", {}).get("data"))
    if score:
        if score.get("totalScore") is not None:
            p["rating"] = norm(score.get("totalScore"))
        if score.get("reviewsCount") is not None:
            p["reviews_count"] = norm(score.get("reviewsCount"))
    else:
        s2 = _as_dict(widgets.get("webSingleProductScore", {}).get("data"))
        s2_text = _pick_text(s2.get("text"), textrs_first(s2.get("textRs")))
        if s2_text:
            p["rating_text"] = s2_text

    heading = _as_dict(widgets.get("webProductHeading", {}).get("data"))
    heading_title = heading.get("title")
    if isinstance(heading_title, dict):
        heading_title = _as_dict(heading_title).get("text")
    if isinstance(heading_title, str) and heading_title:
        p["title"] = norm(heading_title)

    sku_w = _as_dict(widgets.get("webDetailSKU", {}).get("data"))
    if sku_w.get("copyText"):
        p["sku"] = norm(sku_w["copyText"])

    q = _as_dict(widgets.get("webQuestionCount", {}).get("data"))
    if q.get("text"):
        qtext = re.sub(
            r"\s*вопрос\w*\s*$", "", norm(q["text"]), flags=re.IGNORECASE
        ).strip()
        p["questions_count"] = qtext

    promo = _as_dict(widgets.get("bigPromoPDP", {}).get("data"))
    if promo:
        stock_num = _as_dict(promo.get("stockNumber")).get("text")
        stock_lbl = _as_dict(promo.get("stockText")).get("text")
        if stock_num:
            p["stock_hint"] = f"{stock_num} {stock_lbl or ''}".strip()
        promo_title = _as_dict(promo.get("title")).get("text")
        if promo_title:
            p["promo_title"] = norm(promo_title)
        tb = _as_dict(promo.get("timerBadge"))
        tt = _as_dict(tb.get("timerText")).get("text")
        if tt:
            p["promo_end"] = norm(tt)

    labels_w = _as_dict(widgets.get("webMarketingLabels", {}).get("data"))
    labels = labels_w.get("labels")
    if isinstance(labels, list):
        p["marketing_labels"] = [
            norm(_as_dict(b.get("badge")).get("text"))
            for b in labels
            if isinstance(b, dict) and _as_dict(b.get("badge")).get("text")
        ]

    seller_w = _as_dict(widgets.get("webCurrentSeller", {}).get("data"))
    if seller_w:
        cell = _as_dict(seller_w.get("sellerCell"))
        cb = _as_dict(cell.get("centerBlock"))
        p["seller_name"] = _pick_text(
            (cb.get("title") or {}).get("text"),
            textrs_first((cb.get("title") or {}).get("textRs")),
        )
        act = _as_dict(_as_dict(cell.get("common")).get("action"))
        if act.get("link"):
            p["seller_link"] = act["link"]

        rating_obj = _as_dict(seller_w.get("rating"))
        title_obj = _as_dict(rating_obj.get("title"))
        p["seller_rating"] = _pick_text(
            title_obj.get("text"),
            textrs_first(title_obj.get("textRs")),
        )

        for tf in seller_w.get("trustFactors") or []:
            if not isinstance(tf, dict):
                continue
            title_obj = _as_dict(tf.get("title"))
            title_txt = _pick_text(
                title_obj.get("text"), textrs_first(title_obj.get("textRs"))
            )
            if not title_txt or "заказ" not in title_txt.lower():
                continue
            badge = _as_dict(tf.get("badge"))
            v = _pick_text(badge.get("text"), textrs_first(badge.get("textRs")))
            if v:
                p["seller_orders"] = v
                break

    if not p["brand"]:
        brand_w = _as_dict(widgets.get("webBrand", {}).get("data"))
        p["brand"] = _pick_text(
            brand_w.get("text"),
            textrs_first(brand_w.get("textRs")),
            _as_dict(brand_w.get("title")).get("text"),
        )

    inst = _as_dict(widgets.get("webInstallmentPurchase", {}).get("data"))
    if inst:
        p["installment_text"] = textrs_first(inst.get("titleRs"))

    crumbs_w = _as_dict(widgets.get("breadCrumbs", {}).get("data"))
    bc_list = crumbs_w.get("breadcrumbs")
    if isinstance(bc_list, list) and bc_list:
        p["breadcrumbs"] = [
            (norm(c.get("text")), norm(c.get("link")))
            for c in bc_list
            if isinstance(c, dict)
        ]
    else:
        p["breadcrumbs"] = extract_breadcrumbs_dom(soup)

    media_w = _as_dict(widgets.get("webListPhotos", {}).get("data"))
    mm = _as_dict(media_w.get("moreMedia"))
    if mm.get("mediaCount"):
        try:
            p["user_media_count"] = int(mm["mediaCount"])
        except (TypeError, ValueError):
            pass
    p["user_media_links"] = [
        m["previewUrl"]
        for m in (media_w.get("mediaContent") or [])
        if isinstance(m, dict) and m.get("previewUrl")
    ]

    # --- 3) DOM fallbacks ---
    chars = extract_full_characteristics_dom(soup)
    if not chars:
        chars_w = _as_dict(widgets.get("webShortCharacteristics", {}).get("data"))
        for c in (chars_w.get("characteristics") or []):
            if not isinstance(c, dict):
                continue
            t = clean_key(textrs_first(_as_dict(c.get("title")).get("textRs")))
            vals = [
                clean_value(v.get("text"))
                for v in (c.get("values") or [])
                if isinstance(v, dict)
            ]
            vals = [v for v in vals if v]
            if t and vals:
                chars.append((t, ", ".join(vals)))
    p["characteristics"] = [
        (k, v) for k, v in chars if k.lower() != "артикул"
    ]

    desc_dom = extract_description_dom(soup)
    if desc_dom and len(desc_dom) > len(p["description"] or ""):
        p["description"] = desc_dom

    p["hashtags"] = extract_hashtags_dom(soup)

    if not p["marketing_labels"]:
        p["marketing_labels"] = extract_marketing_labels_dom(soup)

    if not p["title"]:
        h1 = soup.find("h1")
        if h1:
            p["title"] = norm(h1.get_text())

    return p


# ============================================================================
#  rendering
# ============================================================================


def render_markdown(p: dict, include_images: bool = False) -> str:
    lines: list[str] = []
    if p.get("title"):
        lines += [f'# {p["title"]}', ""]

    if p.get("breadcrumbs"):
        crumb_line = " › ".join(
            (f"[{t}]({lnk})" if lnk else t) for t, lnk in p["breadcrumbs"]
        )
        lines += [crumb_line, ""]

    if p.get("marketing_labels"):
        lines += ["`" + "` · `".join(p["marketing_labels"]) + "`", ""]

    meta: list[str] = []
    if p.get("brand"):
        meta.append(f'- **Бренд:** {p["brand"]}')
    if p.get("sku"):
        meta.append(f'- **Артикул:** {p["sku"]}')
    if p.get("rating"):
        r = f'- **Рейтинг:** {p["rating"]} / 5'
        if p.get("reviews_count"):
            r += f' ({p["reviews_count"]} отзывов)'
        meta.append(r)
    elif p.get("rating_text"):
        meta.append(f'- **Рейтинг:** {p["rating_text"]}')
    if p.get("questions_count"):
        meta.append(f'- **Вопросов:** {p["questions_count"]}')

    if p.get("price") or p.get("price_card") or p.get("price_original"):
        parts = []
        if p.get("price_card"):
            parts.append(f'с Ozon Card: {p["price_card"]}')
        if p.get("price"):
            parts.append(f'обычная: {p["price"]}')
        if p.get("price_original"):
            parts.append(f'было: {p["price_original"]}')
        line = "- **Цена:** " + " • ".join(parts)
        cur = p.get("currency") or ""
        if cur and not any(has_currency_symbol(x) for x in parts):
            line += f" {cur}"
        meta.append(line)

    if p.get("stock_hint"):
        meta.append(f'- **Наличие:** {p["stock_hint"]}')
    if p.get("promo_title"):
        meta.append(f'- **Акция:** {p["promo_title"]}')
    if p.get("promo_end"):
        meta.append(f'- **Срок акции:** {p["promo_end"]}')
    if p.get("installment_text"):
        meta.append(f'- **Рассрочка:** {p["installment_text"]}')

    if p.get("seller_name"):
        s = f'- **Продавец:** {p["seller_name"]}'
        extras = []
        if p.get("seller_rating"):
            extras.append(f'рейтинг {p["seller_rating"]}')
        if p.get("seller_orders"):
            extras.append(f'{p["seller_orders"]} заказов')
        if extras:
            s += " (" + ", ".join(extras) + ")"
        if p.get("seller_link"):
            s += f' — [магазин]({p["seller_link"]})'
        meta.append(s)

    if p.get("user_media_count"):
        meta.append(f'- **Фото/видео покупателей:** {p["user_media_count"]}')
    if p.get("videos_count"):
        meta.append(f'- **Видео в галерее:** {p["videos_count"]}')

    if meta:
        lines += meta + [""]

    if include_images and p.get("images"):
        lines += [f'![{p.get("title") or "product"}]({p["images"][0]})', ""]

    if p.get("description"):
        lines += ["## Описание", ""]
        lines += p["description"].split("\n")
        lines.append("")

    if p.get("characteristics"):
        lines += ["## Характеристики", ""]
        for k, v in p["characteristics"]:
            lines.append(f"- **{k}:** {v}")
        lines.append("")

    if p.get("hashtags"):
        lines += ["## Хэштеги", ""]
        lines.append(" ".join(p["hashtags"]))
        lines.append("")

    if include_images and p.get("images") and len(p["images"]) > 1:
        lines += ["## Галерея", ""]
        for i, url in enumerate(p["images"][:20], 1):
            lines.append(f"{i}. ![]({url})")
        lines.append("")

    if include_images and p.get("user_media_links"):
        lines += ["## Медиа от покупателей", ""]
        for url in p["user_media_links"][:10]:
            lines.append(f"- ![]({url})")
        lines.append("")

    if p.get("url"):
        lines.append(f'[Ссылка на товар]({p["url"]})')

    return "\n".join(lines).strip() + "\n"


# ============================================================================
#  entry
# ============================================================================


def parse_product(
    html: str, include_images: bool = False
) -> tuple[str, dict[str, Any]]:
    soup = BeautifulSoup(html, _BS_PARSER)
    product = collect_product(soup)
    md = render_markdown(product, include_images=include_images)
    return md, product
