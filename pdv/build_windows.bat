@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ApPdv --icon=assets\appdv-icon.ico --add-data "assets;assets" run_pdv.py
if errorlevel 1 exit /b 1
where ISCC.exe >nul 2>nul
if not errorlevel 1 (
  ISCC.exe installer\ApPdv.iss
  echo Instalador criado em dist\installer\ApPdv-Setup.exe
) else (
  echo Executavel criado em dist\ApPdv.exe
  echo Instale Inno Setup para gerar o instalador Setup.
)
pause
