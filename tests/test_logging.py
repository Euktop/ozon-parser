"""Тесты setup_logging / default_log_path."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from ozon_parser.logging_utils import default_log_path, setup_logging


@pytest.fixture(autouse=True)

def _cleanup_file_handlers():
    """После каждого теста убираем FileHandler'ы, чтобы не течь fd на Windows.
    StreamHandler'ы оставляем — они не мешают.
    """
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler):
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)


# --- default_log_path -----------------------------------------------------


def test_default_log_path_win(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = default_log_path("serve")
    assert p == tmp_path / "ozon-parser" / "serve.log"


def test_default_log_path_darwin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    p = default_log_path("daemon")
    assert p == tmp_path / "Library" / "Logs" / "ozon-parser-daemon.log"


def test_default_log_path_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    p = default_log_path("serve")
    assert p == tmp_path / "ozon-parser" / "serve.log"


# --- setup_logging --------------------------------------------------------


def test_setup_logging_no_file_adds_stderr_handler() -> None:
    setup_logging(None)
    root = logging.getLogger()
    stream_handlers = [
        h for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    assert stream_handlers
    assert root.level == logging.INFO


def test_setup_logging_quiet_sets_warning() -> None:
    setup_logging(None, quiet=True)
    root = logging.getLogger()
    stream_handlers = [
        h for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    assert stream_handlers
    assert stream_handlers[0].level == logging.WARNING


def test_setup_logging_with_file_creates_and_writes(tmp_path: Path) -> None:
    log = tmp_path / "sub" / "test.log"
    setup_logging(log)
    assert log.parent.exists()
    assert log.exists()

    # Проверяем, что setup_logging реально поставил FileHandler на root.
    # Если пусто — тест упадёт с понятным сообщением, а не «file is empty».
    file_handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler)
    ]
    assert file_handlers, "setup_logging did not add a FileHandler"

    # Пишем через root, WARNING-уровень: проходит все handler-level фильтры.
    # Не зависим от состояния логгера "test" (pytest может его трогать).
    logging.getLogger().warning("hello-log")

    for h in logging.getLogger().handlers:
        h.flush()

    content = log.read_text(encoding="utf-8")
    assert "hello-log" in content, f"file content: {content!r}"
    # Формат строки: '<date> <time>,<ms> WARNING root: hello-log'
    # Проверяем, что asctime реально подставился (а не остался плейсхолдером
    # или, как было в баге, format-строка содержала несуществующее поле
    # `_asctime` — и тогда в файл вообще ничего не писалось).
    assert "WARNING root: hello-log" in content, (
        f"unexpected log line: {content!r}"
    )
    # asctime = YYYY-MM-DD HH:MM:SS,mmm — должен быть в начале строки
    assert content[:4].isdigit(), f"no asctime prefix: {content!r}"


def test_setup_logging_idempotent(tmp_path: Path) -> None:
    log1 = tmp_path / "a.log"
    log2 = tmp_path / "b.log"
    setup_logging(log1)
    setup_logging(log2)
    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(file_handlers) == 1
    assert file_handlers[0].baseFilename.endswith("b.log")


def test_setup_logging_unavailable_path_no_crash(tmp_path: Path) -> None:
    """Если log_file создать нельзя — не падаем, работаем со stderr."""
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    log = blocker / "sub.log"
    setup_logging(log)  # не должно бросить
    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.FileHandler)
    ]
    assert not file_handlers
