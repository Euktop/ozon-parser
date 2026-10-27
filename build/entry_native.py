"""Entry point для ozon-parser-native.exe (PyInstaller).

Chrome Native Messaging: процесс запускается браузером, общается
через stdin/stdout. Не CLI, не консольная утилита.
"""
import sys

from ozon_parser.presentation.native_host import main

if __name__ == "__main__":
    sys.exit(main())
