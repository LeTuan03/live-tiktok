@echo off
setlocal
cd /d "%~dp0\.."
if not exist "venv\Scripts\python.exe" (
    echo [!] Chua cai dat. Chay INSTALL.bat truoc.
    pause
    exit /b 1
)
"venv\Scripts\python.exe" -m pip install -q -r requirements-dev.txt
"venv\Scripts\python.exe" -m pytest -q
pause
endlocal
