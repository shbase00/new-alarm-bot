@echo off
echo ========================================
echo   NFT Mint Alarm Bot - Setup
echo ========================================
echo.

echo Step 1: Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed. Make sure Python is installed.
    pause
    exit /b 1
)

echo.
echo Step 2: Installing Chromium browser for Playwright...
playwright install chromium
if errorlevel 1 (
    echo ERROR: playwright install failed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Installation Complete!
echo ========================================
echo.
echo Next steps:
echo 1. Copy .env.example to .env
echo 2. Fill in your BOT_TOKEN and ADMIN_IDS in .env
echo 3. Run:  py bot.py
echo.
pause
