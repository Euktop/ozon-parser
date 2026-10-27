"""Dev-mode: быстрая итерация без переустановки приложения.

- ``ozon-parser dev register`` — регистрация Native Messaging host как
  wrapper (.bat/.sh), а не PyInstaller .exe. Работает только из editable
  install: frozen .exe — это уже прод-режим.
- ``ozon-parser dev serve`` — HTTP-сервер с автоперезапуском при правке
  `.py`-файлов (через ``watchfiles``). Запускает дочерний
  ``python -m ozon_parser serve`` и пересоздаёт его при изменениях.

Идея: разработчик правит код → перезапускается только сервер,
а не полная пересборка (``build.bat`` + ``iscc`` + installer).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def cmd_dev_register(args: "argparse.Namespace") -> int:
    """Регистрирует Native Messaging host как wrapper-скрипт (не .exe).

    Отказывается работать из frozen .exe: dev-режим требует editable
    install (``pip install -e .``), тогда ``default_host_path()``
    возвращает ``.bat``/``.sh``-wrapper с ``python -m ...``.
    """
    from ozon_parser.native._extension_id import EXTENSION_ID
    from ozon_parser.native.register import (
        default_host_path,
        register as _register,
    )

    if getattr(sys, "frozen", False):
        print(
            "dev register: запущен frozen .exe — это не dev-режим.\n"
            "Используй `ozon-parser native register` или установщик.",
            file=sys.stderr,
        )
        return 2

    host = default_host_path()
    res = _register(host, EXTENSION_ID)
    if not res.ok:
        print(f"register failed: {res.message}", file=sys.stderr)
        return 2

    print(f"registered (dev wrapper): {host}", file=sys.stderr)
    for p in res.manifest_paths:
        print(f"  {p}", file=sys.stderr)
    print(
        "\nДальше:\n"
        "  1. chrome://extensions → Load unpacked → extension/\n"
        "  2. ozon-parser dev serve      # HTTP с hot-reload\n"
        "  3. Клик по иконке расширения — popup\n",
        file=sys.stderr,
    )
    return 0


def cmd_dev_serve(args: "argparse.Namespace") -> int:
    """HTTP-сервер с автоперезапуском при правке ``.py``.

    Супервизор: запускает дочерний ``python -m ozon_parser serve``,
    следит за ``ozon_parser/`` через ``watchfiles``, перезапускает
    процесс при изменениях. SIGINT останавливает и дочерний процесс.
    """
    try:
        from watchfiles import watch, PythonFilter
    except ImportError:
        print(
            "watchfiles не установлен — поставь `pip install -e .[dev]`",
            file=sys.stderr,
        )
        return 2

    watch_dir = Path(__file__).parent  # ozon_parser/
    child_args: list[str] = [sys.executable, "-m", "ozon_parser"]
    if args.db:
        child_args += ["--db", str(args.db)]
    if args.config:
        child_args += ["--config", str(args.config)]
    child_args.append("serve")
    if args.port is not None:
        child_args += ["--port", str(args.port)]
    if args.log_file:
        child_args += ["--log-file", args.log_file]

    def _spawn():
        return subprocess.Popen(child_args)

    proc = _spawn()
    print(
        f"dev serve: PID {proc.pid}, watching {watch_dir}",
        file=sys.stderr,
    )
    try:
        for changes in watch(str(watch_dir), watch_filter=PythonFilter()):
            print(
                f"\n[dev] changes: {len(changes)} — restart",
                file=sys.stderr,
            )
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc = _spawn()
            print(f"[dev] restarted, PID {proc.pid}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n[dev] stopping...", file=sys.stderr)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return 0
