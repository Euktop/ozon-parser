@echo off
rem Сборка: .exe (PyInstaller). Требует: pip install pyinstaller.

setlocal
cd /d "%~dp0\.."

echo [1/3] pip install pyinstaller (если нет)
pip show pyinstaller >nul 2>&1 || pip install pyinstaller

echo [2/3] pyinstaller build\ozon-parser.spec
pyinstaller --noconfirm --clean build\ozon-parser.spec
if errorlevel 1 (
    echo [!] PyInstaller failed
    exit /b 1
)

:done
echo.
echo Готово: dist\ozon-parser.exe, dist\ozon-parser-native.exe
endlocal
