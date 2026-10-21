@echo off
rem Собирает папку dist\FishPilot (FishPilot.exe + _internal) и архив dist\FishPilot-win64.zip
cd /d "%~dp0"
python -m pip install -q -r requirements.txt pyinstaller==6.22.3 pip-licenses==5.0.0 || exit /b 1
python tools\build_meta.py version build\version.txt || exit /b 1
python tools\build_meta.py licenses build\THIRD_PARTY_LICENSES.txt || exit /b 1
python -m PyInstaller --noconfirm --onedir --windowed --noupx --name FishPilot ^
  --version-file build\version.txt --icon assets\icon.ico --add-data "assets;assets" ^
  --collect-data customtkinter --collect-all winrt gpo_macro.py || exit /b 1
copy /Y LICENSE dist\FishPilot\LICENSE.txt >nul
copy /Y build\THIRD_PARTY_LICENSES.txt dist\FishPilot\ >nul
copy /Y README.md dist\FishPilot\README.md >nul
powershell -NoProfile -Command "Compress-Archive -Force -Path dist\FishPilot -DestinationPath dist\FishPilot-win64.zip"
