"""CLI entry point: ozon-parser.

Подкоманды:

    list        — таблица SKU → title / quality / updated_at
    show        — полный Markdown одного товара
    flush       — выгрузка коллекции в stdout / clipboard / файл
    count       — число записей
    clear       — очистка коллекции (требует --yes)
    once        — один проход по буферу
    read        — диагностика клипборда
    daemon      — фоновый цикл по буферу
    serve       — HTTP-приём из bookmarklet / расширения
    bookmarklet — генератор JS-закладки
    rm          — удалить один/несколько SKU
    reparse     — перепарсить raw_html
    export-fixture — выгрузить raw_html в tests/fixtures
    service     — автозапуск (install/uninstall/status)
    native      — регистрация Native Messaging host
    dev         — dev-режим (register/serve)
    config      — показать / отредактировать TOML-конфиг

Приоритет значений: CLI-флаг > TOML-конфиг > встроенный дефолт.
Числовые дефолты (порт, интервал, формат, target, include-images)
берутся из конфига, если соответствующий флаг не указан.
"""

from __future__ import annotations

import argparse
import gzip
import re
import signal
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Callable, Sequence

from ozon_parser import __version__
from ozon_parser.application.reparse import ReparseUseCase
from ozon_parser.bookmarklet import generate_bookmarklet
from ozon_parser.clipboard import get_backend
from ozon_parser.config import (
    default_config_path,
    load_config,
    open_in_editor,
    write_template_if_missing,
)
from ozon_parser.daemon import poll_once, run_forever
from ozon_parser.dev import cmd_dev_register, cmd_dev_serve
from ozon_parser.infrastructure.parsing.ozon import OzonHtmlParser
from ozon_parser.logging_utils import default_log_path, setup_logging
from ozon_parser.native.register import (
    default_host_path,
    register as native_register,
    unregister as native_unregister,
)
from ozon_parser.render import (
    per_sku_file_name,
    render_collection,
    render_item_markdown,
)
from ozon_parser.service import install as service_install, status as service_status, uninstall as service_uninstall
from ozon_parser.server import run_server
from ozon_parser.store import Store, default_db_path


def _open_store(db: str | None) -> Store:
    path = Path(db) if db else default_db_path()
    return Store(path)


def _cmd_list(args: argparse.Namespace) -> int:
    with _open_store(args.db) as s:
        rows = s.list()
    if not rows:
        print("(collection is empty)")
        return 0
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    header = f"{'SKU':<14} {'Q':>2}  {'UPDATED':<20}  TITLE"
    print(header)
    print("-" * len(header))
    for r in rows:
        title = r.get("title") or ""
        if len(title) > 60:
            title = title[:59] + "…"
        print(
            f"{r['sku']:<14} {int(r.get('quality') or 0):>2}  "
            f"{(r.get('updated_at') or ''):<20}  {title}"
        )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    with _open_store(args.db) as s:
        row = s.get(args.sku)
    if row is None:
        print(f"SKU not found: {args.sku}", file=sys.stderr)
        return 1
    sys.stdout.write(row.get("markdown") or "")
    return 0


def _cmd_count(args: argparse.Namespace) -> int:
    with _open_store(args.db) as s:
        print(s.count())
    return 0


def _write_output(text: str, target: str, *, quiet: bool = False) -> int:
    if target == "stdout":
        sys.stdout.write(text)
        return 0
    if target == "clipboard":
        try:
            import pyperclip
        except Exception as e:
            print(f"pyperclip not available: {e}", file=sys.stderr)
            return 2
        try:
            pyperclip.copy(text)
        except Exception as e:
            print(f"clipboard copy failed: {e}", file=sys.stderr)
            return 2
        if not quiet:
            print(f"copied {len(text)} chars to clipboard", file=sys.stderr)
        return 0
    path = Path(target)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"cannot write {path}: {e}", file=sys.stderr)
        return 2
    if not quiet:
        print(f"written: {path}", file=sys.stderr)
    return 0


def _cmd_flush(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    fmt = args.format if args.format is not None else cfg.default_format
    target = args.to if args.to is not None else cfg.default_target
    quiet = args.quiet
    dry_run = args.dry_run
    per_sku = args.per_sku

    with _open_store(args.db) as s:
        if args.only is not None:
            wanted = [x.strip() for x in args.only.split(",") if x.strip()]
            items = list(s.iter_all(include_raw=False, skus=wanted))
        else:
            items = list(s.iter_all(include_raw=False))
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    if args.since:
        since = str(args.since)[:10]
        items = [it for it in items if (it.get("updated_at") or "")[:10] >= since]

    if not items:
        if not quiet:
            print("flush: no items matched", file=sys.stderr)
        return 0

    if per_sku:
        if target in ("stdout", "clipboard"):
            print("flush --per-sku requires --to DIR", file=sys.stderr)
            return 2
        path = Path(target)
        if path.exists() and not path.is_dir():
            print(f"--per-sku requires a directory, got file: {path}", file=sys.stderr)
            return 2
        if not dry_run:
            path.mkdir(parents=True, exist_ok=True)
        for it in items:
            filename = per_sku_file_name(it)
            content = render_item_markdown(it)
            target_file = path / filename
            if dry_run:
                print(
                    f"[dry-run] {it['sku']} -> {target_file} "
                    f"({len(content)} chars)",
                    file=sys.stderr,
                )
            else:
                target_file.write_text(content, encoding="utf-8")
                if not quiet:
                    print(f"written: {target_file}", file=sys.stderr)
        return 0

    try:
        text = render_collection(items, fmt)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if dry_run:
        print(
            f"[dry-run] would write {len(text)} chars to {target}",
            file=sys.stderr,
        )
        return 0

    return _write_output(text, target, quiet=quiet)


def _cmd_clear(args: argparse.Namespace) -> int:
    if not args.yes:
        print(
            "refusing to clear collection without --yes",
            file=sys.stderr,
        )
        return 1
    with _open_store(args.db) as s:
        n = s.clear()
    print(f"cleared: {n} items", file=sys.stderr)
    return 0


def _cmd_rm(args: argparse.Namespace) -> int:
    """Удалить один или несколько SKU. Требует --yes.

    SKU можно передавать позиционно (`rm 111 222`) и/или через запятую
    (`rm 111,222`). Итог: все найденные удалены, отсутствующие —
    перечислены в stderr. Код: 0 если все OK, 1 если что-то не найдено.
    """
    if not args.yes:
        print("refusing to delete without --yes", file=sys.stderr)
        return 1

    wanted: list[str] = []
    for raw in args.sku:
        for part in str(raw).split(","):
            s = part.strip()
            if s and s not in wanted:
                wanted.append(s)

    if not wanted:
        print("no SKU given", file=sys.stderr)
        return 2

    deleted: list[str] = []
    not_found: list[str] = []
    with _open_store(args.db) as s:
        for sku in wanted:
            if s.delete(sku):
                deleted.append(sku)
            else:
                not_found.append(sku)

    if deleted:
        print(f"deleted: {', '.join(deleted)}", file=sys.stderr)
    if not_found:
        print(f"not found: {', '.join(not_found)}", file=sys.stderr)
        return 1
    return 0


def _cmd_once(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    include_images = (
        bool(args.include_images)
        if args.include_images is not None
        else cfg.include_image_urls
    )
    with _open_store(args.db) as s:
        try:
            res = poll_once(
                s,
                include_images=include_images,
                store_raw_html=cfg.store_raw_html,
            )
        except Exception as e:
            sys.stderr.write(f"poll failed: {e}\n")
            return 2
        if cfg.notify_desktop and res.get("status") in ("added", "updated"):
            from ozon_parser.notifier import notify_ingest
            notify_ingest(
                res.get("info") or {},
                is_new=(res.get("status") == "added"),
                store_count=s.count(),
            )
    st = res.get("status")
    if st in ("added", "updated"):
        info = res.get("info") or {}
        raw_size = int(res.get("raw_html_size") or 0)
        raw_note = f", raw {raw_size} B" if raw_size else ", no raw_html"
        sys.stderr.write(
            f"[{st}] {res.get('sku')} — "
            f"{(info.get('title') or '')[:80]} "
            f"(quality {res.get('quality')}/9, {res.get('copy_type')}{raw_note})\n"
        )
        return 0
    if st == "no_sku":
        sys.stderr.write("распознан Ozon PDP, но SKU не определён — пропущено\n")
        return 2
    if st == "deduped":
        sys.stderr.write("буфер не изменился — пропущено\n")
        return 0
    if st == "error":
        sys.stderr.write(f"parse error: {res.get('error') or '?'}\n")
        return 2
    sys.stderr.write(f"буфер не Ozon PDP — пропущено ({st})\n")
    return 1


def _cmd_read(_args: argparse.Namespace) -> int:
    """Диагностика клипборда: что видит парсер.

    Полезно, когда `once` говорит `skipped`: показывает длины, результаты
    детекта, превью и наличие маркеров в text/html.
    """
    from ozon_parser.domain.detection import (
        STRONG_MARKERS,
        WEAK_MARKERS,
        detect_copy_type,
        is_usable_ozon_copy,
        looks_like_ozon_product_page,
    )

    backend = get_backend()
    text = backend.read_text()
    html = backend.read_html()

    print(f"backend: {backend.name}")
    print(f"text.len: {len(text)}")
    print(f"html.len: {len(html) if html else 0}")
    print()

    if text:
        print(f"text.looks_like_ozon_pdp: {looks_like_ozon_product_page(text)}")
        print(f"text.detect_copy_type:   {detect_copy_type(text)}")
        print(f"text.is_usable:          {is_usable_ozon_copy(text)}")
    else:
        print("text: (empty)")
    print()

    if html:
        print(f"html.looks_like_ozon_pdp: {looks_like_ozon_product_page(html)}")
        print(f"html.detect_copy_type:   {detect_copy_type(html)}")
        print(f"html.is_usable:          {is_usable_ozon_copy(html)}")
    else:
        print("html: (empty)")
    print()

    # CF_HTML от Chrome (Ctrl+U-копия) — это <table><pre> с экранированным
    # текстом, не сырой документ. html.detect_copy_type=unknown — норма.
    if html and ("<!--StartFragment-->" in html or "<table" in html[:2000]):
        print(
            "html.note: CF_HTML fragment (StartFragment/table) — "
            "not raw document; parser uses plain text"
        )
        print()

    print("--- markers ---")
    marker_list = (
        '<html', '<!doctype', '<div', '<script', 'ozon.ru',
    ) + STRONG_MARKERS + WEAK_MARKERS
    text_low = text.lower()
    html_low = (html or "").lower()
    for marker in marker_list:
        m = marker.lower()
        print(
            f"  {marker!r:42}  "
            f"text={m in text_low}  html={m in html_low}"
        )
    print()

    if text:
        preview = text[:300].replace("\n", "\\n")
        print(f"text[0:300]: {preview}")
    if html:
        preview = html[:300].replace("\n", "\\n")
        print(f"html[0:300]: {preview}")

    return 0


def _setup_signal_handlers(stop_event: threading.Event) -> "Callable[[], None]":
    def _handler(signum: int, _frame: FrameType | None) -> None:
        sys.stderr.write(f"signal {signum} — stopping...\n")
        stop_event.set()

    old_int = signal.signal(signal.SIGINT, _handler)
    old_term = None
    try:
        old_term = signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        pass

    def _restore():
        try:
            signal.signal(signal.SIGINT, old_int)
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)
        except (ValueError, OSError):
            pass
    return _restore


def _cmd_daemon(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    interval = (
        args.interval
        if args.interval is not None
        else cfg.poll_interval_ms / 1000.0
    )
    include_images = (
        bool(args.include_images)
        if args.include_images is not None
        else cfg.include_image_urls
    )
    store_raw_html = cfg.store_raw_html

    setup_logging(
        getattr(args, "log_file", None),
        quiet=bool(getattr(args, "quiet", False)),
    )

    store = _open_store(args.db)
    stop = threading.Event()

    on_ingest = None
    if cfg.notify_desktop:
        from ozon_parser.notifier import notify_ingest

        def _on_ingest(res: dict) -> None:
            notify_ingest(
                res.get("info") or {},
                is_new=(res.get("status") == "added"),
                store_count=store.count(),
            )
        on_ingest = _on_ingest
    restore = _setup_signal_handlers(stop)

    try:
        run_forever(
            store,
            interval=interval,
            stop_event=stop,
            include_images=include_images,
            store_raw_html=store_raw_html,
            on_ingest=on_ingest,
        )
    finally:
        restore()
        store.close()
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    port = args.port if args.port is not None else cfg.http_port
    store_raw_html = cfg.store_raw_html

    setup_logging(
        getattr(args, "log_file", None),
        quiet=bool(getattr(args, "quiet", False)),
    )

    store = _open_store(args.db)
    stop = threading.Event()
    restore = _setup_signal_handlers(stop)

    try:
        run_server(
            store,
            host=args.host,
            port=port,
            stop_event=stop,
            store_raw_html=store_raw_html,
            config=cfg.to_dict(),
        )
    finally:
        restore()
        store.close()
    return 0


def _cmd_bookmarklet(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    port = args.port if args.port is not None else cfg.http_port
    host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    base = f"http://{host}:{port}"
    js = generate_bookmarklet(base)
    if args.to == "stdout":
        sys.stdout.write(js + "\n")
        return 0
    if args.to == "clipboard":
        try:
            import pyperclip
            pyperclip.copy(js)
        except Exception as e:
            print(f"clipboard: {e}", file=sys.stderr)
            return 2
        print(f"bookmarklet copied to clipboard ({len(js)} chars)", file=sys.stderr)
        return 0
    path = Path(args.to)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(js + "\n", encoding="utf-8")
    print(f"bookmarklet written: {path}", file=sys.stderr)
    return 0


def _cmd_service_install(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    port = args.port if args.port is not None else cfg.http_port
    interval = (
        args.interval
        if args.interval is not None
        else cfg.poll_interval_ms / 1000.0
    )

    if getattr(args, "no_log_file", False):
        log_file: Path | None = None
    elif getattr(args, "log_file", None):
        log_file = Path(args.log_file)
    else:
        log_file = default_log_path(args.mode)

    res = service_install(
        args.mode, port=port, interval=interval, log_file=log_file
    )
    if res.ok:
        print(
            f"installed: {res.mode} via {res.kind} — {res.location}",
            file=sys.stderr,
        )
        print(f"  {res.message}", file=sys.stderr)
        if log_file is not None:
            print(f"  log: {log_file}", file=sys.stderr)
        return 0
    print(f"install failed: {res.message}", file=sys.stderr)
    return 2


def _cmd_service_uninstall(args: argparse.Namespace) -> int:
    res = service_uninstall(args.mode)
    if not res.ok:
        print(f"uninstall failed: {res.message}", file=sys.stderr)
        return 2
    if res.missing:
        print(f"not installed: {res.message}", file=sys.stderr)
        return 0
    print(f"uninstalled: {res.message}", file=sys.stderr)
    return 0


def _cmd_service_status(args: argparse.Namespace) -> int:
    mode = args.mode
    if mode is None:
        # без --mode — отчёт по обоим
        for m in ("serve", "daemon"):
            s = service_status(m)
            marker = "✓" if s.running else ("○" if s.installed else "✗")
            print(f"{marker} {m:8} {s.message}")
        return 0
    s = service_status(mode)
    marker = "✓" if s.running else ("○" if s.installed else "✗")
    print(f"{marker} {mode:8} {s.message}")
    return 0


def _cmd_native_register(args: argparse.Namespace) -> int:
    """Регистрирует Native Messaging host во всех Chromium-браузерах.

    Extension ID: --extension-id или DEFAULT_EXTENSION_ID (из
    build/gen_extension_key.py). Без ключа в manifest.json ID случайный
    на каждой машине, тогда --extension-id обязателен.
    """
    from ozon_parser.native._extension_id import EXTENSION_ID as DEFAULT_EXTENSION_ID

    ext_id = args.extension_id or DEFAULT_EXTENSION_ID
    if not ext_id:
        print(
            "--extension-id is required: run `python build/gen_extension_key.py` "
            "to fix extension ID, or pass ID from chrome://extensions",
            file=sys.stderr,
        )
        return 2
    host = default_host_path()
    if not host.exists():
        print(f"host not found: {host}", file=sys.stderr)
        return 2
    res = native_register(host, ext_id)
    if res.ok:
        print(f"registered: {', '.join(res.registered)}", file=sys.stderr)
        for p in res.manifest_paths:
            print(f"  {p}", file=sys.stderr)
        return 0
    print(f"register failed: {res.message}", file=sys.stderr)
    return 2


def _cmd_native_unregister(args: argparse.Namespace) -> int:
    res = native_unregister()
    if res.ok:
        print(f"unregistered: {', '.join(res.removed) or 'none'}", file=sys.stderr)
        return 0
    print(f"unregister failed: {res.message}", file=sys.stderr)
    return 2


def _cmd_config_show(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else default_config_path()
    cfg = load_config(path)
    print(f"# config: {path}")
    print(f"# exists: {path.exists()}")
    print()
    print("[input]")
    print(f"http_port = {cfg.http_port}")
    print(f"poll_interval_ms = {cfg.poll_interval_ms}")
    print()
    print("[parse]")
    print(f"include_image_urls = {'true' if cfg.include_image_urls else 'false'}")
    print(f"store_raw_html = {'true' if cfg.store_raw_html else 'false'}")
    print()
    print("[output]")
    print(f'default_format = "{cfg.default_format}"')
    print(f'default_target = "{cfg.default_target}"')
    print()
    print("[notify]")
    print(f"desktop = {'true' if cfg.notify_desktop else 'false'}")
    return 0


def _cmd_reparse(args: argparse.Namespace) -> int:
    """Перечитать raw_html из БД, перепарсить, перезаписать.

    Вся оркестрация — в ReparseUseCase (application layer). CLI только
    форматирует вывод и возвращает exit code.

    Товары без raw_html пропускаются с сообщением.
    """
    cfg = load_config(args.config)
    only: set[str] | None = None
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}

    with _open_store(args.db) as s:
        use_case = ReparseUseCase(
            parser=OzonHtmlParser(),
            repository=s,
            include_images=cfg.include_image_urls,
        )
        result = use_case.execute(
            only=only,
            since=args.since,
            on_progress=lambda sku, msg: sys.stderr.write(f"{sku}: {msg}\n"),
        )

    sys.stderr.write(
        f"reparse done: {result.reparsed} reparsed, "
        f"{result.no_raw} without raw_html, "
        f"{result.failed} failed\n"
    )
    return 0 if result.failed == 0 else 1


def _cmd_export_fixture(args: argparse.Namespace) -> int:
    """Выгрузить raw_html из БД в файл для регрессионных тестов.

    Режимы (взаимоисключающие):
      - `<SKU>` — один товар, файл `{copy_type}.html` (source.html / rendered.html);
      - `--latest` — самый свежий товар с raw_html;
      - `--all` — все товары с raw_html, файлы `{sku}-{copy_type}.html`.
    """
    has_sku = bool(args.sku)
    has_latest = bool(getattr(args, "latest", False))
    has_all = bool(getattr(args, "all", False))
    if sum([has_sku, has_latest, has_all]) != 1:
        sys.stderr.write(
            "export-fixture: specify SKU, --latest, or --all (not multiple)\n"
        )
        return 2

    target_dir = Path(args.to)

    if has_all:
        target_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        with _open_store(args.db) as s:
            for it in s.iter_with_raw():
                sku = it["sku"]
                copy_type = str(it.get("copy_type") or "unknown")
                target_file = target_dir / f"{sku}-{copy_type}.html"
                if target_file.exists() and not args.force:
                    sys.stderr.write(
                        f"{target_file} exists — use --force to overwrite\n"
                    )
                    continue
                try:
                    html_bytes = gzip.decompress(it["raw_html"])
                except Exception as e:
                    sys.stderr.write(f"{sku}: gunzip failed: {e}\n")
                    continue
                target_file.write_bytes(html_bytes)
                sys.stderr.write(
                    f"exported: {target_file} ({len(html_bytes)} bytes)\n"
                )
                count += 1
        return 0 if count > 0 else 1

    with _open_store(args.db) as s:
        if has_latest:
            row = next(iter(s.iter_with_raw()), None)
            if row is None:
                sys.stderr.write(
                    "no items with raw_html — re-copy with store_raw_html=true\n"
                )
                return 1
        else:
            row = s.get(args.sku)
    if row is None:
        sys.stderr.write(f"SKU not found: {args.sku}\n")
        return 1
    raw = row.get("raw_html")
    if not raw:
        sys.stderr.write(
            f"{row['sku']}: no raw_html stored — re-copy with store_raw_html=true\n"
        )
        return 1

    copy_type = str(row.get("copy_type") or "unknown")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{copy_type}.html"
    if target.exists() and not args.force:
        sys.stderr.write(f"{target} exists — use --force to overwrite\n")
        return 2
    try:
        html_bytes = gzip.decompress(raw)
    except Exception as e:
        sys.stderr.write(f"gunzip failed: {e}\n")
        return 2
    target.write_bytes(html_bytes)
    sys.stderr.write(
        f"exported: {target} ({len(html_bytes)} bytes, copy_type={copy_type})\n"
    )
    return 0


def _cmd_config_edit(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else default_config_path()
    try:
        write_template_if_missing(path)
    except OSError as e:
        print(f"cannot create config: {e}", file=sys.stderr)
        return 2
    try:
        return int(open_in_editor(path))
    except Exception as e:
        print(f"editor failed: {e}", file=sys.stderr)
        print(f"file: {path}", file=sys.stderr)
        return 2


def _host_or_ip(s: str) -> str:
    """argparse-валидатор для --host. Только hostname/IP-символы."""
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", s):
        raise argparse.ArgumentTypeError(f"invalid host: {s!r}")
    return s


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ozon-parser",
        description="Кроссплатформенный парсер Ozon PDP в Markdown-коллекцию.",
    )
    ap.add_argument(
        "--version", action="version", version=f"ozon-parser {__version__}"
    )
    ap.add_argument(
        "--db",
        default=None,
        help="путь к SQLite (по умолчанию: user-data/ozon-parser/collection.db)",
    )
    ap.add_argument(
        "--config",
        default=None,
        help=(
            "путь к TOML-конфигу "
            "(по умолчанию: user-config/ozon-parser/config.toml)"
        ),
    )

    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="показать коллекцию")
    p_list.add_argument("--limit", type=int, default=0, help="макс. строк (0 = все)")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="показать один товар")
    p_show.add_argument("sku")
    p_show.set_defaults(func=_cmd_show)

    p_count = sub.add_parser("count", help="число записей")
    p_count.set_defaults(func=_cmd_count)

    p_flush = sub.add_parser("flush", help="выгрузить коллекцию")
    p_flush.add_argument(
        "-f", "--format",
        choices=["markdown", "jsonl", "csv"],
        default=None,
        help="формат вывода (по умолчанию: из конфига или markdown)",
    )
    p_flush.add_argument(
        "-t", "--to",
        default=None,
        help=(
            "stdout | clipboard | путь к файлу "
            "(по умолчанию: из конфига или stdout)"
        ),
    )
    p_flush.add_argument(
        "--only", default=None,
        help="только эти SKU через запятую",
    )
    p_flush.add_argument("--limit", type=int, default=0)
    p_flush.add_argument(
        "--since", default=None,
        help="только записи с updated_at >= DATE (YYYY-MM-DD)",
    )
    p_flush.add_argument(
        "--per-sku", action="store_true",
        help="каждый товар в отдельный {sku}-{slug}.md с YAML frontmatter (--to = DIR)",
    )
    p_flush.add_argument(
        "--dry-run", action="store_true",
        help="показать, что было бы записано, но не писать",
    )
    p_flush.add_argument(
        "--quiet", action="store_true",
        help="не печатать информационные сообщения",
    )
    p_flush.set_defaults(func=_cmd_flush)

    p_clear = sub.add_parser("clear", help="очистить коллекцию")
    p_clear.add_argument("--yes", "-y", action="store_true", help="подтвердить")
    p_clear.set_defaults(func=_cmd_clear)

    p_rm = sub.add_parser("rm", help="удалить один или несколько SKU")
    p_rm.add_argument(
        "sku", nargs="+",
        help="SKU (позиционно и/или через запятую: `rm 111 222` или `rm 111,222`)",
    )
    p_rm.add_argument(
        "--yes", "-y", action="store_true",
        help="подтвердить удаление (обязательно)",
    )
    p_rm.set_defaults(func=_cmd_rm)

    p_once = sub.add_parser("once", help="один проход по буферу и выход")
    p_once.add_argument(
        "--include-images",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="включить/выключить URL картинок (переопределяет конфиг)",
    )
    p_once.set_defaults(func=_cmd_once)

    p_read = sub.add_parser(
        "read", help="диагностика клипборда (что видит парсер)"
    )
    p_read.set_defaults(func=_cmd_read)

    p_daemon = sub.add_parser("daemon", help="фоновый цикл по буферу обмена")
    p_daemon.add_argument(
        "--interval",
        type=float,
        default=None,
        help="период опроса в секундах (по умолчанию: из конфига)",
    )
    p_daemon.add_argument(
        "--include-images",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="включить/выключить URL картинок (переопределяет конфиг)",
    )
    p_daemon.add_argument(
        "--log-file", default=None,
        help="писать DEBUG-лог в файл (по умолчанию только stderr)",
    )
    p_daemon.add_argument(
        "--quiet", action="store_true",
        help="не печатать INFO-логи",
    )
    p_daemon.set_defaults(func=_cmd_daemon)

    p_serve = sub.add_parser("serve", help="HTTP-приём из bookmarklet / расширения")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="порт (по умолчанию: из конфига)",
    )
    p_serve.add_argument(
        "--log-file", default=None,
        help="писать DEBUG-лог в файл (по умолчанию только stderr)",
    )
    p_serve.add_argument(
        "--quiet", action="store_true",
        help="не печатать INFO-логи в stderr",
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_bm = sub.add_parser(
        "bookmarklet",
        help="напечатать JS-букмарклет (или --to clipboard)",
    )
    p_bm.add_argument(
        "--host", default="127.0.0.1", type=_host_or_ip,
        help="hostname или IP сервера (regex [A-Za-z0-9.-]+)",
    )
    p_bm.add_argument(
        "--port",
        type=int,
        default=None,
        help="порт сервера (по умолчанию: из конфига)",
    )
    p_bm.add_argument(
        "--to",
        default="stdout",
        help="stdout | clipboard | путь к файлу",
    )
    p_bm.set_defaults(func=_cmd_bookmarklet)

    p_reparse = sub.add_parser(
        "reparse", help="перепарсить сохранённый raw_html (без повторного копирования)"
    )
    p_reparse.add_argument(
        "--only", default=None, help="только эти SKU через запятую"
    )
    p_reparse.add_argument(
        "--since", default=None, help="только записи с updated_at >= DATE (ISO)"
    )
    p_reparse.set_defaults(func=_cmd_reparse)

    p_exp = sub.add_parser(
        "export-fixture",
        help="выгрузить raw_html SKU в DIR/{copy_type}.html",
    )
    p_exp.add_argument("sku", nargs="?", default=None)
    p_exp.add_argument(
        "--latest", action="store_true",
        help="выгрузить самый свежий товар с raw_html",
    )
    p_exp.add_argument(
        "--all", action="store_true",
        help="выгрузить все товары с raw_html (файлы {sku}-{copy_type}.html)",
    )
    p_exp.add_argument(
        "--to", required=True, help="папка для выгрузки (например, tests/fixtures)"
    )
    p_exp.add_argument(
        "--force", "-f", action="store_true", help="перезаписать существующий файл"
    )
    p_exp.set_defaults(func=_cmd_export_fixture)

    p_svc = sub.add_parser(
        "service", help="автозапуск daemon/serve через менеджер сервисов ОС"
    )
    svc_sub = p_svc.add_subparsers(dest="svc_cmd", required=True)

    p_svc_install = svc_sub.add_parser(
        "install", help="установить и запустить сервис"
    )
    p_svc_install.add_argument(
        "--mode", choices=["serve", "daemon"], default="serve"
    )
    p_svc_install.add_argument(
        "--port", type=int, default=None,
        help="порт (только для --mode serve; по умолчанию из конфига)",
    )
    p_svc_install.add_argument(
        "--interval", type=float, default=None,
        help="интервал опроса (только для --mode daemon; по умолчанию из конфига)",
    )
    p_svc_install.add_argument(
        "--log-file", default=None,
        help="путь к лог-файлу (по умолчанию — платформенный)",
    )
    p_svc_install.add_argument(
        "--no-log-file", action="store_true",
        help="не писать в лог-файл (только stderr)",
    )
    p_svc_install.set_defaults(func=_cmd_service_install)

    p_svc_uninstall = svc_sub.add_parser(
        "uninstall", help="удалить сервис"
    )
    p_svc_uninstall.add_argument(
        "--mode", choices=["serve", "daemon"], default="serve"
    )
    p_svc_uninstall.set_defaults(func=_cmd_service_uninstall)

    p_svc_status = svc_sub.add_parser(
        "status", help="статус сервисов (serve/daemon)"
    )
    p_svc_status.add_argument(
        "--mode", choices=["serve", "daemon"], default=None,
        help="если не указано — отчёт по обоим",
    )
    p_svc_status.set_defaults(func=_cmd_service_status)

    p_dev = sub.add_parser(
        "dev", help="dev-режим: быстрая итерация без переустановки"
    )
    dev_sub = p_dev.add_subparsers(dest="dev_cmd", required=True)

    p_dev_reg = dev_sub.add_parser(
        "register",
        help="зарегистрировать Native host как wrapper (не .exe)",
    )
    p_dev_reg.set_defaults(func=cmd_dev_register)

    p_dev_serve = dev_sub.add_parser(
        "serve",
        help="HTTP-сервер с автоперезапуском при изменении .py",
    )
    p_dev_serve.add_argument(
        "--port", type=int, default=None,
        help="порт (по умолчанию из конфига)",
    )
    p_dev_serve.add_argument(
        "--log-file", default=None,
        help="путь к лог-файлу дочернего процесса",
    )
    p_dev_serve.set_defaults(func=cmd_dev_serve)

    p_native = sub.add_parser(
        "native", help="Native Messaging host: регистрация в браузерах"
    )
    native_sub = p_native.add_subparsers(dest="native_cmd", required=True)
    p_native_reg = native_sub.add_parser(
        "register", help="зарегистрировать host во всех Chromium-браузерах"
    )
    p_native_reg.add_argument(
        "--extension-id", default=None,
        help=(
            "ID расширения (по умолчанию — из ozon_parser/native/_extension_id.py, "
            "сгенерирован build/gen_extension_key.py)"
        ),
    )
    p_native_reg.set_defaults(func=_cmd_native_register)
    p_native_unreg = native_sub.add_parser(
        "unregister", help="удалить регистрацию"
    )
    p_native_unreg.set_defaults(func=_cmd_native_unregister)

    p_cfg = sub.add_parser(
        "config", help="показать или отредактировать TOML-конфиг"
    )
    cfg_sub = p_cfg.add_subparsers(dest="cfg_cmd", required=True)
    p_cfg_show = cfg_sub.add_parser("show", help="показать эффективные значения")
    p_cfg_show.set_defaults(func=_cmd_config_show)
    p_cfg_edit = cfg_sub.add_parser(
        "edit", help="открыть в редакторе (создать шаблон при отсутствии)"
    )
    p_cfg_edit.set_defaults(func=_cmd_config_edit)

    return ap


def main(argv: Sequence[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = _build_parser()
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
