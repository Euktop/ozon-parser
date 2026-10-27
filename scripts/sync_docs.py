"""Single source of truth для версии проекта.

Источник: ``pyproject.toml [project].version``. Скрипт разносит версию
по всем местам, где она дублируется:

- ``ozon_parser/__init__.py``      → ``__version__ = "..."``
- ``ozon_parser/server.py``        → ``server_version = "ozon-parser/..."``
- ``extension/manifest.json``      → ``"version": "..."``
- любой ``*.md`` с маркером         → ``<!-- AUTO:VERSION -->...<!-- /AUTO -->``
- ``SECURITY.md`` с маркером         → ``<!-- AUTO:SUPPORT_TABLE -->...<!-- /AUTO -->``

Команды:
    python scripts/sync_docs.py              # синхронизировать
    python scripts/sync_docs.py --check      # exit 1 если рассинхрон (для CI)
    python scripts/sync_docs.py --bump 0.5.0 # поднять версию + changelog stub

Идемпотентно: повторный прогон без изменений ничего не пишет.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"
MANIFEST = ROOT / "extension" / "manifest.json"
INIT = ROOT / "ozon_parser" / "__init__.py"
SERVER = ROOT / "ozon_parser" / "server.py"
SECURITY = ROOT / "SECURITY.md"

MARK_VERSION = "AUTO:VERSION"
MARK_SUPPORT = "AUTO:SUPPORT_TABLE"


def read_version() -> str:
    m = re.search(
        r"^version\s*=\s*['\"]([^'\"]+)['\"]",
        PYPROJECT.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not m:
        raise SystemExit(f"cannot find version in {PYPROJECT}")
    return m.group(1)


def write_version(new: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(
        r"^version\s*=\s*['\"][^'\"]+['\"]",
        f"version = '{new}'",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    PYPROJECT.write_text(text, encoding="utf-8")


def _replace_pattern(path: Path, pattern: str, repl: str) -> bool:
    """Первую замену — если есть. True если файл изменился."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if n > 0 and new_text != text:
        path.write_text(new_text, encoding="utf-8")
        return True
    return False


def _replace_marker(path: Path, marker: str, body: str) -> bool:
    """Заменить содержимое <!-- AUTO:X -->...<!-- /AUTO --> на body."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    pat = re.compile(
        rf"<!--\s*{re.escape(marker)}\s*-->.*?<!--\s*/AUTO\s*-->",
        re.DOTALL,
    )
    new_text, n = pat.subn(
        f"<!-- {marker} -->{body}<!-- /AUTO -->", text
    )
    if n > 0 and new_text != text:
        path.write_text(new_text, encoding="utf-8")
        return True
    return False


def support_table(version: str) -> str:
    """Таблица 'Версия / Поддержка' для SECURITY.md."""
    parts = version.split(".")
    major, minor = parts[0], parts[1] if len(parts) > 1 else "0"
    return (
        "\n"
        "| Версия | Поддержка |\n"
        "|--------|-----------|\n"
        f"| {major}.{minor}.x  | ✅        |\n"
        f"| < {major}.{minor}  | ❌        |\n"
    )


def sync_all(version: str) -> list[tuple[Path, str]]:
    changes: list[tuple[Path, str]] = []

    if _replace_pattern(INIT, r'__version__ = "[^"]+"', f'__version__ = "{version}"'):
        changes.append((INIT, "pattern"))
    if _replace_pattern(
        SERVER,
        r'server_version = "ozon-parser/[^"]+"',
        f'server_version = "ozon-parser/{version}"',
    ):
        changes.append((SERVER, "pattern"))

    # Extension manifest — через JSON, чтобы не сломать форматирование
    if MANIFEST.exists():
        try:
            data = json.loads(MANIFEST.read_text(encoding="utf-8"))
            if data.get("version") != version:
                data["version"] = version
                MANIFEST.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                changes.append((MANIFEST, "json"))
        except json.JSONDecodeError as e:
            print(f"WARN: {MANIFEST}: {e}", file=sys.stderr)

    # Маркеры в .md
    for md in ROOT.rglob("*.md"):
        parts = set(md.parts)
        if parts & {".git", "node_modules", "dist", "build", ".venv"}:
            continue
        text = md.read_text(encoding="utf-8")
        if MARK_VERSION in text:
            if _replace_marker(md, MARK_VERSION, version):
                changes.append((md, "AUTO:VERSION"))
        if MARK_SUPPORT in text:
            if _replace_marker(md, MARK_SUPPORT, support_table(version)):
                changes.append((md, "AUTO:SUPPORT_TABLE"))

    return changes


def check(version: str) -> list[str]:
    """Список проблем. Пусто = всё ок."""
    problems: list[str] = []

    m = re.search(r'__version__ = "([^"]+)"', INIT.read_text(encoding="utf-8"))
    if not m or m.group(1) != version:
        problems.append(f"{INIT.relative_to(ROOT)}: {m.group(1) if m else '?'} != {version}")

    if MANIFEST.exists():
        try:
            mv = json.loads(MANIFEST.read_text(encoding="utf-8")).get("version")
            if mv != version:
                problems.append(f"{MANIFEST.relative_to(ROOT)}: {mv} != {version}")
        except json.JSONDecodeError:
            problems.append(f"{MANIFEST.relative_to(ROOT)}: invalid JSON")

    for md in ROOT.rglob("*.md"):
        if set(md.parts) & {".git", "node_modules", "dist", "build", ".venv"}:
            continue
        text = md.read_text(encoding="utf-8")
        if MARK_VERSION in text:
            m = re.search(
                rf"<!--\s*{re.escape(MARK_VERSION)}\s*-->(.*?)<!--\s*/AUTO\s*-->",
                text,
                re.DOTALL,
            )
            if m and m.group(1).strip() != version:
                problems.append(
                    f"{md.relative_to(ROOT)}: marker={m.group(1).strip()!r} != {version!r}"
                )
    return problems


def bump(new_version: str) -> None:
    """Поднять версию и продвинуть [Unreleased] → [new_version] в CHANGELOG."""
    write_version(new_version)
    if not CHANGELOG.exists():
        return
    text = CHANGELOG.read_text(encoding="utf-8")
    today = date.today().isoformat()

    fresh_unreleased = (
        "## [Unreleased]\n\n"
        "### Добавлено\n\n- \n\n"
        "### Изменено\n\n- \n\n"
        "### Исправлено\n\n- \n\n"
    )

    if "## [Unreleased]" in text:
        text = text.replace(
            "## [Unreleased]",
            f"## [{new_version}] — {today}",
            1,
        )
        m = re.search(rf"## \[{re.escape(new_version)}\]", text)
        if m:
            text = text[: m.start()] + fresh_unreleased + text[m.start():]
    else:
        m = re.match(r"(# Changelog\n\n)", text)
        if m:
            insert = fresh_unreleased + f"## [{new_version}] — {today}\n\n"
            text = m.group(1) + insert + text[m.end():]

    CHANGELOG.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sync version across project from pyproject.toml",
    )
    ap.add_argument("--bump", metavar="X.Y.Z", help="поднять версию + changelog stub")
    ap.add_argument("--check", action="store_true", help="exit 1 если рассинхрон")
    args = ap.parse_args(argv)

    if args.bump:
        bump(args.bump)
        version = args.bump
    else:
        version = read_version()

    if args.check:
        problems = check(version)
        if problems:
            print(f"Version is OUT OF SYNC (expected {version}):", file=sys.stderr)
            for p in problems:
                print(f"  {p}", file=sys.stderr)
            return 1
        print(f"In sync: {version}")
        return 0

    changes = sync_all(version)
    if changes:
        print(f"Synced to {version}:")
        for path, kind in changes:
            print(f"  [{kind}] {path.relative_to(ROOT)}")
    else:
        print(f"Already in sync: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
