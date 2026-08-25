@echo off
setlocal EnableExtensions EnableDelayedExpansion

title WraithguardToolkit - Linux Builder

echo ============================================================
echo  WraithguardToolkit Linux Binary Builder
echo ============================================================
echo.

:: Always use the directory containing this BAT file as the project root.
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

echo Project directory:
echo   %PROJECT_DIR%
echo.

:: ------------------------------------------------------------
:: Check Docker
:: ------------------------------------------------------------

where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker was not found in PATH.
    echo.
    echo Install Docker Desktop and make sure it is running.
    echo.
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Desktop is not running.
    echo.
    echo Start Docker Desktop and run this script again.
    echo.
    pause
    exit /b 1
)

:: ------------------------------------------------------------
:: Check required files
:: ------------------------------------------------------------

set "MISSING=0"

for %%F in (
    "wraithguard_toolkit_gui.py"
    "wraithguard_toolkit.py"
    "README.md"
    "QUICKSTART.md"
    "MLOX_RULES.md"
    "wraithguard_toolkit_icon.ico"
    "Dockerfile.wraithguard"
) do (
    if not exist "%PROJECT_DIR%\%%~F" (
        echo ERROR: Missing %%~F
        set "MISSING=1"
    )
)

if not exist "%PROJECT_DIR%\wraithguard\" (
    echo ERROR: Missing wraithguard directory
    set "MISSING=1"
)

if "%MISSING%"=="1" (
    echo.
    echo Required project files are missing.
    echo.
    pause
    exit /b 1
)

:: ------------------------------------------------------------
:: Create output directories
:: ------------------------------------------------------------

if not exist "%PROJECT_DIR%\dist" mkdir "%PROJECT_DIR%\dist"
if not exist "%PROJECT_DIR%\build" mkdir "%PROJECT_DIR%\build"

:: ------------------------------------------------------------
:: Build Docker image
:: ------------------------------------------------------------

echo.
echo ============================================================
echo  Building Docker image
echo ============================================================
echo.

docker build ^
    --pull ^
    -t wraithguard-toolkit-builder ^
    -f "%PROJECT_DIR%\Dockerfile.wraithguard" ^
    "%PROJECT_DIR%"

if errorlevel 1 (
    echo.
    echo ERROR: Docker image build failed.
    echo.
    pause
    exit /b 1
)

:: ------------------------------------------------------------
:: Run PyInstaller inside Linux container
:: ------------------------------------------------------------

echo.
echo ============================================================
echo  Building Linux executable
echo ============================================================
echo.

docker run ^
    --rm ^
    --name wraithguard-toolkit-build ^
    -v "%PROJECT_DIR%:/src" ^
    wraithguard-toolkit-builder

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  BUILD FAILED
    echo ============================================================
    echo.
    pause
    exit /b 1
)

:: ------------------------------------------------------------
:: Verify output
:: ------------------------------------------------------------

if not exist "%PROJECT_DIR%\dist\wraithguard_toolkit_gui" (
    echo.
    echo ERROR: PyInstaller completed but the Linux binary
    echo was not found.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  BUILD COMPLETE
echo ============================================================
echo.
echo Linux executable:
echo.
echo   %PROJECT_DIR%\dist\wraithguard_toolkit_gui
echo.

for %%A in ("%PROJECT_DIR%\dist\wraithguard_toolkit_gui") do (
    echo Size: %%~zA bytes
)

echo.
echo This file is a Linux executable and cannot be run directly
echo on Windows.
echo.

pause
endlocal