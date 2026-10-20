"""TOML-конфиг: %APPDATA%/ozon-parser/config.toml (Win, roaming),
~/Library/Application Support/ozon-parser/config.toml (mac),
$XDG_CONFIG_HOME/ozon-parser/config.toml (Linux).

Единый источник настроек. Приоритет: CLI-флаг > конфиг > встроенный дефолт.

Формат (все секции и поля опциональны):

    [input]
    http_port = 8765            # default для serve / bookmarklet --port
    poll_interval_ms = 200      # default для daemon --interval

    [parse]
    include_image_urls = false  # default для once / daemon --include-images
    min_quality_score = 4       # warning-порог (пока только читается)

    [output]
    default_format = "markdown" # default для flush --format
    default_target = "stdout"   # default для flush --to

    [notify]
    desktop = true              # задел под notifier.py (P2)

Невалидный TOML или неверный тип значения → тихий фолбэк на дефолт
с warning в лог. Пользователь не должен терять доступ к CLI из-за опечатки
в конфиге.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — tomli в pyproject для 3.10
    import tomli as tomllib  # type: ignore[no-redef]

LOG = logging.getLogger("ozon_parser.config")


TEMPLATE = """\
# Конфиг ozon-parser. Все поля опциональны; CLI-флаги имеют приоритет.
# Документация по полям — в ozon_parser/config.py.

[input]
http_port = 8765
poll_interval_ms = 200

[parse]
include_image_urls = false
min_quality_score = 4
store_raw_html = true

[output]
default_format = "markdown"
default_target = "stdout"

[notify]
desktop = true
"""


@dataclass
class Config:
    http_port: int = 8765
    poll_interval_ms: int = 200
    include_image_urls: bool = False
    min_quality_score: int = 4
    store_raw_html: bool = True
    default_format: str = "markdown"
    default_target: str = "stdout"
    notify_desktop: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_config_path() -> Path:
    """Путь к конфигу по умолчанию.

    Порядок:
      1. env `OZON_PARSER_CONFIG` — явный override (тесты, продвинутые
         пользователи, CI). Удобно для запуска с другим конфигом, не
         трогая %APPDATA%.
      2. Платформенный дефолт.

    - Windows: %APPDATA%\\ozon-parser\\config.toml  (roaming — переносим)
    - macOS:   ~/Library/Application Support/ozon-parser/config.toml
    - Linux:   $XDG_CONFIG_HOME/ozon-parser/config.toml или ~/.config/...

    Не пересекается с `store.default_db_path()`: там LOCALAPPDATA (данные),
    здесь APPDATA (настройки, переносимые между машинами).
    """
    override = os.environ.get("OZON_PARSER_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "ozon-parser" / "config.toml"


def _coerce_int(v: Any, default: int) -> int:
    # bool — подкласс int в Python; не превращаем true в 1.
    if isinstance(v, bool):
        LOG.warning("config: %r is bool, using %r (expected int)", v, default)
        return default
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        LOG.warning("config: %r is not int, using %r", v, default)
        return default


def _coerce_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if v is not None:
        LOG.warning("config: %r is not bool, using %r", v, default)
    return default


def _coerce_str(v: Any, default: str) -> str:
    if isinstance(v, str):
        return v
    if v is not None:
        LOG.warning("config: %r is not str, using %r", v, default)
    return default


def load_config(path: Path | str | None = None) -> Config:
    """Читает TOML и возвращает Config.

    - `path=None` → default_config_path().
    - Файла нет → Config() с дефолтами.
    - Невалидный TOML / неверный тип поля → поле остаётся дефолтным,
      пишется warning в лог, процесс продолжается.
    """
    p = Path(path) if path is not None else default_config_path()
    if not p.exists():
        return Config()

    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError < ValueError
        LOG.warning("config %s: %s — using defaults", p, e)
        return Config()

    if not isinstance(data, dict):
        LOG.warning("config %s: top-level is not a table — using defaults", p)
        return Config()

    cfg = Config()

    inp = data.get("input") or {}
    if isinstance(inp, dict):
        cfg.http_port = _coerce_int(inp.get("http_port"), cfg.http_port)
        cfg.poll_interval_ms = _coerce_int(
            inp.get("poll_interval_ms"), cfg.poll_interval_ms
        )

    parse_sec = data.get("parse") or {}
    if isinstance(parse_sec, dict):
        cfg.include_image_urls = _coerce_bool(
            parse_sec.get("include_image_urls"), cfg.include_image_urls
        )
        cfg.min_quality_score = _coerce_int(
            parse_sec.get("min_quality_score"), cfg.min_quality_score
        )
        cfg.store_raw_html = _coerce_bool(
            parse_sec.get("store_raw_html"), cfg.store_raw_html
        )

    out = data.get("output") or {}
    if isinstance(out, dict):
        cfg.default_format = _coerce_str(
            out.get("default_format"), cfg.default_format
        )
        cfg.default_target = _coerce_str(
            out.get("default_target"), cfg.default_target
        )

    notify = data.get("notify") or {}
    if isinstance(notify, dict):
        cfg.notify_desktop = _coerce_bool(
            notify.get("desktop"), cfg.notify_desktop
        )

    return cfg


def write_template_if_missing(path: Path | str | None = None) -> Path:
    """Создаёт файл с шаблоном, если его нет. Возвращает путь.

    Существующий файл не трогает — пользовательские правки важнее шаблона.
    """
    p = Path(path) if path is not None else default_config_path()
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(TEMPLATE, encoding="utf-8")
    return p


def open_in_editor(path: Path | str) -> int:
    """Открывает файл в $VISUAL / $EDITOR или в платформенном дефолте."""
    p = Path(path)
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        import shlex
        try:
            r = subprocess.run([*shlex.split(editor), str(p)])
            return int(r.returncode)
        except (FileNotFoundError, OSError):
            LOG.warning("editor %r not found; falling back", editor)
    if sys.platform == "win32":
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
            return 0
        except OSError as e:
            LOG.warning("startfile failed: %s", e)
            return 2
    if sys.platform == "darwin":
        try:
            return int(subprocess.run(["open", "-e", str(p)]).returncode)
        except (FileNotFoundError, OSError) as e:
            LOG.warning("open failed: %s", e)
            return 2
    try:
        return int(subprocess.run(["xdg-open", str(p)]).returncode)
    except (FileNotFoundError, OSError) as e:
        LOG.warning("xdg-open failed: %s", e)
        return 2
