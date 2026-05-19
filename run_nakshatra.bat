@echo off
setlocal EnableExtensions

title NAKSHATRA-KAVACH Launcher

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "BACKEND_PORT=5000"
set "FRONTEND_PORT=5173"
set "BACKEND_URL=http://127.0.0.1:%BACKEND_PORT%"
set "FRONTEND_URL=http://localhost:%FRONTEND_PORT%"
set "PYTHON_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
set "PIP_EXE=%BACKEND_DIR%\venv\Scripts\pip.exe"
set "BACKEND_DEPS_MARKER=%BACKEND_DIR%\.deps.ok"
set "FORCE_INSTALL=0"

if /I "%~1"=="--install" set "FORCE_INSTALL=1"

echo =====================================================================
echo  NAKSHATRA-KAVACH - Project Launcher
echo =====================================================================
echo  Root:     %ROOT%
echo  Backend:  %BACKEND_URL%
echo  Frontend: %FRONTEND_URL%
echo =====================================================================
echo.

if not exist "%BACKEND_DIR%\run.py" (
  echo [ERROR] backend\run.py not found.
  echo Keep this launcher in the project root: %ROOT%
  goto :fail
)

if not exist "%FRONTEND_DIR%\package.json" (
  echo [ERROR] frontend\package.json not found.
  echo Keep this launcher in the project root: %ROOT%
  goto :fail
)

where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js is not available on PATH.
  goto :fail
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm is not available on PATH.
  goto :fail
)

where curl.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] curl.exe is not available on PATH.
  goto :fail
)

if /I "%~1"=="--check" (
  echo [CHECK] Project layout and Node/npm/curl checks passed.
  echo [CHECK] Backend Python expected at: %PYTHON_EXE%
  echo [CHECK] Frontend package found at: %FRONTEND_DIR%\package.json
  exit /b 0
)

echo [1/7] Preparing Python virtual environment...
if not exist "%PYTHON_EXE%" (
  echo Backend venv missing. Creating backend\venv...
  py -3 -m venv "%BACKEND_DIR%\venv" 2>nul
  if errorlevel 1 python -m venv "%BACKEND_DIR%\venv"
  set "FORCE_INSTALL=1"
)

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Could not create or find backend virtual environment.
  goto :fail
)

echo [2/7] Checking backend dependencies...
if not exist "%BACKEND_DEPS_MARKER%" (
  if exist "%BACKEND_DIR%\venv\Lib\site-packages\groq" if exist "%BACKEND_DIR%\venv\Lib\site-packages\flask" (
    echo Existing backend packages detected. Writing dependency marker.
    echo ok>"%BACKEND_DEPS_MARKER%"
  ) else (
    set "FORCE_INSTALL=1"
  )
)
if "%FORCE_INSTALL%"=="1" (
  if exist "%BACKEND_DIR%\requirements.txt" (
    echo Installing backend packages. This may take a few minutes the first time.
    "%PIP_EXE%" install -r "%BACKEND_DIR%\requirements.txt"
    if errorlevel 1 (
      echo [ERROR] Backend dependency installation failed.
      goto :fail
    )
    echo ok>"%BACKEND_DEPS_MARKER%"
  )
) else (
  echo Backend dependencies already installed. Use run_nakshatra.bat --install to force refresh.
)

echo [3/7] Checking frontend dependencies...
if not exist "%FRONTEND_DIR%\node_modules" (
  pushd "%FRONTEND_DIR%"
  call npm install
  if errorlevel 1 (
    popd
    echo [ERROR] Frontend dependency installation failed.
    goto :fail
  )
  popd
) else (
  echo Frontend dependencies already installed.
)

echo [4/7] Stopping old dev servers on ports %BACKEND_PORT% and %FRONTEND_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort %BACKEND_PORT%,%FRONTEND_PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 } | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul

echo [5/7] Starting backend...
set "BACKEND_CMD=%TEMP%\nakshatra_backend_%RANDOM%%RANDOM%.cmd"
(
  echo @echo off
  echo title Nakshatra Backend
  echo cd /d "%BACKEND_DIR%"
  echo set "PORT=%BACKEND_PORT%"
  echo set "NAKSHATRA_PORT=%BACKEND_PORT%"
  echo set "FLASK_ENV=development"
  echo set "SOCKETIO_ASYNC_MODE=threading"
  echo set "PYTHONUNBUFFERED=1"
  echo "%PYTHON_EXE%" run.py
) > "%BACKEND_CMD%"
start "Nakshatra Backend" cmd /k call "%BACKEND_CMD%"

echo Waiting for backend health endpoint. Model loading can take 30-90 seconds...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(180); do { try { Invoke-WebRequest -UseBasicParsing -Uri '%BACKEND_URL%/api/solar/status' -TimeoutSec 3 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 2 } } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
  echo [ERROR] Backend did not become ready at %BACKEND_URL%.
  echo Check the "Nakshatra Backend" window for the Python error.
  goto :fail
)

echo [6/7] Starting frontend...
set "FRONTEND_CMD=%TEMP%\nakshatra_frontend_%RANDOM%%RANDOM%.cmd"
(
  echo @echo off
  echo title Nakshatra Frontend
  echo cd /d "%FRONTEND_DIR%"
  echo set "VITE_API_BASE=http://localhost:%BACKEND_PORT%"
  echo set "VITE_USE_MOCK_DATA=false"
  echo set "VITE_DEFAULT_LANG=en"
  echo call npm run dev -- --host 0.0.0.0 --port %FRONTEND_PORT% --strictPort
) > "%FRONTEND_CMD%"
start "Nakshatra Frontend" cmd /k call "%FRONTEND_CMD%"

echo Waiting for Vite dev server...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(90); do { try { Invoke-WebRequest -UseBasicParsing -Uri '%FRONTEND_URL%' -TimeoutSec 3 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 2 } } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
  echo [ERROR] Frontend did not become ready at %FRONTEND_URL%.
  echo Check the "Nakshatra Frontend" window for the npm/Vite error.
  goto :fail
)

echo [7/7] Opening dashboard...
start "" "%FRONTEND_URL%"

echo.
echo =====================================================================
echo  Services are running.
echo  Backend:  %BACKEND_URL%
echo  Frontend: %FRONTEND_URL%
echo.
echo  Close the "Nakshatra Backend" and "Nakshatra Frontend" windows to stop.
echo =====================================================================
echo.
pause
exit /b 0

:fail
echo.
echo Launcher failed. Fix the message above and run run_nakshatra.bat again.
echo.
pause
exit /b 1
