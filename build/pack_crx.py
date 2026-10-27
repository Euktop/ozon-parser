"""Сборщик .crx (CRX3 format) без использования chrome.exe.

Chrome удалил флаг --pack-extension в новых версиях, поэтому
собираем файл вручную по спецификации CRX3.

Использование: python build/pack_crx.py
"""
from __future__ import annotations

import io
import struct
import sys
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "extension"
PEM_PATH = ROOT / "build" / "ozon-parser-extension.pem"
OUT_PATH = ROOT / "dist" / "extension.crx"


def _varint(n: int) -> bytes:
    """Кодирует число в Protobuf varint."""
    buf = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n > 0:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            break
    return bytes(buf)


def main() -> int:
    if not PEM_PATH.exists():
        print(f"[!] Private key not found: {PEM_PATH}", file=sys.stderr)
        print("    Run: python build/gen_extension_key.py", file=sys.stderr)
        return 1

    print(f"[crx] Zipping {EXT_DIR}...")
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in EXT_DIR.rglob("*"):
            if f.is_file():
                # as_posix() чтобы внутри zip были прямые слэши
                zf.write(f, f.relative_to(EXT_DIR).as_posix())
    zip_data = zip_buf.getvalue()
    print(f"[crx] ZIP size: {len(zip_data)} bytes")

    print("[crx] Signing...")
    with open(PEM_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    pubkey_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # Данные, которые подписываются
    signed_data = b"CRX3 SignedData\x00"
    signed_data += struct.pack("<I", len(pubkey_der)) + pubkey_der
    signed_data += struct.pack("<I", len(zip_data)) + zip_data

    signature = private_key.sign(
        signed_data,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    # Собираем Protobuf заголовок CRX3
    # Field 1 (pubkey) = 0x0A, Field 2 (sig) = 0x12, AsymmetricKeyProof = 0x12
    pb_pubkey = b"\x0A" + _varint(len(pubkey_der)) + pubkey_der
    pb_sig = b"\x12" + _varint(len(signature)) + signature
    pb_rsa = pb_pubkey + pb_sig
    pb_header = b"\x12" + _varint(len(pb_rsa)) + pb_rsa

    # Финальный файл: Magic + Version(3) + HeaderLen + Header + ZipData
    crx_data = b"Cr24" + struct.pack("<I", 3) + struct.pack("<I", len(pb_header)) + pb_header + zip_data

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_bytes(crx_data)
    print(f"[ok] {OUT_PATH} ({len(crx_data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
