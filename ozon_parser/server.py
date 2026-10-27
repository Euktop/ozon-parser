"""HTTP-сервер приёма Ozon PDP из bookmarklet / расширения.

Эндпоинты:

    GET    /health              → {"ok": true}
    POST   /ingest?url=...      → body = HTML, parse + upsert
    GET    /items               → [{sku, title, quality, updated_at}, ...]
    GET    /items/{sku}         → markdown (text/plain) или 404
    POST   /flush               → {"format": "markdown|jsonl|csv",
                                    "to": "response|stdout|clipboard|file",
                                    "path": "..."}
    DELETE /items/{sku}         → {"deleted": true|false}

Bind на 127.0.0.1 по умолчанию. CORS открыт для localhost-страниц
(bookmarklet на ozon.ru должен уметь POST сюда).

Сервер спроектирован так, чтобы его можно было запустить в фоновом
потоке и остановить через stop_event (нужно для тестов).
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ozon_parser.application.ingest import IngestHtmlUseCase
from ozon_parser.infrastructure.parsing.ozon import OzonHtmlParser
from ozon_parser.render import render_collection
from ozon_parser.store import Store

LOG = logging.getLogger("ozon_parser.server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

MAX_BODY_BYTES = 30 * 1024 * 1024  # 30 MB


# ============================================================================
#  handler
# ============================================================================


def _cors_headers() -> dict[str, str]:
    # Allow-Origin только ozon.ru: bookmarklet выполняется в page-origin
    # ozon.ru и нуждается в CORS. Extension обходит CORS через
    # host_permissions — на него заголовок не влияет. Любой другой сайт
    # получит CORS-блок на fetch → /ingest, что и есть защита от злых
    # сайтов (при Allow-Origin: * такой защиты не было).
    return {
        "Access-Control-Allow-Origin": "https://www.ozon.ru",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Private-Network": "true",
        "Access-Control-Max-Age": "600",
    }


class Handler(BaseHTTPRequestHandler):
    store: Store
    store_raw_html: bool = True
    config: dict | None = None
    server_version = "ozon-parser/0.5.1"

    def _public_config(self) -> dict:
        """Публичный config для discovery."""
        return dict(self.config or {})

    # ----- helpers -----

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in _cors_headers().items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in _cors_headers().items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        if n > MAX_BODY_BYTES:
            raise ValueError(f"body too large: {n} bytes")
        return self.rfile.read(n)

    # ----- verb dispatch -----

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        for k, v in _cors_headers().items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send_json(200, {"ok": True})
        if u.path == "/config":
            return self._send_json(200, self._public_config())
        if u.path == "/items":
            return self._send_json(200, {"items": self.store.list()})
        if u.path.startswith("/items/"):
            sku = u.path[len("/items/"):].strip("/")
            if not sku:
                return self._send_json(400, {"error": "empty sku"})
            row = self.store.get(sku)
            if row is None:
                return self._send_json(404, {"error": "not found", "sku": sku})
            return self._send_text(200, row.get("markdown") or "")
        return self._send_json(404, {"error": "unknown path", "path": u.path})

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/ingest":
            return self._handle_ingest(u)
        if u.path == "/flush":
            return self._handle_flush()
        return self._send_json(404, {"error": "unknown path", "path": u.path})

    def do_DELETE(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        # DELETE /items (без sku) — очистить всю коллекцию.
        # Popup «Очистить всё» раньше шёл по одному SKU — N round-trip'ов
        # и рассинхрон при сбое на середине. Теперь — атомарный вызов.
        if u.path in ("/items", "/items/"):
            n = self.store.clear()
            return self._send_json(200, {"cleared": n})
        if u.path.startswith("/items/"):
            sku = u.path[len("/items/"):].strip("/")
            if not sku:
                return self._send_json(400, {"error": "empty sku"})
            ok = self.store.delete(sku)
            return self._send_json(200, {"deleted": ok, "sku": sku})
        return self._send_json(404, {"error": "unknown path", "path": u.path})

    # ----- endpoint implementations -----

    def _handle_ingest(self, u) -> None:
        try:
            raw = self._read_body()
        except ValueError as e:
            return self._send_json(413, {"status": "error", "error": str(e)})
        try:
            html = raw.decode("utf-8")
        except UnicodeDecodeError:
            html = raw.decode("cp1251", "ignore")

        qs = parse_qs(u.query)
        url_hint = (qs.get("url") or [""])[0]

        include_images = bool(
            (self.config or {}).get("include_image_urls", False)
        )
        use_case = IngestHtmlUseCase(
            parser=OzonHtmlParser(),
            repository=self.store,
            store_raw_html=self.store_raw_html,
            include_images=include_images,
        )
        result = use_case.execute(html, url_hint=url_hint)

        if result.status == "skipped":
            return self._send_json(400, {
                "status": "skipped",
                "error": "not an Ozon PDP",
                "payload_len": result.payload_len,
            })

        if result.status == "error":
            return self._send_json(500, {
                "status": "error",
                "error": f"parse: {result.error}",
            })

        if result.status == "no_sku":
            return self._send_json(200, {
                "status": "no_sku",
                "quality": result.quality,
                "copy_type": result.copy_type,
            })

        resp = result.to_dict()
        resp["title"] = (result.info.get("title") or "")[:120]
        resp["url"] = (
            str(result.info.get("url") or "").strip() or url_hint
        )
        return self._send_json(200, resp)

    def _handle_flush(self) -> None:
        try:
            raw = self._read_body()
            req = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError) as e:
            return self._send_json(400, {"error": f"bad json: {e}"})
        if not isinstance(req, dict):
            return self._send_json(400, {"error": "expected JSON object"})

        fmt = str(req.get("format") or "markdown")
        target = str(req.get("to") or "response")

        # HTTP /flush принимает только to=response. Запись на диск сервера
        # (to=file) и управление буфером сервера (to=clipboard) доступны
        # ТОЛЬКО из CLI: в HTTP это arbitrary file write vector — злой
        # сайт мог записать .bat в Startup folder. Extension сохраняет
        # ответ через chrome.downloads (browser API), без доступа к ФС.
        if target != "response":
            return self._send_json(400, {
                "error": (
                    f"unsupported target: {target!r}; "
                    "only 'response' is allowed over HTTP"
                ),
            })

        if "only" in req:
            only = req["only"]
            if not isinstance(only, list):
                return self._send_json(400, {"error": "only must be a list"})
            wanted = [str(x) for x in only]
            items = list(
                self.store.iter_all(include_raw=False, skus=wanted)
            )
        else:
            items = list(self.store.iter_all(include_raw=False))

        if not items:
            return self._send_json(200, {"status": "empty", "items": 0})

        try:
            text = render_collection(items, fmt)
        except ValueError as e:
            return self._send_json(400, {"error": str(e)})

        return self._send_text(200, text)


# ============================================================================
#  server
# ============================================================================


class _ThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer с безопасной семантикой SO_REUSEADDR.

    На Windows `allow_reuse_address=True` (дефолт HTTPServer) позволяет
    ДВУМ живым процессам bind'иться на один и тот же порт. Запросы при
    этом идут только к первому — второй висит зомби в LISTENING.

    Практическое следствие (поймано вручную на 127.0.0.1:8765):
    2-3 вызова `service install` без `uninstall` → 4 python.exe в
    LISTENING на одном порту. Новый `serve` биндится, но `curl` идёт
    к старому процессу — лог нового пустой, поведение не то.

    Фикс: выключаем `allow_reuse_address` на Windows. Второй bind
    получит явную `OSError: [WinError 10048]` вместо тихого дублирования.
    На Unix — оставляем `True`: там семантика другая (разрешает bind
    только после TIME_WAIT), и без флага перезапуск сервера залипает
    на ~2 минуты.
    """

    allow_reuse_address = sys.platform != "win32"


def make_server(
    store: Store,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    store_raw_html: bool = True,
    config: dict | None = None,
) -> _ThreadingHTTPServer:
    """Создаёт HTTP-сервер, но не запускает его.

    Тесты используют это, чтобы выбрать свободный порт.
    """
    handler = type(
        "BoundHandler",
        (Handler,),
        {
            "store": store,
            "store_raw_html": store_raw_html,
            "config": config,
        },
    )
    httpd = _ThreadingHTTPServer((host, port), handler)
    return httpd


def run_server(
    store: Store,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    stop_event: threading.Event | None = None,
    store_raw_html: bool = True,
    config: dict | None = None,
) -> None:
    """serve_forever() в текущем потоке; остановка — по stop_event."""
    httpd = make_server(
        store,
        host,
        port,
        store_raw_html=store_raw_html,
        config=config,
    )
    if stop_event is None:
        stop_event = threading.Event()

    def _watch() -> None:
        stop_event.wait()
        httpd.shutdown()

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()

    LOG.info("HTTP listening on http://%s:%d", host, port)
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        httpd.server_close()
        LOG.info("HTTP stopped")
