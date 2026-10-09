@echo off
rem Builds a single-file SafaBrura.exe into dist\ (needs: pip install pyinstaller)
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name SafaBrura ^
  --add-data "data;data" --hidden-import comtypes.automation --hidden-import pystray._win32 autolang.py
echo.
echo Done: dist\SafaBrura.exe
