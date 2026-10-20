"""Генератор JS-букмарклета.

Букмарклет делает два запроса:

1. fetch(location.href) — забирает ПОЛНЫЙ исходник страницы
   (с `data-server-rendered="true"`, NUXT-state, client-state).
   Это именно source-копия, лучшее качество.
2. POST /ingest — отправляет HTML на локальный сервер ozon-parser.

Если `fetch(location.href)` недоступен (CSP, старая страница),
букмарклет падает, и пользователь использует обычный Ctrl+A/Ctrl+C.
"""

def generate_bookmarklet(base_url: str) -> str:
    """Возвращает `javascript:...`-URL для закладки.

    `base_url` — например, `http://127.0.0.1:8765` (без слэша в конце).
    """
    base = base_url.rstrip("/")
    # Один IIFE, чтобы не засорять глобальный scope.
    js = (
        "javascript:(async()=>{try{"
        f"const S='{base}';"
        "const h=await(await fetch(location.href)).text();"
        "const u=S+'/ingest?url='+encodeURIComponent(location.href);"
        "const r=await fetch(u,{method:'POST',body:h});"
        "const j=await r.json();"
        "alert('ozon-parser: '+(j.status||'?')+(j.sku?' '+j.sku:'')+(j.error?' — '+j.error:''));"
        "}catch(e){alert('ozon-parser: '+e)}})()"
    )
    return js
