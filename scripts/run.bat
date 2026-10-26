@echo off
REM Cross-platform launcher for the ytm2j web UI - Windows.
setlocal
cd /d "%~dp0.."

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo Python 3.9+ is required but was not found on PATH. >&2
  exit /b 1
)

%PY% -c "import yt_dlp, fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo First run: installing dependencies ...
  %PY% -m pip install --quiet -r requirements.txt || (
    echo Failed to install dependencies. >&2
    exit /b 1
  )
)

%PY% -m ytm2j serve %*
