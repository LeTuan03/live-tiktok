@echo off
setlocal EnableExtensions EnableDelayedExpansion
title LiveFlow AI Studio - Cai dat
cd /d "%~dp0"

echo ============================================================
echo   LiveFlow AI Studio - CAI DAT (lan dau)
echo ============================================================
echo.

REM --- 1. Tim Python 3.12+ ---
set "PY="
for %%V in (3.13 3.12 3.14) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [LOI] Khong tim thay Python 3.12 tro len.
    echo        Tai tai https://www.python.org/downloads/windows/  ^(tich "Add python.exe to PATH" khi cai^)
    echo.
    pause
    exit /b 1
)
echo [OK] Python: !PY!

REM --- 2. Tao moi truong ao ---
if not exist "venv\Scripts\python.exe" (
    echo [..] Dang tao moi truong ao venv ...
    !PY! -m venv venv
    if errorlevel 1 (
        echo [LOI] Khong tao duoc venv.
        pause
        exit /b 1
    )
)
echo [OK] venv san sang

REM --- 3. Cai thu vien ---
echo [..] Dang cai thu vien (can Internet, 2-5 phut)...
"venv\Scripts\python.exe" -m pip install --upgrade pip >nul
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [LOI] Cai thu vien that bai. Kiem tra Internet va chay lai INSTALL.bat
    pause
    exit /b 1
)
echo [OK] Da cai thu vien

REM --- 4. Tao .env tu mau neu chua co ---
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo [OK] Da tao .env tu .env.example ^(co the bo trong, cau hinh trong ung dung^)
)

REM --- 5. FFmpeg (tuy chon) ---
where ffprobe >nul 2>&1
if errorlevel 1 (
    if not exist "tools\ffmpeg" (
        echo.
        echo [?] FFmpeg chua co. FFmpeg dung de doc thoi luong/do phan giai video ^(khong bat buoc^).
        set /p DL="    Tai FFmpeg tu dong ve thu muc tools\ffmpeg? (Y/N): "
        if /i "!DL!"=="Y" (
            powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\download_ffmpeg.ps1"
        )
    )
) else (
    echo [OK] FFmpeg da co trong PATH
)

echo.
echo ============================================================
echo   CAI DAT XONG. Chay RUN.bat de mo LiveFlow AI Studio.
echo   Buoc tiep theo trong ung dung: Cai dat -^> nhap Gemini API Key.
echo ============================================================
echo.
pause
endlocal
