@echo off
setlocal
title LiveFlow AI Studio (debug)
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo [!] Chua cai dat. Chay INSTALL.bat truoc.
    pause
    exit /b 1
)
if exist "tools\ffmpeg" (
    for /d %%D in ("tools\ffmpeg\*") do if exist "%%D\bin\ffprobe.exe" set "PATH=%%D\bin;%PATH%"
    if exist "tools\ffmpeg\bin\ffprobe.exe" set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
)
set LIVEFLOW_DEBUG=true
"venv\Scripts\python.exe" -m app.main
echo.
echo Ung dung da thoat. Log: %LOCALAPPDATA%\LiveFlowAIStudio\logs
pause
endlocal
