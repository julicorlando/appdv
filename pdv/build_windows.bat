@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ApPdv --icon=assets\appdv-icon.ico --add-data "assets;assets" run_pdv.py
echo.
echo Executavel criado em dist\ApPdv.exe
pause
