@echo off
setlocal
title LiveFlow AI Studio
cd /d "%~dp0"

set "PY_EXE=venv\Scripts\python.exe"
set "PYW_EXE=venv\Scripts\pythonw.exe"
if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=.venv\Scripts\python.exe"
    set "PYW_EXE=.venv\Scripts\pythonw.exe"
)
if not exist "%PY_EXE%" (
    echo [!] Chua cai dat. Dang chay INSTALL.bat ...
    call INSTALL.bat
    if not exist "%PY_EXE%" exit /b 1
)

REM Them tools\ffmpeg vao PATH neu da tai ve
if exist "tools\ffmpeg" (
    for /d %%D in ("tools\ffmpeg\*") do if exist "%%D\bin\ffprobe.exe" set "PATH=%%D\bin;%PATH%"
    if exist "tools\ffmpeg\bin\ffprobe.exe" set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
)

REM Use Windows Media Foundation for smooth, warning-free video decoding
set "QT_MEDIA_BACKEND=windows"

start "" "%PYW_EXE%" -m app.main
endlocal
