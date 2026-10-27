"""Desktop-уведомления (toast) на успешный ingest.

Best-effort: если backend недоступен (powershell заблокирован, osascript
не отдаёт права, notify-send не установлен), тихо no-op. Уведомление
НИКОГДА не должно ломать ingest.

Платформенные backends:

- Windows: powershell + System.Windows.Forms.NotifyIcon (встроен).
- macOS:   osascript -e 'display notification ...'.
- Linux:   notify-send (libnotify).

Запускается через subprocess.Popen (fire-and-forget), чтобы не блокировать
daemon/once на время показа toast'а (3 сек на Windows).
"""

from __future__ import annotations

import logging
import subprocess
import sys

LOG = logging.getLogger("ozon_parser.notifier")


_PS_SCRIPT_TEMPLATE = (
    "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null;"
    "[System.Reflection.Assembly]::LoadWithPartialName('System.Drawing') | Out-Null;"
    "$n = New-Object System.Windows.Forms.NotifyIcon;"
    "$n.Icon = [System.Drawing.SystemIcons]::Information;"
    "$n.BalloonTipTitle = '{title}';"
    "$n.BalloonTipText = '{body}';"
    "$n.Visible = $true;"
    "$n.ShowBalloonTip(3000);"
    "Start-Sleep -Seconds 3;"
    "$n.Dispose()"
)


def _powershell(title: str, message: str) -> None:
    """Windows: balloon tip через PowerShell (без сторонних зависимостей)."""
    t = str(title).replace("'", "''").replace("\n", " ")
    m = str(message).replace("'", "''").replace("\n", " ")
    # str.replace, а не str.format: title/body с фигурными скобками
    # (например, «Набор {5 шт}») раньше валили .format() с KeyError.
    # Это осознанный workaround — не менять на .format()!
    script = _PS_SCRIPT_TEMPLATE.replace("{title}", t).replace("{body}", m)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _osascript(title: str, message: str) -> None:
    """macOS: display notification через osascript."""
    t = str(title).replace('"', '\\"').replace("\n", " ")
    m = str(message).replace('"', '\\"').replace("\n", " ")
    script = f'display notification "{m}" with title "{t}"'
    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _notify_send(title: str, message: str) -> None:
    """Linux: notify-send из libnotify."""
    subprocess.Popen(
        ["notify-send", str(title), str(message)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _pick_backend():
    if sys.platform == "win32":
        return _powershell
    if sys.platform == "darwin":
        return _osascript
    return _notify_send


def notify(title: str, message: str) -> bool:
    """Показать toast. Возвращает True, если Popen удался.

    Не бросает исключений — best-effort.
    """
    backend = _pick_backend()
    try:
        backend(title, message)
        return True
    except Exception as e:
        LOG.debug("notify failed via %s: %s", backend.__name__, e)
        return False


def notify_ingest(
    info: dict,
    *,
    is_new: bool,
    store_count: int | None = None,
) -> bool:
    """Уведомление о новом/обновлённом товаре.

    info — как возвращает parse_product; берём title и sku.
    store_count — опционально, добавит «всего N» в текст.
    """
    title = str(info.get("title") or "")[:80]
    sku = str(info.get("sku") or "")
    status = "Добавлено" if is_new else "Обновлено"
    body = f"{status}: {title}" if title else status
    if sku:
        body += f" [{sku}]"
    if store_count is not None:
        body += f" · всего {store_count}"
    return notify("Ozon Parser", body)
