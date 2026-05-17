@echo off
REM NAKSHATRA-KAVACH — Dataset Download Setup
REM ==========================================
REM Creates a dedicated venv for the download scripts and runs the full download.
REM Usage: setup_and_download.bat [--quick]

setlocal
set SCRIPT_DIR=%~dp0
set VENV_DIR=%SCRIPT_DIR%venv

echo.
echo ============================================================
echo   NAKSHATRA-KAVACH — Dataset Downloader Setup
echo ============================================================
echo.

REM ── Create venv if it doesn't exist ────────────────────────────
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: Failed to create venv. Is Python 3.9+ installed?
        pause & exit /b 1
    )
    echo       Done: %VENV_DIR%
) else (
    echo [1/4] venv already exists — skipping creation
)

REM ── Upgrade pip ─────────────────────────────────────────────────
echo [2/4] Upgrading pip...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip --quiet

REM ── Install requirements ─────────────────────────────────────────
echo [3/4] Installing requirements...
"%VENV_DIR%\Scripts\pip.exe" install -r "%SCRIPT_DIR%requirements.txt" --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Check internet connection.
    pause & exit /b 1
)
echo       requests, tqdm, pandas, numpy installed.

REM ── Run downloader ───────────────────────────────────────────────
echo [4/4] Starting dataset download...
echo.

if "%1"=="--quick" (
    echo Mode: QUICK  (2010-2023, ~7M rows, ~3-5 GB)
    "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%download_all.py" --quick
) else (
    echo Mode: FULL   (2000-2024, ~12M rows, ~8-12 GB)
    echo TIP: Run with --quick flag for faster hackathon demo download.
    echo.
    "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%download_all.py"
)

if errorlevel 1 (
    echo.
    echo Download encountered errors. Check the log above.
    pause & exit /b 1
)

REM ── Build training dataset ────────────────────────────────────────
echo.
echo Building training Parquet files...
"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%build_training_dataset.py"

echo.
echo ============================================================
echo   DONE! Training data ready in: %SCRIPT_DIR%raw\
echo ============================================================
echo.
echo Next steps:
echo   cd ..\backend
echo   python -m ml_training.03_train_xgboost
echo   python -m ml_training.04_train_lstm
echo.
pause
