@echo off
setlocal
title LiveFlow AI Studio - Build EXE
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo [!] Chua cai dat. Chay INSTALL.bat truoc.
    pause
    exit /b 1
)
echo [..] Cai PyInstaller ...
"venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
echo [..] Dong goi ... (3-10 phut)
"venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean liveflow.spec
if errorlevel 1 (
    echo [LOI] Build that bai.
    pause
    exit /b 1
)
echo.
echo [OK] Ban build nam o: dist\LiveFlowAIStudio\LiveFlowAIStudio.exe
echo      Copy ca thu muc dist\LiveFlowAIStudio sang may khac la chay duoc (khong can Python).
echo      Muon tao bo cai .exe: cai Inno Setup roi mo scripts\installer.iss va bam Compile.
pause
endlocal
