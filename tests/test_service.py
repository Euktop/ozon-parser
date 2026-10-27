"""Тесты service.py: формирование unit/plist/команд для каждой платформы.

Реальные `schtasks`/`systemctl`/`launchctl` не вызываются — `subprocess.run`
и `Path.home` монкипатчатся. Проверяем:

- содержимое systemd unit и launchd plist;
- аргументы `schtasks` / `systemctl` / `launchctl`;
- идемпотентность install, missing-семантику uninstall, поля status;
- обработку ошибок (returncode != 0).
"""

from __future__ import annotations

import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from ozon_parser import service as S


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Запрет реальной сети: если тест случайно дёрнет urllib — упадёт явно."""
    def _blocked(*a, **kw):
        raise urllib.error.URLError("network disabled in tests")

    monkeypatch.setattr("urllib.request.urlopen", _blocked)


@pytest.fixture(autouse=True)
def _fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Path.home() → tmp_path; APPDATA → tmp; subprocess.run → запись."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    return tmp_path


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    responses: dict[str, subprocess.CompletedProcess] = {}

    def _run(cmd, **kw):
        calls.append(cmd)
        key = " ".join(cmd[:2])
        return responses.get(key, subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))

    monkeypatch.setattr(subprocess, "run", _run)
    return calls, responses


# --- валидация ------------------------------------------------------------


def test_invalid_mode() -> None:
    with pytest.raises(ValueError):
        S.install("bogus")


def test_valid_modes() -> None:
    assert S.VALID_MODES == ("serve", "daemon")


# --- helpers --------------------------------------------------------------


def test_python_args_serve() -> None:
    args = S._python_and_args("serve", port=9999, interval=None)
    assert args[-2:] == ["--port", "9999"]
    assert "-m" in args and "ozon_parser" in args and "serve" in args


def test_python_args_daemon_quiet() -> None:
    args = S._python_and_args("daemon", port=None, interval=2.0)
    assert "--quiet" in args
    assert args[-2:] == ["--interval", "2.0"]


def test_python_args_no_port() -> None:
    args = S._python_and_args("serve", port=None, interval=None)
    assert "--port" not in args


# --- linux ----------------------------------------------------------------


def test_install_linux_writes_unit(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = fake_run
    monkeypatch.setattr(sys, "platform", "linux")
    res = S.install("serve", port=8765)
    assert res.ok
    assert res.kind == "systemd"
    unit = _fake_home / ".config" / "systemd" / "user" / "ozon-parser-serve.service"
    assert unit.exists()
    text = unit.read_text(encoding="utf-8")
    assert "Description=Ozon Parser (serve)" in text
    assert "-m ozon_parser serve" in text
    assert "--port 8765" in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text
    assert any("enable" in " ".join(c) for c in calls)


def test_install_linux_daemon_has_quiet(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    S.install("daemon")
    unit = _fake_home / ".config" / "systemd" / "user" / "ozon-parser-daemon.service"
    text = unit.read_text(encoding="utf-8")
    assert "--quiet" in text


def test_install_linux_fails_on_systemctl(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, responses = fake_run
    responses["systemctl --user"] = subprocess.CompletedProcess(
        ["systemctl"], 1, stdout="", stderr="unit not found"
    )
    monkeypatch.setattr(sys, "platform", "linux")
    res = S.install("serve")
    # unit создан, но systemctl enable упал с returncode=1
    unit = _fake_home / ".config" / "systemd" / "user" / "ozon-parser-serve.service"
    assert unit.exists()
    assert isinstance(res, S.InstallResult)
    assert res.ok is False
    assert "systemctl enable failed" in res.message


def test_uninstall_linux_removes_unit(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    S.install("serve")
    unit = _fake_home / ".config" / "systemd" / "user" / "ozon-parser-serve.service"
    assert unit.exists()
    res = S.uninstall("serve")
    assert res.ok
    assert not res.missing
    assert not unit.exists()


def test_uninstall_linux_missing(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    res = S.uninstall("serve")
    assert res.ok
    assert res.missing


def test_status_linux(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    s = S.status("serve")
    assert not s.installed
    assert not s.running
    S.install("serve")
    s = S.status("serve")
    assert s.installed


# --- macOS ----------------------------------------------------------------


def test_install_macos_writes_plist(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = fake_run
    monkeypatch.setattr(sys, "platform", "darwin")
    res = S.install("serve", port=9000)
    assert res.ok
    assert res.kind == "launchd"
    plist = _fake_home / "Library" / "LaunchAgents" / "com.ozon-parser.serve.plist"
    assert plist.exists()
    text = plist.read_text(encoding="utf-8")
    assert "<string>com.ozon-parser.serve</string>" in text
    assert "<string>serve</string>" in text
    assert "<string>--port</string>" in text
    assert "<string>9000</string>" in text
    assert "RunAtLoad" in text
    assert "KeepAlive" in text
    assert any("launchctl" in c[0] for c in calls)


def test_install_macos_escapes_xml(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        S,
        "_python_and_args",
        lambda mode, port, interval, log_file=None: [
            "/py&thon", "-m", "ozon_parser", mode, "--tag", "a<b"
        ],
    )
    S.install("serve")
    plist = _fake_home / "Library" / "LaunchAgents" / "com.ozon-parser.serve.plist"
    text = plist.read_text(encoding="utf-8")
    assert "&amp;" in text
    assert "&lt;" in text
    assert "a<b" not in text


def test_uninstall_macos_removes_plist(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    S.install("serve")
    plist = _fake_home / "Library" / "LaunchAgents" / "com.ozon-parser.serve.plist"
    assert plist.exists()
    res = S.uninstall("serve")
    assert res.ok
    assert not plist.exists()


# --- Windows: Startup folder + VBS ----------------------------------------


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []

    def _popen(args, **kw):
        calls.append(args)
        return None

    monkeypatch.setattr(subprocess, "Popen", _popen)
    return calls


def _vbs_path(home: Path, mode: str) -> Path:
    return (
        home / "AppData" / "Roaming"
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / f"ozon-parser-{mode}.vbs"
    )


def test_install_windows_creates_vbs(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    res = S.install("serve", port=8765)
    assert res.ok
    assert res.kind == "startup-vbs"
    vbs = _vbs_path(_fake_home, "serve")
    assert vbs.exists()
    text = vbs.read_text(encoding="utf-16")
    assert "-m ozon_parser serve" in text
    assert "--port 8765" in text
    assert "WScript.Shell" in text
    # Запущен через wscript
    assert any(c[0] == "wscript.exe" for c in fake_popen)


def test_install_windows_daemon_quiet(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    S.install("daemon")
    text = _vbs_path(_fake_home, "daemon").read_text(encoding="utf-16")
    assert "daemon" in text
    assert "--quiet" in text


def test_install_windows_escapes_quotes(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        S,
        "_python_and_args",
        lambda mode, port, interval, log_file=None: [
            "C:\\Program Files\\Python\\python.exe", "-m", "ozon_parser", mode
        ],
    )
    S.install("serve")
    text = _vbs_path(_fake_home, "serve").read_text(encoding="utf-16")
    # Внешние кавычки VBS + экранированные внутренние = """path""
    assert '"""C:\\Program Files\\Python\\python.exe""' in text


def test_install_windows_launch_fails_still_ok(
    _fake_home: Path,
    fake_run,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _popen(args, **kw):
        raise FileNotFoundError("wscript not found")

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(sys, "platform", "win32")
    res = S.install("serve")
    # ok=True: файл записан, vbs появится при логине
    assert res.ok
    assert "launch failed" in res.message
    assert _vbs_path(_fake_home, "serve").exists()


def test_uninstall_windows_removes_vbs(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    S.install("serve")
    vbs = _vbs_path(_fake_home, "serve")
    assert vbs.exists()
    res = S.uninstall("serve")
    assert res.ok
    assert not res.missing
    assert not vbs.exists()


def test_uninstall_windows_missing(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    res = S.uninstall("serve")
    assert res.ok
    assert res.missing


def test_status_windows_not_installed(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    s = S.status("serve")
    assert not s.installed
    assert not s.running
    assert "not installed" in s.message


def test_status_windows_installed_serve_stopped(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    # Изолируем от реального конфига и реального /health
    monkeypatch.setattr("ozon_parser.config.load_config", lambda *a, **kw: type(
        "C", (), {"http_port": 8765}
    )())
    monkeypatch.setattr(S, "_probe_serve_health", lambda port, timeout=0.5: False)
    S.install("serve")
    s = S.status("serve")
    assert s.installed
    assert not s.running
    assert "not responding" in s.message


def test_status_windows_installed_serve_running(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("ozon_parser.config.load_config", lambda *a, **kw: type(
        "C", (), {"http_port": 8765}
    )())
    monkeypatch.setattr(S, "_probe_serve_health", lambda port, timeout=0.5: True)
    S.install("serve")
    s = S.status("serve")
    assert s.installed
    assert s.running
    assert "running" in s.message


def test_status_windows_installed_daemon(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    S.install("daemon")
    s = S.status("daemon")
    assert s.installed
    assert "running state unknown" in s.message


# --- log_file passing -----------------------------------------------------


def test_install_windows_includes_log_file(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    log = _fake_home / "logs" / "serve.log"
    res = S.install("serve", log_file=log)
    assert res.ok
    text = _vbs_path(_fake_home, "serve").read_text(encoding="utf-16")
    assert "--log-file" in text
    assert "serve.log" in text


def test_install_windows_no_log_file(
    _fake_home: Path,
    fake_run,
    fake_popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    S.install("serve", log_file=None)
    text = _vbs_path(_fake_home, "serve").read_text(encoding="utf-16")
    assert "--log-file" not in text


def test_install_linux_includes_log_file(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    log = _fake_home / "logs" / "serve.log"
    S.install("serve", log_file=log)
    unit = (
        _fake_home / ".config" / "systemd" / "user" / "ozon-parser-serve.service"
    )
    text = unit.read_text(encoding="utf-8")
    assert "--log-file" in text
    assert "serve.log" in text


def test_install_linux_no_log_file(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    S.install("serve", log_file=None)
    unit = (
        _fake_home / ".config" / "systemd" / "user" / "ozon-parser-serve.service"
    )
    text = unit.read_text(encoding="utf-8")
    assert "--log-file" not in text


def test_install_macos_includes_log_file(
    _fake_home: Path, fake_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    log = _fake_home / "logs" / "serve.log"
    S.install("serve", log_file=log)
    plist = (
        _fake_home / "Library" / "LaunchAgents" / "com.ozon-parser.serve.plist"
    )
    text = plist.read_text(encoding="utf-8")
    assert "--log-file" in text
    assert "serve.log" in text


# --- _probe_serve_health (unit) -------------------------------------------


def test_probe_serve_health_returns_false_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _no_network уже блокирует urlopen → должен вернуть False, не падать
    assert S._probe_serve_health(65535) is False


def test_probe_serve_health_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *exc) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _Resp())
    assert S._probe_serve_health(8765) is True


def test_probe_serve_health_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    class _Resp:
        status = 500

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *exc) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _Resp())
    assert S._probe_serve_health(8765) is False


# --- неподдерживаемая платформа -------------------------------------------


def test_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "plan9")
    res = S.install("serve")
    assert not res.ok
    assert "unsupported" in res.message

    u = S.uninstall("serve")
    assert not u.ok

    st = S.status("serve")
    assert not st.installed
