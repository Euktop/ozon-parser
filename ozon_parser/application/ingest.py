"""Единый use case: HTML → parse → quality → SKU → upsert → notify.

Раньше эта последовательность была скопирована в:
  - daemon.poll_once
  - server._handle_ingest
  - cli._cmd_reparse

Теперь — одна реализация. Все три точки зовут `IngestHtmlUseCase.execute()`.

Что здесь НЕ делается (это не работа use case):
  - Чтение клипборда / HTTP — транспорт.
  - Дедуп по hash между poll'ами — это daemon.
  - Формат ответа клиенту — presentation.
"""
from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ozon_parser.domain.detection import (
    detect_copy_type,
    is_usable_ozon_copy,
)
from ozon_parser.domain.ports import HtmlParser, Notifier, ProductRepository, ParseFunc
from ozon_parser.domain.quality import quality_score

LOG = logging.getLogger("ozon_parser.application.ingest")

_SKU_URL_RE = re.compile(r"/product/[^/]*?-(\d+)(?:/|$|\?|#)")
_SKU_HTML_RE = re.compile(r'"(?:sku|skuId)"\s*:\s*"?(\d{6,})')


def _sku_from_url(url: str) -> str:
    if not url:
        return ""
    m = _SKU_URL_RE.search(url)
    return m.group(1) if m else ""


def _sku_from_html(html: str) -> str:
    if not html:
        return ""
    m = _SKU_HTML_RE.search(html)
    return m.group(1) if m else ""


def _resolve_sku(info: dict[str, Any], payload: str) -> str:
    """SKU из info → из url → из сырого HTML. Пустая строка если нет."""
    for candidate in (
        str(info.get("sku") or "").strip(),
        _sku_from_url(str(info.get("url") or "")),
        _sku_from_html(payload),
    ):
        if candidate:
            return candidate
    return ""


@dataclass
class IngestResult:
    """Результат use case.

    `to_dict()` — для HTTP-ответа (без `info`: он слишком большой).
    `info` — доступно для notifier и callback'ов.
    """

    status: str  # added | updated | skipped | no_sku | error
    sku: str = ""
    copy_type: str = "unknown"
    quality: int = 0
    info: dict[str, Any] = field(default_factory=dict)
    raw_html_size: int = 0
    payload_len: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": self.status,
            "sku": self.sku,
            "copy_type": self.copy_type,
            "quality": self.quality,
            "payload_len": self.payload_len,
        }
        if self.raw_html_size:
            d["raw_html_size"] = self.raw_html_size
        if self.error:
            d["error"] = self.error
        return d


class IngestHtmlUseCase:
    """HTML → ProductRepository. Единственная точка ingest.

    Зависимости инжектятся: parser и repository — Protocol-порты.
    `notifier` опционален: без него use case работает (для CLI `once`, тестов).
    """

    def __init__(
        self,
        *,
        parser: HtmlParser | None,
        repository: ProductRepository,
        store_raw_html: bool = True,
        include_images: bool = False,
        notifier: Notifier | None = None,
        parse_func: ParseFunc | None = None,
    ) -> None:
        self._parser = parser
        self._repo = repository
        self._store_raw = store_raw_html
        self._include_images = include_images
        self._notifier = notifier
        # Опциональный override парсинга. Используется native_host для
        # параллельного парсинга через ProcessPoolExecutor: сам use case
        # синхронен, но parse можно выполнить в отдельном процессе и
        # дождаться результата здесь. Если None — используется self._parser.
        self._parse_func = parse_func

    def execute(self, html: str, *, url_hint: str = "") -> IngestResult:
        payload_len = len(html)

        if not is_usable_ozon_copy(html):
            return IngestResult(status="skipped", payload_len=payload_len)

        copy_type = detect_copy_type(html)

        try:
            if self._parse_func is not None:
                md, info = self._parse_func(html, self._include_images)
            elif self._parser is not None:
                md, info = self._parser.parse(
                    html, include_images=self._include_images
                )
            else:
                raise ValueError("Either parser or parse_func must be provided")
        except Exception as e:
            LOG.exception("parse_product failed: %s", e)
            return IngestResult(
                status="error",
                copy_type=copy_type,
                payload_len=payload_len,
                error=str(e),
            )

        q = quality_score(info)
        sku = _resolve_sku(info, html)
        if not sku:
            return IngestResult(
                status="no_sku",
                copy_type=copy_type,
                quality=q,
                info=info,
                payload_len=payload_len,
            )

        url = str(info.get("url") or "").strip() or url_hint

        raw_html: bytes | None = None
        if self._store_raw:
            try:
                raw_html = gzip.compress(html.encode("utf-8"))
            except Exception as e:  # pragma: no cover — encoding should never fail
                LOG.warning("gzip.compress failed: %s", e)

        is_new = self._repo.upsert(
            sku=sku,
            url=url,
            info=info,
            markdown=md,
            quality=q,
            copy_type=copy_type,
            raw_html=raw_html,
            html_text=html,
        )

        if self._notifier is not None:
            try:
                self._notifier.notify_ingest(
                    info, is_new=is_new, store_count=self._repo.count()
                )
            except Exception as e:
                LOG.debug("notifier failed: %s", e)

        return IngestResult(
            status="added" if is_new else "updated",
            sku=sku,
            copy_type=copy_type,
            quality=q,
            info=info,
            raw_html_size=len(raw_html) if raw_html else 0,
            payload_len=payload_len,
        )
