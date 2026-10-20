"""Application layer: use cases (оркестрация).

Зависит только от domain (ports + detection + quality). Не импортирует
infrastructure (SQLite, bs4) и presentation (CLI, server) — проверяется
tests/test_architecture.py.

- ingest: IngestHtmlUseCase — HTML → parse → quality → SKU → upsert → notify.
"""
