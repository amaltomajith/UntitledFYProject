@echo off
rem Double-click to set up and open Puppeteer-Code. The first run takes a few minutes.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add to PATH".
  pause
  exit /b 1
)

if not exist .venv (
  echo Setting up, this happens once...
  python -m venv .venv || goto failed
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -e ".[web]" || goto failed
)

if not exist web\out\ide.html (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo Node.js is needed once to build the IDE page. Get it from https://nodejs.org/
    pause
    exit /b 1
  )
  echo Building the IDE page...
  pushd web
  call npm ci || goto failed
  call npm run build || goto failed
  popd
)

.venv\Scripts\python -m puppeteer_code ide
exit /b 0

:failed
echo Setup failed. The messages above say why.
pause
exit /b 1
