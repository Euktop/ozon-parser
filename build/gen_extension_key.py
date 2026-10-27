"""Генератор extension key + стабильного ID.

Одноразовая операция для проекта. Создаёт RSA-2048 ключ, кладёт base64
публичного ключа в extension/manifest.json (поле `key`), пишет extension
ID в ozon_parser/native/_extension_id.py.

Зачем: без `key` Chrome генерирует случайный extension ID при каждом
Load unpacked. Installer не может его знать заранее → wizard спрашивает
ID руками. С `key` ID стабилен (одинаков на любой машине) → installer
хардкодит его через _extension_id.py.

Приватный ключ (build/ozon-parser-extension.pem) не коммитится
(.gitignore). Нужен только для Chrome Web Store upload; для локальной
установки достаточно публичного ключа в manifest.json.

Идемпотентно: если pem существует — переиспользует. Удалять только
если хочешь сменить ID (тогда: удалить pem → запустить → переустановить
extension + installer).
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PEM = ROOT / "build" / "ozon-parser-extension.pem"
MANIFEST = ROOT / "extension" / "manifest.json"
EXT_ID_FILE = ROOT / "ozon_parser" / "native" / "_extension_id.py"


def _load_or_gen_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if PEM.exists():
        print(f"[key] reusing: {PEM}", file=sys.stderr)
        return serialization.load_pem_private_key(PEM.read_bytes(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    PEM.parent.mkdir(parents=True, exist_ok=True)
    PEM.write_bytes(pem_bytes)
    try:
        PEM.chmod(0o600)
    except OSError:
        pass
    print(f"[key] generated: {PEM}", file=sys.stderr)
    return key


def _pubkey_b64_and_id(key) -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization

    der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    b64 = base64.b64encode(der).decode("ascii")
    # Chrome: SHA-256 от DER pubkey, первые 32 hex-символа, 0-9a-f → a-p.
    h = hashlib.sha256(der).hexdigest()
    ext_id = "".join(chr(ord("a") + int(c, 16)) for c in h[:32])
    return b64, ext_id


def _write_extension_id(ext_id: str) -> None:
    EXT_ID_FILE.write_text(
        '"""Extension ID. Генерируется build/gen_extension_key.py — не править руками."""\n'
        "\n"
        f'EXTENSION_ID = "{ext_id}"\n',
        encoding="utf-8",
    )
    print(f"[id]  wrote: {EXT_ID_FILE}", file=sys.stderr)


def _patch_manifest_json(b64: str) -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data.get("key") == b64:
        print(f"[key] manifest already up to date", file=sys.stderr)
        return
    data["key"] = b64
    MANIFEST.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[key] patched: {MANIFEST}", file=sys.stderr)


def main() -> int:
    try:
        key = _load_or_gen_key()
    except ImportError:
        print(
            "cryptography is required. Run: pip install cryptography",
            file=sys.stderr,
        )
        return 2
    b64, ext_id = _pubkey_b64_and_id(key)
    _patch_manifest_json(b64)
    _write_extension_id(ext_id)

    print()
    print(f"Extension ID: {ext_id}")
    print()
    print("Дальше:")
    print("  1. pip install -e .")
    print("  2. build\\build.bat")
    print('  3. "C:\\Program Files\\Inno Setup 7\\ISCC.exe" installer\\install.iss')
    print("  4. chrome://extensions → удалить Ozon Parser → Load unpacked → extension/")
    print("  5. installer\\Output\\ozon-parser-setup.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
