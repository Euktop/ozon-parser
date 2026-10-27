# PyInstaller spec: два onefile-бинаря (CLI + Native Messaging host).
#
# Сборка: pyinstaller build/ozon-parser.spec
# Результат: dist/ozon-parser.exe, dist/ozon-parser-native.exe
#
# console=True для обоих: CLI запускается из cmd (нужен stdout), native
# запускается Chrome'ом скрыто — окно не появится даже с console=True,
# а stdio-pipe работает надёжнее, чем с --windowed.

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).parent


def _analysis(entry: Path, name: str):
    return Analysis(
        [str(entry)],
        pathex=[str(ROOT)],
        binaries=[],
        datas=[],
        hiddenimports=[
            # lxml C-расширения: hook PyInstaller'а не всегда их подхватывает,
            # особенно если lxml установлен через wheel после snapshot'а
            # PyInstaller. Явный список — самый безопасный вариант.
            "lxml.etree",
            "lxml._elementpath",
            "lxml.html",
            # bs4 использует этот builder для lxml-парсинга.
            "bs4.builder._lxml",
        ],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        # PyQt5/PySide тянутся hooks PyInstaller'а из env (даже если
        # наш код их не импортирует). Раздувают .exe с 15 до 50 МБ.
        # ai_talk/docs2md — другие editable-пакеты в PYTHONPATH.
        excludes=[
            "tkinter", "pytest", "matplotlib", "numpy", "PIL",
            "PyQt5", "PyQt6", "PySide2", "PySide6", "shiboken2", "shiboken6",
            "IPython", "jupyter", "notebook",
            "ai_talk", "docs2md",
            "coverage", "_pytest", "pytest_cov",
        ],
        win_no_prefer_redirects=False,
        win_private_assemblies=False,
        cipher=block_cipher,
        noarchive=False,
    )


# --- ozon-parser.exe (CLI) ---
a_cli = _analysis(ROOT / "build" / "entry_cli.py", "ozon-parser")
pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data, cipher=block_cipher)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    a_cli.binaries,
    a_cli.zipfiles,
    a_cli.datas,
    [],
    name="ozon-parser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
)

# --- ozon-parser-native.exe (Native Messaging host) ---
a_native = _analysis(ROOT / "build" / "entry_native.py", "ozon-parser-native")
pyz_native = PYZ(a_native.pure, a_native.zipped_data, cipher=block_cipher)
exe_native = EXE(
    pyz_native,
    a_native.scripts,
    a_native.binaries,
    a_native.zipfiles,
    a_native.datas,
    [],
    name="ozon-parser-native",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
)
