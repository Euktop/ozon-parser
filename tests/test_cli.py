"""Тесты CLI. Все используют --db во временной папке."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ozon_parser.cli import main
from ozon_parser.store import Store


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый тест использует пустой конфиг через env var OZON_PARSER_CONFIG.

    Раньше патчили cli_mod.default_config_path, но `load_config(None)` вызывает
    config.default_config_path — патч был неэффективен. Env var читается в
    default_config_path() до любой платформенной логики — работает всегда.
    """
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(tmp_path / "_no_config_.toml"))


def _seed(db: Path, n: int = 2) -> None:
    with Store(db) as s:
        for i in range(1, n + 1):
            s.upsert(
                sku=str(i),
                url=f"https://ozon.ru/p/{i}",
                info={"title": f"Item{i}", "brand": "B", "price": str(i * 100)},
                markdown=f"# Item{i}\n",
                quality=9,
                copy_type="source",
            )


def test_count_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    assert main(["--db", str(db), "count"]) == 0
    assert capsys.readouterr().out.strip() == "0"


def test_count_nonempty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 3)
    assert main(["--db", str(db), "count"]) == 0
    assert capsys.readouterr().out.strip() == "3"


def test_list_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    assert main(["--db", str(db), "list"]) == 0
    assert "empty" in capsys.readouterr().out.lower()


def test_list_nonempty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    assert main(["--db", str(db), "list"]) == 0
    out = capsys.readouterr().out
    assert "SKU" in out
    assert "Item1" in out
    assert "Item2" in out


def test_list_limit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 3)
    assert main(["--db", str(db), "list", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    # одна строка товара + заголовок + разделитель + (collection is empty) не появится
    assert sum(1 for line in out.splitlines() if line.startswith(("1 ", "2 ", "3 "))) == 1


def test_show_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    rc = main(["--db", str(db), "show", "999"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_show_existing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 1)
    assert main(["--db", str(db), "show", "1"]) == 0
    assert capsys.readouterr().out == "# Item1\n"


def test_flush_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    assert main(["--db", str(db), "flush"]) == 0
    assert capsys.readouterr().out == ""


def test_flush_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    assert main(["--db", str(db), "flush", "-f", "markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Ozon collection")
    assert "## 1 — Item1" in out
    assert "## 2 — Item2" in out


def test_flush_jsonl(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    assert main(["--db", str(db), "flush", "-f", "jsonl"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 2
    objs = [json.loads(ln) for ln in lines]
    assert {o["sku"] for o in objs} == {"1", "2"}
    for o in objs:
        assert "markdown" in o
        assert "raw_html" not in o


def test_flush_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 1)
    assert main(["--db", str(db), "flush", "-f", "csv"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("sku,title,brand,price,rating,seller,copy_type,quality,url")
    assert ",Item1," in out


def test_flush_to_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 1)
    out_file = tmp_path / "out.md"
    assert main(["--db", str(db), "flush", "-t", str(out_file)]) == 0
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8").startswith("# Ozon collection")


def test_flush_to_clipboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "c.db"
    _seed(db, 1)

    import pyperclip

    captured: dict[str, str] = {}
    monkeypatch.setattr(pyperclip, "copy", lambda t: captured.setdefault("t", t))

    assert main(["--db", str(db), "flush", "-t", "clipboard"]) == 0
    assert captured["t"].startswith("# Ozon collection")


def test_flush_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 3)
    assert main(["--db", str(db), "flush", "--only", "1,3"]) == 0
    out = capsys.readouterr().out
    assert "## 1 — Item1" in out
    assert "## 3 — Item3" in out
    assert "## 2 — Item2" not in out


def test_clear_without_yes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 1)
    assert main(["--db", str(db), "clear"]) == 1
    assert "--yes" in capsys.readouterr().err
    with Store(db) as s:
        assert s.count() == 1


def test_clear_yes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    assert main(["--db", str(db), "clear", "--yes"]) == 0
    with Store(db) as s:
        assert s.count() == 0


def test_rm_single(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 3)
    assert main(["--db", str(db), "rm", "2", "--yes"]) == 0
    err = capsys.readouterr().err
    assert "deleted: 2" in err
    with Store(db) as s:
        assert s.count() == 2
        assert s.get("2") is None
        assert s.get("1") is not None
        assert s.get("3") is not None


def test_rm_multiple_positional(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 3)
    assert main(["--db", str(db), "rm", "1", "3", "--yes"]) == 0
    with Store(db) as s:
        assert s.count() == 1
        assert s.get("2") is not None


def test_rm_multiple_comma_separated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 3)
    assert main(["--db", str(db), "rm", "1,3", "--yes"]) == 0
    with Store(db) as s:
        assert s.count() == 1
        assert s.get("2") is not None


def test_rm_mixed_positional_and_comma(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 3)
    assert main(["--db", str(db), "rm", "1,2", "3", "--yes"]) == 0
    with Store(db) as s:
        assert s.count() == 0


def test_rm_without_yes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    assert main(["--db", str(db), "rm", "1"]) == 1
    assert "--yes" in capsys.readouterr().err
    with Store(db) as s:
        assert s.count() == 2


def test_rm_not_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    assert main(["--db", str(db), "rm", "999", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "not found: 999" in err
    with Store(db) as s:
        assert s.count() == 2


def test_rm_partial_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    assert main(["--db", str(db), "rm", "1,999", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "deleted: 1" in err
    assert "not found: 999" in err
    with Store(db) as s:
        assert s.count() == 1
        assert s.get("1") is None


def test_rm_deduplicates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    assert main(["--db", str(db), "rm", "1", "1,1", "--yes"]) == 0
    err = capsys.readouterr().err
    # SKU 1 в выводе один раз
    assert err.count("deleted: 1") == 1


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "ozon-parser" in capsys.readouterr().out


def test_help_does_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    # Регрессия: `%` в help-строках argparse валит format % params
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_read_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`read` на пустом клипборде: отчёт без падений."""
    import ozon_parser.cli as cli_mod

    class FakeBackend:
        name = "fake"

        def read_text(self) -> str:
            return ""

        def read_html(self) -> str | None:
            return None

    monkeypatch.setattr(cli_mod, "get_backend", lambda: FakeBackend())
    assert main(["read"]) == 0
    out = capsys.readouterr().out
    assert "backend: fake" in out
    assert "text.len: 0" in out
    assert "html.len: 0" in out


def test_read_reports_detection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`read` показывает маркеры и тип копии."""
    import ozon_parser.cli as cli_mod

    payload = (
        '<html><body><div id="__ozon" data-server-rendered="true">'
        '<div id="section-characteristics"></div></div></body></html>'
    )

    class FakeBackend:
        name = "fake"

        def read_text(self) -> str:
            return payload

        def read_html(self) -> str | None:
            return None

    monkeypatch.setattr(cli_mod, "get_backend", lambda: FakeBackend())
    assert main(["read"]) == 0
    out = capsys.readouterr().out
    assert "text.detect_copy_type:   source" in out
    assert "data-server-rendered" in out


def test_flush_per_sku(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    out_dir = tmp_path / "out"
    assert main(["--db", str(db), "flush", "--per-sku", "-t", str(out_dir)]) == 0
    files = sorted(out_dir.glob("*.md"))
    assert len(files) == 2
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "sku: " in text


def test_flush_per_sku_requires_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 1)
    assert main(["--db", str(db), "flush", "--per-sku"]) == 2
    assert "requires --to DIR" in capsys.readouterr().err


def test_flush_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 1)
    out_file = tmp_path / "out.md"
    assert main(["--db", str(db), "flush", "--dry-run", "-t", str(out_file)]) == 0
    assert not out_file.exists()
    assert "[dry-run]" in capsys.readouterr().err


def test_flush_dry_run_per_sku(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    out_dir = tmp_path / "out"
    assert (
        main(
            ["--db", str(db), "flush", "--per-sku", "--dry-run", "-t", str(out_dir)]
        )
        == 0
    )
    # Директория не создаётся
    assert not out_dir.exists()
    assert "[dry-run]" in capsys.readouterr().err


def test_flush_since_filters(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 2)
    # Фильтр в будущее → пусто
    assert main(["--db", str(db), "flush", "--since", "2999-01-01"]) == 0
    out = capsys.readouterr().out
    assert out == ""
    # Фильтр в прошлое → все
    assert main(["--db", str(db), "flush", "--since", "1970-01-01"]) == 0
    out = capsys.readouterr().out
    assert "Ozon collection" in out


def test_flush_quiet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "c.db"
    _seed(db, 1)
    out_file = tmp_path / "out.md"
    assert main(["--db", str(db), "flush", "-t", str(out_file), "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "written:" not in err


def test_subcommand_help_does_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    for sub in ("list", "show", "flush", "count", "clear"):
        with pytest.raises(SystemExit) as exc:
            main([sub, "--help"])
        assert exc.value.code == 0, f"{sub} --help crashed"
        capsys.readouterr()
