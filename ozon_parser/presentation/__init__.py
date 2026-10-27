"""Presentation layer: транспорты (inbound adapters).

- native_host.py — Chrome Native Messaging (stdio + 4-byte framing)

Примечание: `cli.py`, `daemon.py`, `server.py` пока лежат в корне
пакета (исторически), но по смыслу тоже относятся к presentation.

Каждый транспорт — тонкий адаптер: маршрутизация в application use cases
и сериализация результата. Никакой бизнес-логики здесь нет.
"""
