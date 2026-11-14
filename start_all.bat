@echo off
setlocal
cd /d "%~dp0"

if exist "var\admin_env.bat" call "var\admin_env.bat"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv\Scripts\python.exe not found.
  echo Create the environment first:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
  pause
  exit /b 1
)

if not exist "var\logs" mkdir "var\logs"

if "%DINELENS_ADMIN_TOKEN%"=="" (
  echo DINELENS_ADMIN_TOKEN is not configured.
  set /p DINELENS_ADMIN_TOKEN=Enter admin token:
)
if "%DINELENS_ADMIN_TOKEN%"=="" (
  echo [WARN] No admin token. The panel will open but its API will return 503.
)

set "PY=%~dp0.venv\Scripts\python.exe"
set "ADMIN_PORT=%DINELENS_ADMIN_PORT%"
if "%ADMIN_PORT%"=="" set "ADMIN_PORT=8765"

"%PY%" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',8000))==0 else 1)" >nul 2>&1
if errorlevel 1 (
  echo Starting backend on http://127.0.0.1:8000 ...
  start "DineLens Backend" /min "%PY%" -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
) else (
  echo Backend already listening on 127.0.0.1:8000.
)

"%PY%" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',%ADMIN_PORT%))==0 else 1)" >nul 2>&1
if errorlevel 1 (
  echo Starting control panel on http://127.0.0.1:%ADMIN_PORT% ...
  start "DineLens Admin" /min "%PY%" -m uvicorn admin.server:app --host 127.0.0.1 --port %ADMIN_PORT%
) else (
  echo Control panel already listening on 127.0.0.1:%ADMIN_PORT%.
)

"%PY%" admin\wait_port.py %ADMIN_PORT% >nul 2>&1
if errorlevel 1 echo [WARN] Control panel is not answering on 127.0.0.1:%ADMIN_PORT% yet.

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%EDGE%" (
  start "" "%EDGE%" --app=http://127.0.0.1:%ADMIN_PORT%
) else (
  start "" http://127.0.0.1:%ADMIN_PORT%
)

endlocal
