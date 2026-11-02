@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ArmazemPDV app\main.py
echo.
echo Executavel criado em dist\ArmazemPDV.exe
pause
