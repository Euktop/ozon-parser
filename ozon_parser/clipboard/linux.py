"""Linux clipboard backend: wl-paste / xclip / xsel + pyperclip fallback.

Порядок попыток для plain-text:
  1. ``wl-paste`` — Wayland.
  2. ``xclip`` — X11.
  3. ``xsel`` — X11.
  4. ``pyperclip`` — что-нибудь ещё (tkinter, Qt, ...).

Для rich HTML xsel не подходит (не умеет произвольные targets).
Порядок: ``wl-paste --type text/html`` → ``xclip -t text/html`` → ``None``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

import pyperclip

LOG = logging.getLogger("ozon_parser.clipboard.linux")


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        LOG.debug("run %s: %s", cmd, e)
        return None


def _which(name: str) -> str | None:
    return shutil.which(name)


def _from_cmd(cmd: list[str]) -> str | None:
    r = _run(cmd)
    if r is None or r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "ignore")


def read_text() -> str:
    if _which("wl-paste"):
        v = _from_cmd(["wl-paste", "--no-newline"])
        if v is not None:
            return v
    if _which("xclip"):
        v = _from_cmd(["xclip", "-selection", "clipboard", "-o"])
        if v is not None:
            return v
    if _which("xsel"):
        v = _from_cmd(["xsel", "--clipboard", "--output"])
        if v is not None:
            return v
    try:
        return pyperclip.paste() or ""
    except Exception as e:
        LOG.debug("pyperclip.paste: %s", e)
        return ""


def read_html() -> str | None:
    if _which("wl-paste"):
        v = _from_cmd(["wl-paste", "--type", "text/html"])
        if v is not None:
            return v
    if _which("xclip"):
        v = _from_cmd(["xclip", "-selection", "clipboard", "-t", "text/html", "-o"])
        if v is not None:
            return v
    return None


class LinuxBackend:
    """Бэкенд для Linux (X11 / Wayland)."""

    name = "linux"

    def read_text(self) -> str:
        return read_text()

    def read_html(self) -> str | None:
        return read_html()
