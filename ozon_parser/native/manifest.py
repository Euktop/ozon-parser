"""Native Messaging host manifest (com.ozon_parser.json).

Структура (Chrome):
    {
      "name": "com.ozon_parser",
      "description": "...",
      "path": "/abs/path/to/host.exe",
      "type": "stdio",
      "allowed_origins": ["chrome-extension://<ID>/"]
    }

BROWSERS описывает, куда класть manifest / какой registry key для каждого
Chromium-браузера. Список — по факту установленных; register.py регистрирует
во все, кто найден в системе.
"""
from __future__ import annotations

import json
from pathlib import Path

HOST_NAME = "com.ozon_parser"


# key для win32 — registry path (HKCU\<key>\com.ozon_parser = manifest_path).
# Для darwin/linux — относительный путь от Path.home().
BROWSERS: dict[str, list[tuple[str, str]]] = {
    "win32": [
        ("chrome", r"Software\Google\Chrome\NativeMessagingHosts"),
        ("edge", r"Software\Microsoft\Edge\NativeMessagingHosts"),
        ("yandex", r"Software\Yandex\YandexBrowser\NativeMessagingHosts"),
        ("brave", r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"),
        ("chromium", r"Software\Chromium\NativeMessagingHosts"),
        ("vivaldi", r"Software\Vivaldi\NativeMessagingHosts"),
    ],
    "darwin": [
        ("chrome", "Library/Application Support/Google/Chrome/NativeMessagingHosts"),
        ("edge", "Library/Application Support/Microsoft Edge/NativeMessagingHosts"),
        ("yandex", "Library/Application Support/Yandex/YandexBrowser/NativeMessagingHosts"),
        ("brave", "Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts"),
        ("chromium", "Library/Application Support/Chromium/NativeMessagingHosts"),
        ("vivaldi", "Library/Application Support/Vivaldi/NativeMessagingHosts"),
    ],
    "linux": [
        ("chrome", ".config/google-chrome/NativeMessagingHosts"),
        ("edge", ".config/microsoft-edge/NativeMessagingHosts"),
        ("yandex", ".config/yandex-browser/NativeMessagingHosts"),
        ("brave", ".config/BraveSoftware/Brave-Browser/NativeMessagingHosts"),
        ("chromium", ".config/chromium/NativeMessagingHosts"),
        ("vivaldi", ".config/vivaldi/NativeMessagingHosts"),
    ],
}


def build_manifest(host_path: Path, extension_id: str) -> dict:
    """Manifest как dict для сериализации."""
    return {
        "name": HOST_NAME,
        "description": "Ozon Parser native messaging host",
        "path": str(host_path.resolve()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def manifest_json(host_path: Path, extension_id: str) -> str:
    """Готовый JSON (indent=2, UTF-8)."""
    return json.dumps(
        build_manifest(host_path, extension_id),
        indent=2,
        ensure_ascii=False,
    )
