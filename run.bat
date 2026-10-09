@echo off
rem Starts Safa Brura from source (no console window). Needs Python 3.10+ and: pip install -r requirements.txt
cd /d "%~dp0"
start "" pythonw autolang.py
