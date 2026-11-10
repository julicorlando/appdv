@echo off
setlocal
cd /d "%~dp0"
set "PDV_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PDV_PYTHON=.venv\Scripts\python.exe"
"%PDV_PYTHON%" -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
"%PDV_PYTHON%" -m PyInstaller --noconfirm --clean --onedir --windowed --name ApPdv --icon=assets\appdv-icon.ico --add-data "assets;assets" run_pdv.py
if errorlevel 1 exit /b 1
for /f "usebackq delims=" %%v in (`"%PDV_PYTHON%" -c "from app import VERSION; print(VERSION)"`) do set "APPDV_VERSION=%%v"
where ISCC.exe >nul 2>nul
if not errorlevel 1 (
  ISCC.exe /DAppVer=%APPDV_VERSION% installer\ApPdv.iss
  if errorlevel 1 exit /b 1
  echo Instalador criado em dist\installer\ApPdv-Setup-%APPDV_VERSION%.exe
) else (
  echo Aplicativo criado em dist\ApPdv\ApPdv.exe
  echo Mantenha toda a pasta ApPdv junta. Instale Inno Setup para gerar o instalador.
)
pause
