"""Presentation layer: транспорты (inbound adapters).

- cli.py         — argparse, stdin/stdout
- native_host.py — Chrome Native Messaging (stdio + 4-byte framing)
- server.py      — HTTP (опциональный транспорт для bookmarklet/Web UI)

Каждый транспорт — тонкий адаптер: маршрутизация в application use cases
и сериализация результата. Никакой бизнес-логики здесь нет.
"""
