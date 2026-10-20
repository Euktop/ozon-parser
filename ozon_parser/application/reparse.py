"""Use case: reparse raw_html из ProductRepository.

Вынесено из `cli._cmd_reparse` (Этап 5 плана) — не устраняет дублирование
(единственная точка), но инкапсулирует нетривиальную логику:
  - двухфазная обработка (SKU → стриминг raw_html) — без OOM на больших БД;
  - aggregation ошибок gunzip/parse;
  - отчёт о missing raw_html.

`on_progress` — callback для presentation: CLI пишет в stderr, HTTP пишет
в JSON-ответ, тесты собирают в список. Use case не знает, куда идёт вывод.
"""
from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass, field
from typing import Callable

from ozon_parser.domain.ports import HtmlParser, ProductRepository
from ozon_parser.domain.quality import quality_score

LOG = logging.getLogger("ozon_parser.application.reparse")


@dataclass
class ReparseResult:
    """Итог reparse: сколько прошло, сколько упало, почему."""

    reparsed: int = 0
    no_raw: int = 0
    failed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)  # (sku, reason)


class ReparseUseCase:
    """Reparse raw_html из ProductRepository.

    Двухфазная обработка, чтобы не тянуть все raw_html в память:
      1. Лёгкий проход по метаданным (без raw_html) — собираем SKU.
      2. Стриминг raw_html через `iter_with_raw` — по одной записи.
    """

    def __init__(
        self,
        *,
        parser: HtmlParser,
        repository: ProductRepository,
        include_images: bool = False,
    ) -> None:
        self._parser = parser
        self._repo = repository
        self._include_images = include_images

    def execute(
        self,
        *,
        only: set[str] | None = None,
        since: str | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> ReparseResult:
        # Phase 1: SKU для обработки (лёгкий запрос, без raw_html).
        skus_to_process: set[str] = set()
        for it in self._repo.iter_all(include_raw=False):
            if only is not None and it["sku"] not in only:
                continue
            if since and (it.get("updated_at") or "") < since:
                continue
            skus_to_process.add(it["sku"])

        if not skus_to_process:
            return ReparseResult()

        # Phase 2: стриминг raw_html по одной записи.
        result = ReparseResult()
        processed: set[str] = set()
        for it in self._repo.iter_with_raw(skus=skus_to_process):
            sku = it["sku"]
            processed.add(sku)
            raw = it["raw_html"]
            try:
                html = gzip.decompress(raw).decode("utf-8", "replace")
            except Exception as e:
                result.failed += 1
                result.errors.append((sku, f"gunzip: {e}"))
                if on_progress:
                    on_progress(sku, f"gunzip failed: {e}")
                continue
            try:
                md, info = self._parser.parse(
                    html, include_images=self._include_images
                )
                qs = quality_score(info)
                self._repo.upsert(
                    sku=sku,
                    url=str(info.get("url") or it.get("url") or ""),
                    info=info,
                    markdown=md,
                    quality=qs,
                    copy_type=str(it.get("copy_type") or "unknown"),
                    raw_html=raw,
                )
                result.reparsed += 1
                if on_progress:
                    on_progress(sku, f"reparsed (quality {qs}/9)")
            except Exception as e:
                result.failed += 1
                result.errors.append((sku, f"parse: {e}"))
                if on_progress:
                    on_progress(sku, f"parse failed: {e}")

        # Phase 3: отчитаться об отсутствующих raw_html (не попали в Phase 2).
        missing = skus_to_process - processed
        for sku in sorted(missing):
            result.no_raw += 1
            if on_progress:
                on_progress(sku, "no raw_html stored — re-copy required")

        return result
