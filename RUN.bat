@echo off
setlocal
title LiveFlow AI Studio
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [!] Chua cai dat. Dang chay INSTALL.bat ...
    call INSTALL.bat
    if not exist "venv\Scripts\python.exe" exit /b 1
)

REM Them tools\ffmpeg vao PATH neu da tai ve
if exist "tools\ffmpeg" (
    for /d %%D in ("tools\ffmpeg\*") do if exist "%%D\bin\ffprobe.exe" set "PATH=%%D\bin;%PATH%"
    if exist "tools\ffmpeg\bin\ffprobe.exe" set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
)

start "" "venv\Scripts\pythonw.exe" -m app.main
endlocal
