"""Domain layer: чистая логика без I/O.

- detection: detect_copy_type, looks_like, is_usable — по HTML.
- quality: quality_score — по распарсенному info.
- ports: HtmlParser, ProductRepository, Notifier — Protocol-интерфейсы.

Правило: domain не импортирует ничего из ozon_parser.*, кроме domain.*
и stdlib. Проверяется tests/test_architecture.py.
"""
