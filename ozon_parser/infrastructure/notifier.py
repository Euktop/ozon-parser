"""DesktopNotifier — Notifier поверх notifier.notify_ingest.

Тонкая обёртка: use case принимает объект с методом `.notify_ingest()`,
а не free-function. Best-effort наследуется от notify_ingest (никогда
не бросает).
"""
from __future__ import annotations

from typing import Any

from ozon_parser.notifier import notify_ingest as _notify_ingest


class DesktopNotifier:
    """Adapter: free-function notify_ingest → Notifier.notify_ingest."""

    def notify_ingest(
        self,
        info: dict[str, Any],
        *,
        is_new: bool,
        store_count: int | None = None,
    ) -> bool:
        return _notify_ingest(
            info, is_new=is_new, store_count=store_count
        )
