"""Native Messaging: host manifest и регистрация в браузерах.

- manifest.py — структура com.ozon_parser.json (allowed_origins, path)
- register.py — запись в реестр (Windows) / файл (macOS, Linux)

Сам stdio-цикл — в presentation/native_host.py: это inbound adapter.
Здесь только метаданные и регистрация — инфраструктурная часть.
"""
