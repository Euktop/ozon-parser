r"""Регистрация Native Messaging host в Chromium-браузерах.

Windows: HKCU\<browser_key>\com.ozon_parser = <manifest_path>. Один
общий manifest в %LOCALAPPDATA%\ozon-parser\native\, регистрируется в
реестре каждого браузера отдельно.

macOS/Linux: manifest кладётся в ~/.config/<browser>/NativeMessagingHosts/
(или ~/Library/Application Support/... на macOS) — по файлу на браузер.

Идемпотентно: повторный register перезаписывает. unregister чистит всё.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ozon_parser.native.manifest import (
    BROWSERS,
    HOST_NAME,
    manifest_json,
)

LOG = logging.getLogger("ozon_parser.native.register")


def _platform() -> str:
    """Возвращает ключ BROWSERS или sys.platform, если платформа чужая.

    Явно проверяем linux, а не fallback на него: иначе plan9/freebsd/...
    попадают в linux-ветку и регистрируют manifest в несуществующих путях.
    """
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform  # unknown → не в BROWSERS → register вернёт ошибку


@dataclass
class RegisterResult:
    ok: bool
    registered: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    manifest_paths: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class UnregisterResult:
    ok: bool
    removed: list[str] = field(default_factory=list)
    message: str = ""


def _win_manifest_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "ozon-parser" / "native" / f"{HOST_NAME}.json"


def _write_manifest(path: Path, host_path: Path, extension_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest_json(host_path, extension_id), encoding="utf-8")


def _register_registry(key: str, manifest_path: Path) -> None:
    r"""Windows: HKCU\<key>\com.ozon_parser = str(manifest_path)."""
    import winreg  # type: ignore[import-not-found]
    full = f"{key}\\{HOST_NAME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, full) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(manifest_path))


def _unregister_registry(key: str) -> bool:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{key}\\{HOST_NAME}")
        return True
    except FileNotFoundError:
        return False


def register(host_path: Path, extension_id: str) -> RegisterResult:
    """Регистрирует host manifest во всех Chromium-браузерах платформы."""
    plat = _platform()
    if plat not in BROWSERS:
        return RegisterResult(ok=False, message=f"unsupported platform: {sys.platform}")

    registered: list[str] = []
    skipped: list[str] = []
    manifest_paths: list[str] = []

    if plat == "win32":
        manifest_path = _win_manifest_path()
        _write_manifest(manifest_path, host_path, extension_id)
        manifest_paths.append(str(manifest_path))
        for name, key in BROWSERS["win32"]:
            try:
                _register_registry(key, manifest_path)
                registered.append(name)
            except Exception as e:
                LOG.warning("register %s: %s", name, e)
                skipped.append(name)
    else:
        home = Path.home()
        for name, rel in BROWSERS[plat]:
            target = home / rel / f"{HOST_NAME}.json"
            try:
                _write_manifest(target, host_path, extension_id)
                manifest_paths.append(str(target))
                registered.append(name)
            except OSError as e:
                LOG.warning("write %s: %s", target, e)
                skipped.append(name)

    ok = len(registered) > 0
    msg = (
        f"registered: {', '.join(registered) or 'none'}"
        + (f"; skipped: {', '.join(skipped)}" if skipped else "")
    )
    return RegisterResult(
        ok=ok, registered=registered, skipped=skipped,
        manifest_paths=manifest_paths, message=msg,
    )


def default_host_path() -> Path:
    """Путь к исполняемому файлу host.

    - PyInstaller (frozen): sys.executable — это и есть host бинарь.
    - Dev/editable install: создаём wrapper-скрипт в user-data, который
      вызывает `python -m ozon_parser.presentation.native_host`. Chrome
      Native Messaging требует executable — модуль Python напрямую не
      запустит.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller: sys.executable — это тот .exe, который запущен.
        # Если запущен ozon-parser.exe (CLI) — рядом лежит ozon-parser-native.exe.
        # Native Messaging host — именно native; CLI stdout-парсер сломается.
        exe = Path(sys.executable)
        if sys.platform == "win32":
            sibling = exe.with_name("ozon-parser-native.exe")
        else:
            sibling = exe.with_name("ozon-parser-native")
        if sibling.exists():
            return sibling
        return exe
    if sys.platform == "win32":
        base = Path(
            os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        )
        wrapper = base / "ozon-parser" / "native" / "ozon-parser-native.bat"
        content = (
            "@echo off\r\n"
            f'"{sys.executable}" -m ozon_parser.presentation.native_host\r\n'
        )
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        )
        wrapper = base / "ozon-parser" / "native" / "ozon-parser-native.sh"
        content = (
            "#!/bin/sh\n"
            f'exec "{sys.executable}" -m ozon_parser.presentation.native_host "$@"\n'
        )
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(content, encoding="utf-8")
    if sys.platform != "win32":
        try:
            wrapper.chmod(0o755)
        except OSError:
            pass
    return wrapper


def unregister() -> UnregisterResult:
    """Удаляет manifest/registry keys, если были."""
    plat = _platform()
    if plat not in BROWSERS:
        return UnregisterResult(ok=False, message=f"unsupported platform: {sys.platform}")

    removed: list[str] = []
    if plat == "win32":
        for name, key in BROWSERS["win32"]:
            if _unregister_registry(key):
                removed.append(name)
        manifest_path = _win_manifest_path()
        try:
            manifest_path.unlink()
        except FileNotFoundError:
            pass
    else:
        home = Path.home()
        for name, rel in BROWSERS[plat]:
            target = home / rel / f"{HOST_NAME}.json"
            try:
                target.unlink()
                removed.append(name)
            except FileNotFoundError:
                pass

    return UnregisterResult(
        ok=True, removed=removed,
        message=f"removed: {', '.join(removed) or 'none'}",
    )
