"""Тесты TOML-конфига (загрузка, валидация, шаблон) и подкоманд config show/edit."""

from __future__ import annotations

from pathlib import Path

import pytest

from ozon_parser.cli import main
from ozon_parser.config import (
    Config,
    load_config,
    write_template_if_missing,
)


# --- Config / load_config --------------------------------------------------


def test_defaults() -> None:
    c = Config()
    assert c.http_port == 8765
    assert c.poll_interval_ms == 200
    assert c.include_image_urls is False
    assert c.min_quality_score == 4
    assert c.default_format == "markdown"
    assert c.default_target == "stdout"
    assert c.notify_desktop is True


def test_to_dict() -> None:
    d = Config().to_dict()
    assert d["http_port"] == 8765
    assert d["default_format"] == "markdown"


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    c = load_config(tmp_path / "nope.toml")
    assert c == Config()


def test_load_partial(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text("[input]\nhttp_port = 9999\n", encoding="utf-8")
    c = load_config(f)
    assert c.http_port == 9999
    assert c.poll_interval_ms == 200  # default preserved


def test_load_all_sections(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text(
        '[input]\nhttp_port = 8000\npoll_interval_ms = 500\n\n'
        '[parse]\ninclude_image_urls = true\nmin_quality_score = 6\n\n'
        '[output]\ndefault_format = "jsonl"\ndefault_target = "clipboard"\n\n'
        '[notify]\ndesktop = false\n',
        encoding="utf-8",
    )
    c = load_config(f)
    assert c.http_port == 8000
    assert c.poll_interval_ms == 500
    assert c.include_image_urls is True
    assert c.min_quality_score == 6
    assert c.default_format == "jsonl"
    assert c.default_target == "clipboard"
    assert c.notify_desktop is False


def test_load_wrong_type_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text('[input]\nhttp_port = "not-int"\n', encoding="utf-8")
    c = load_config(f)
    assert c.http_port == 8765  # default


def test_load_bool_is_not_int(tmp_path: Path) -> None:
    """bool — подкласс int; не превращаем true в 1 для min_quality_score."""
    f = tmp_path / "c.toml"
    f.write_text("[parse]\nmin_quality_score = true\n", encoding="utf-8")
    c = load_config(f)
    assert c.min_quality_score == 4  # default, не 1


def test_load_invalid_toml_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text("not { valid toml", encoding="utf-8")
    c = load_config(f)  # не должно падать
    assert c == Config()


def test_load_unknown_keys_ignored(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text(
        "[mystery]\nfoo = 1\n\n[input]\nhttp_port = 7000\n",
        encoding="utf-8",
    )
    c = load_config(f)
    assert c.http_port == 7000


def test_env_var_overrides_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OZON_PARSER_CONFIG переопределяет платформенный путь."""
    from ozon_parser.config import default_config_path

    custom = tmp_path / "custom.toml"
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(custom))
    assert default_config_path() == custom


def test_env_var_drives_load_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config(None) читает OZON_PARSER_CONFIG."""
    custom = tmp_path / "custom.toml"
    custom.write_text("[input]\nhttp_port = 5555\n", encoding="utf-8")
    monkeypatch.setenv("OZON_PARSER_CONFIG", str(custom))
    c = load_config(None)
    assert c.http_port == 5555


def test_write_template_creates_file(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    write_template_if_missing(f)
    assert f.exists()
    text = f.read_text(encoding="utf-8")
    assert "[input]" in text
    assert "http_port = 8765" in text
    assert "[output]" in text


def test_write_template_does_not_overwrite(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text("# custom\n", encoding="utf-8")
    write_template_if_missing(f)
    assert f.read_text(encoding="utf-8") == "# custom\n"


# --- CLI: config show / config edit ----------------------------------------


def test_config_show_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "c.toml"
    assert main(["--config", str(target), "config", "show"]) == 0
    out = capsys.readouterr().out
    assert f"# config: {target}" in out
    assert "# exists: False" in out
    assert "http_port = 8765" in out
    assert 'default_format = "markdown"' in out


def test_config_show_reflects_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "c.toml"
    target.write_text("[input]\nhttp_port = 7777\n", encoding="utf-8")
    assert main(["--config", str(target), "config", "show"]) == 0
    out = capsys.readouterr().out
    assert "# exists: True" in out
    assert "http_port = 7777" in out


def test_config_edit_creates_and_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ozon_parser import cli as cli_mod

    calls: list[Path] = []
    monkeypatch.setattr(cli_mod, "open_in_editor", lambda p: calls.append(p) or 0)
    target = tmp_path / "c.toml"
    assert main(["--config", str(target), "config", "edit"]) == 0
    assert target.exists()
    assert "[input]" in target.read_text(encoding="utf-8")
    assert calls == [Path(target)]


def test_config_edit_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ozon_parser import cli as cli_mod

    monkeypatch.setattr(cli_mod, "open_in_editor", lambda p: 0)
    target = tmp_path / "c.toml"
    target.write_text("# custom\n", encoding="utf-8")
    assert main(["--config", str(target), "config", "edit"]) == 0
    assert target.read_text(encoding="utf-8") == "# custom\n"


# --- CLI: интеграция default-фолбэка --------------------------------------


def test_flush_uses_config_default_format(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ozon_parser.store import Store

    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[output]\ndefault_format = "jsonl"\n', encoding="utf-8")
    db = tmp_path / "c.db"
    with Store(db) as s:
        s.upsert(
            sku="1",
            url="",
            info={"title": "X"},
            markdown="# X\n",
            quality=5,
            copy_type="source",
        )
    assert main(["--config", str(cfg_path), "--db", str(db), "flush"]) == 0
    out = capsys.readouterr().out
    # jsonl, не markdown — формат взят из конфига
    assert out.startswith('{"sku"')


def test_flush_flag_overrides_config_format(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ozon_parser.store import Store

    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[output]\ndefault_format = "jsonl"\n', encoding="utf-8")
    db = tmp_path / "c.db"
    with Store(db) as s:
        s.upsert(
            sku="1",
            url="",
            info={"title": "X"},
            markdown="# X\n",
            quality=5,
            copy_type="source",
        )
    # Явный -f markdown перекрывает конфиг
    assert (
        main(
            ["--config", str(cfg_path), "--db", str(db), "flush", "-f", "markdown"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert out.startswith("# Ozon collection")
