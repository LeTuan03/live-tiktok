@echo off
rem Double-click to start SoShop (needs Docker Desktop). See HUONG-DAN.md.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\soshop.ps1" start
echo.
pause
