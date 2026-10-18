@echo off
rem Собирает один exe: dist\GPO-Fishing-Macro.exe
cd /d "%~dp0"
python -m pip install -q -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name GPO-Fishing-Macro gpo_macro.py
