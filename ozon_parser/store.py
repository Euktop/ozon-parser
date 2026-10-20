"""SQLite-хранилище распарсенных товаров Ozon.

Одна запись = один SKU. Повторный `upsert` обновляет поля, но сохраняет
`created_at` от первой вставки. Это позволяет безопасно перекопировать
тот же товар — запись не дублируется, а актуализируется.

WAL-режим включён, чтобы демон и CLI могли одновременно читать/писать.

Thread-safety
-------------
`ThreadingHTTPServer` (server.py) обрабатывает каждый HTTP-запрос в новом
потоке, а `Store` создаётся один раз. `sqlite3.connect()` по умолчанию
ставит `check_same_thread=True`, из-за чего handler падает с
`ProgrammingError: SQLite objects created in a thread can only be used in
that same thread`.

Поэтому:

- `check_same_thread=False` — разрешаем шарить соединение между потоками.
- `threading.RLock` вокруг каждой операции — сериализуем транзакции.
  SQLite C API потокобезопасен, но Python-обёртке нужно гарантировать,
  что `commit()`/`rollback()` не пересекаются. RLock (не Lock), потому
  что `clear()` вызывает `count()`, и будущие nested-вызовы не должны
  дедлочиться.
- `PRAGMA busy_timeout = 5000` — если два процесса (daemon + CLI) пишут
  одновременно, второй ждёт до 5 с вместо `database is locked`.
- `iter_all()` материализует строки в список под локом, а не отдаёт
  курсор наружу: иначе между `yield`-ами другой поток мог бы мутировать
  курсор.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    sku         TEXT PRIMARY KEY,
    url         TEXT,
    title       TEXT,
    brand       TEXT,
    price       TEXT,
    rating      TEXT,
    seller      TEXT,
    copy_type   TEXT,
    quality     INTEGER,
    markdown    TEXT NOT NULL,
    json        TEXT NOT NULL,
    html_hash   TEXT,
    raw_html    BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_created ON items(created_at DESC);
CREATE INDEX IF NOT EXISTS items_updated ON items(updated_at DESC);
"""


def default_db_path() -> Path:
    """Путь к БД по умолчанию в пользовательской data-директории ОС."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "ozon-parser" / "collection.db"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class Store:
    """Обёртка над SQLite. Один экземпляр = одно соединение.

    Thread-safe: `ThreadingHTTPServer` использует новый поток на каждый
    запрос, а Store один. См. docstring модуля.
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._lock = threading.RLock()  # RLock: clear() вызывает count() (nested lock)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- write -----------------------------------------------------------

    def upsert(
        self,
        *,
        sku: str,
        url: str,
        info: dict[str, Any],
        markdown: str,
        quality: int,
        copy_type: str,
        raw_html: bytes | None = None,
        html_text: str | None = None,
    ) -> bool:
        """True если запись новая, False если обновили существующую.

        `html_text` — исходный HTML (не gzip), из него считается `html_hash`.
        Хеш gzip-байтов нестабилен: `gzip.compress` пишет `mtime`, поэтому
        одинаковый HTML в разные секунды даёт разные байты.

        При `raw_html=None` / `html_text=None` существующие колонки не
        перезаписываются (COALESCE) — отключение `store_raw_html` не стирает
        ранее сохранённый архив.
        """
        sku = str(sku).strip()
        if not sku:
            raise ValueError("sku is empty")

        now = _utc_now_iso()
        html_hash: str | None = (
            hashlib.sha1(html_text.encode("utf-8")).hexdigest()
            if html_text is not None
            else None
        )

        payload = (
            url or "",
            str(info.get("title") or ""),
            str(info.get("brand") or ""),
            str(info.get("price") or info.get("price_card") or ""),
            str(info.get("rating") or ""),
            str(info.get("seller_name") or ""),
            copy_type,
            int(quality),
            markdown,
            _json_dumps(info),
            html_hash,
            raw_html,
        )

        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM items WHERE sku = ?", (sku,)
            ).fetchone() is not None

            if exists:
                self._conn.execute(
                    """
                    UPDATE items SET
                        url = ?, title = ?, brand = ?, price = ?, rating = ?,
                        seller = ?, copy_type = ?, quality = ?, markdown = ?,
                        json = ?, html_hash = COALESCE(?, html_hash),
                        raw_html = COALESCE(?, raw_html), updated_at = ?
                    WHERE sku = ?
                    """,
                    (*payload, now, sku),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO items (
                        url, title, brand, price, rating, seller, copy_type,
                        quality, markdown, json, html_hash, raw_html,
                        created_at, updated_at, sku
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*payload, now, now, sku),
                )
            self._conn.commit()
        return not exists

    # --- read ------------------------------------------------------------

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
        return int(row["n"])

    def list(self) -> list[dict[str, Any]]:
        """Список без полей markdown/json — для CLI `list` и расширения."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT sku, url, title, brand, price, rating, seller,
                       copy_type, quality, created_at, updated_at
                FROM items
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, sku: str) -> dict[str, Any] | None:
        """Полная запись, включая markdown и json."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM items WHERE sku = ?", (str(sku).strip(),)
            ).fetchone()
        return dict(row) if row else None

    _SUMMARY_COLUMNS = (
        "sku, url, title, brand, price, rating, seller, copy_type, quality, "
        "markdown, json, created_at, updated_at"
    )

    def iter_all(
        self,
        *,
        include_raw: bool = True,
        skus: list[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Все записи — для `flush` на больших коллекциях.

        `include_raw=False` — не тянет `raw_html` BLOB (главный источник
        памяти при `store_raw_html=true`). `flush` и `/flush` raw не нужен,
        только `reparse` / `export-fixture`.

        `skus` — фильтр на уровне SQL, а не в Python после загрузки.

        Материализуем под локом, чтобы курсор не шарился между потоками.
        """
        if skus is not None and not skus:
            return  # явный пустой фильтр = пусто, не «всё»
        cols = "*" if include_raw else self._SUMMARY_COLUMNS
        sql = f"SELECT {cols} FROM items"
        params: tuple = ()
        if skus is not None:
            placeholders = ",".join("?" * len(skus))
            sql += f" WHERE sku IN ({placeholders})"
            params = tuple(skus)
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        for row in rows:
            yield dict(row)

    def iter_with_raw(
        self, *, skus: set[str] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Стриминг записей с raw_html по одной (для `reparse`).

        В отличие от `iter_all`, не тянет все raw_html сразу: сначала
        лёгкий список SKU (только строки), затем по одной полной записи.
        Пиковая память — один raw_html, а не вся коллекция.

        `skus=set()` — явный пустой фильтр = пусто. `skus=None` — все.
        """
        if skus is not None and not skus:
            return
        sql = "SELECT sku FROM items WHERE raw_html IS NOT NULL"
        params: tuple = ()
        if skus is not None:
            placeholders = ",".join("?" * len(skus))
            sql += f" AND sku IN ({placeholders})"
            params = tuple(skus)
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            sku_rows = self._conn.execute(sql, params).fetchall()
        for sku_row in sku_rows:
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM items WHERE sku = ?", (sku_row["sku"],)
                ).fetchone()
            if row is not None:
                yield dict(row)

    # --- delete ----------------------------------------------------------

    def delete(self, sku: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM items WHERE sku = ?", (str(sku).strip(),)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def clear(self) -> int:
        with self._lock:
            n = self.count()
            self._conn.execute("DELETE FROM items")
            self._conn.commit()
            return n
