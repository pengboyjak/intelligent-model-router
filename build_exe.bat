@echo off
chcp 65001 >nul
echo ╔══════════════════════════════════════════╗
echo ║  Model Router - Build Windows Installer  ║
echo ╚══════════════════════════════════════════╝
echo.

REM Check PyInstaller
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Installing PyInstaller...
    pip install pyinstaller
)

echo [1/3] Cleaning old builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo [2/3] Building executable...
pyinstaller --onefile ^
    --name "ModelRouter" ^
    --add-data "static;static" ^
    --add-data "config.yaml;." ^
    --hidden-import anthropic ^
    --hidden-import fastapi ^
    --hidden-import uvicorn ^
    --hidden-import httpx ^
    --hidden-import yaml ^
    --collect-all anthropic ^
    --noconsole ^
    gateway.py

if %errorlevel% neq 0 (
    echo [FAIL] Build failed!
    pause
    exit /b 1
)

echo [3/3] Build complete!
echo.
echo Output: dist\ModelRouter.exe
echo.
echo Run with: ModelRouter.exe --port 8701
echo Web UI:    http://localhost:8701
echo.
pause
