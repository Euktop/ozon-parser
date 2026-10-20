"""Единая настройка логирования для CLI.

`setup_logging(log_file, quiet)` — идемпотентно настраивает root logger:

- stderr handler (INFO, или WARNING при quiet) — формат `LEVEL: message`.
- file handler (DEBUG, если `log_file` задан) — формат
  `timestamp LEVEL logger: message`.

`default_log_path(mode)` — платформенный путь для файлового лога.

Почему LOCALAPPDATA (а не APPDATA): логи привязаны к машине, roaming
не нужен. Конфиг — наоборот, в APPDATA (`config.default_config_path`),
потому что переносимый.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def default_log_path(mode: str) -> Path:
    """Платформенный путь для лог-файла режима (serve|daemon)."""
    if sys.platform == "win32":
        base = Path(
            os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        )
        return base / "ozon-parser" / f"{mode}.log"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / f"ozon-parser-{mode}.log"
    base = Path(
        os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    )
    return base / "ozon-parser" / f"{mode}.log"


def setup_logging(
    log_file: Path | str | None = None,
    *,
    quiet: bool = False,
) -> None:
    """Идемпотентная настройка root logger'а.

    - stderr: INFO (WARNING при quiet), формат `LEVEL: message`.
    - file (если log_file): DEBUG, формат `timestamp LEVEL logger: message`.

    При ошибке создания log_file (нет прав / путь недоступен) — не падаем,
    пишем warning в stderr и продолжаем со stderr-handler'ом.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    stderr_level = logging.WARNING if quiet else logging.INFO
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(stderr_level)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(sh)

    if not log_file:
        root.setLevel(stderr_level)
        return

    path = Path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(fh)
        root.setLevel(logging.DEBUG)
    except OSError as e:
        root.setLevel(stderr_level)
        logging.getLogger("ozon_parser").warning(
            "log_file %s unavailable: %s", path, e
        )
