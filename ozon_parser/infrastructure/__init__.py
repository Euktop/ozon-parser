"""Infrastructure layer: адаптеры domain-портов.

- persistence/sqlite: SqliteProductRepository → ProductRepository
- parsing/ozon:      OzonHtmlParser       → HtmlParser
- notifier:          DesktopNotifier      → Notifier

Адаптеры — тонкие обёртки над существующими реализациями (store, parser,
notifier). Не переписывают логику. Назначение: явная точка инъекции в
use case + стабильный import path при будущей реорганизации.

Infrastructure может импортировать domain + application + существующие
top-level модули (store/parser/notifier).
"""
