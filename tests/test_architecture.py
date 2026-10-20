"""Архитектурные тесты: правило зависимостей Clean Architecture.

Domain → stdlib only, с явными запретами (sqlite3, bs4, network).
Application → domain + stdlib. Infrastructure → domain + application
+ существующие top-level модули (store/parser/notifier) на время миграции.
Проверяется через ast: ноль внешних зависимостей. Без archon/import-linter —
на нашем масштабе 50 строк AST-обхода достаточно.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent / "ozon_parser"

# Даже если модуль в stdlib, domain не должен его знать:
# sqlite3 — знание БД, urllib/http/subprocess — сеть и процессы,
# tomllib — знание формата конфига (не дело domain).
FORBIDDEN_IN_DOMAIN = {
    "sqlite3", "bs4", "pyperclip", "requests", "urllib", "http",
    "tomli", "tomllib", "subprocess", "socket",
}


def _iter_imports(path: Path):
    """(lineno, module) для каждого import в файле (рекурсивно, ast.walk)."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.lineno, node.module


def test_domain_package_exists() -> None:
    assert (ROOT / "domain").is_dir(), (
        "Stage 2 не выполнен: ozon_parser/domain/ отсутствует"
    )


def test_domain_imports_only_stdlib() -> None:
    """Domain → stdlib only + явные запреты (sqlite3, bs4, network, subprocess)."""
    stdlib = set(sys.stdlib_module_names)
    violations: list[str] = []

    for f in sorted((ROOT / "domain").rglob("*.py")):
        for lineno, mod in _iter_imports(f):
            top = mod.split(".")[0]
            if top in FORBIDDEN_IN_DOMAIN:
                violations.append(f"{f.name}:{lineno}: forbidden {top}")
            elif top == "ozon_parser":
                parts = mod.split(".")
                if len(parts) >= 2 and parts[1] != "domain":
                    violations.append(f"{f.name}:{lineno}: {mod} (only domain allowed)")
            elif top not in stdlib:
                violations.append(f"{f.name}:{lineno}: external {top}")

    assert not violations, "\n".join(violations)


def test_reexports_preserve_identity() -> None:
    """Старые пути — re-export shims: тот же объект, не копия."""
    from ozon_parser import detection, quality
    from ozon_parser.domain import detection as d_dom
    from ozon_parser.domain import quality as q_dom

    assert detection.detect_copy_type is d_dom.detect_copy_type
    assert detection.is_usable_ozon_copy is d_dom.is_usable_ozon_copy
    assert detection.looks_like_ozon_product_page is d_dom.looks_like_ozon_product_page
    assert quality.quality_score is q_dom.quality_score



def test_application_does_not_import_infrastructure() -> None:
    """Application → только domain + stdlib.

    Infrastructure и presentation запрещены. `parser`, `store`, `notifier` —
    это infra; `cli`, `daemon`, `server`, `render` — presentation.
    Старые пути `detection`/`quality` тоже запрещены: use case должен звать
    `ozon_parser.domain.detection`, а не shim.
    """
    assert (ROOT / "application").is_dir(), (
        "Stage 3 не выполнен: ozon_parser/application/ отсутствует"
    )
    stdlib = set(sys.stdlib_module_names)
    violations: list[str] = []

    for f in sorted((ROOT / "application").rglob("*.py")):
        for lineno, mod in _iter_imports(f):
            top = mod.split(".")[0]
            if top == "ozon_parser":
                parts = mod.split(".")
                if len(parts) < 2 or parts[1] != "domain":
                    violations.append(
                        f"{f.name}:{lineno}: {mod} (application may import only domain)"
                    )
            elif top not in stdlib:
                violations.append(f"{f.name}:{lineno}: external {top}")

    assert not violations, "\n".join(violations)


def test_infrastructure_package_exists() -> None:
    """Stage 4: adapter-классы в infrastructure/.

    SqliteProductRepository, OzonHtmlParser, DesktopNotifier — тонкие
    обёртки над store/parser/notifier. Пакет может перестраиваться на
    Stage 5, но должен существовать.
    """
    assert (ROOT / "infrastructure").is_dir(), (
        "Stage 4 не выполнен: ozon_parser/infrastructure/ отсутствует"
    )


def test_presentation_does_not_parse_directly() -> None:
    """Stage 5: presentation не дублирует parse+quality — это работа use case.

    Регрессия: до Stage 5 daemon.poll_once, server._handle_ingest и
    cli._cmd_reparse каждый импортировали parse_product и quality_score
    и содержали по копии ingest-логики. После Stage 5 эти импорты
    запрещены в presentation — единственный вход через use case.
    """
    for fname in ("cli.py", "daemon.py", "server.py"):
        f = ROOT / fname
        src = f.read_text(encoding="utf-8")
        assert "from ozon_parser.parser import" not in src, (
            f"{fname} импортирует parser напрямую — дублирование ingest-логики"
        )
        assert "from ozon_parser.quality import" not in src, (
            f"{fname} импортирует quality напрямую — дублирование ingest-логики"
        )


def test_use_case_wired_into_presentation() -> None:
    """Stage 5: use case'ы реально импортируются в presentation.

    Проверяем по тексту: если кто-то откатит Stage 5 (вернёт прямые вызовы),
    этот тест поймает отсутствие импорта use case.
    """
    daemon_src = (ROOT / "daemon.py").read_text(encoding="utf-8")
    assert "IngestHtmlUseCase" in daemon_src

    server_src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "IngestHtmlUseCase" in server_src

    cli_src = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert "ReparseUseCase" in cli_src
