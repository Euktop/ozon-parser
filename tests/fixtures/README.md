# Fixtures

Сюда положите два файла из реального Ozon PDP для регрессионных тестов:

- `source.html` — копия через Ctrl+U → Ctrl+A → Ctrl+C (полный исходник).
- `rendered.html` — копия через Ctrl+A → Ctrl+C на странице (или Inspect корневого узла).

Требования:

- Один и тот же товар в обоих файлах — чтобы можно было сравнивать значения полей.
- Файлы не коммитятся в git (см. `.gitignore`).
- В rendered-копии атрибуты `data-state` разэкранированы как `&quot;` — это нормально, BeautifulSoup при `html.parser` их разэкранирует.

Использование в тестах:

    from pathlib import Path
    FIXTURES = Path(__file__).parent / "fixtures"
    SOURCE   = FIXTURES / "source.html"
    RENDERED = FIXTURES / "rendered.html"

Регрессионные тесты (реализованы, `skip` без файлов):

- `test_detection.py::test_real_source_is_source` — `detect_copy_type(source) == "source"`.
- `test_detection.py::test_real_rendered_is_rendered` — `detect_copy_type(rendered) == "rendered"`.
- `test_parser.py::test_real_source_parse` — ключевые поля (title, sku, price, rating, seller) заполнены.
- `test_parser.py::test_real_source_vs_rendered` — оба дают одинаковые значения для ключевых полей.
- `test_parser.py::test_real_source_clean_desc_node_safe` — `collect_product` не мутирует оригинальный soup на реальном source.html.
