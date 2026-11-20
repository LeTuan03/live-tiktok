@echo off
rem Double-click to stop SoShop. Your data is kept.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\soshop.ps1" stop
echo.
pause
