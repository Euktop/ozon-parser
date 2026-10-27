"""Entry point для ozon-parser.exe (PyInstaller)."""
import sys

from ozon_parser.cli import main

if __name__ == "__main__":
    sys.exit(main())
