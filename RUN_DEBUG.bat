@echo off
setlocal
title LiveFlow AI Studio (debug)
cd /d "%~dp0"
set "PY_EXE=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY_EXE=.venv\Scripts\python.exe"
if not exist "%PY_EXE%" (
    echo [!] Chua cai dat. Chay INSTALL.bat truoc.
    pause
    exit /b 1
)
if exist "tools\ffmpeg" (
    for /d %%D in ("tools\ffmpeg\*") do if exist "%%D\bin\ffprobe.exe" set "PATH=%%D\bin;%PATH%"
    if exist "tools\ffmpeg\bin\ffprobe.exe" set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
)
set LIVEFLOW_DEBUG=true
set "QT_MEDIA_BACKEND=windows"
"%PY_EXE%" -m app.main
echo.
echo Ung dung da thoat. Log: %LOCALAPPDATA%\LiveFlowAIStudio\logs
pause
endlocal
