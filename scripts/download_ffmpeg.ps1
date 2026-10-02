# Tải FFmpeg (bản essentials, gyan.dev) về tools\ffmpeg để LiveFlow đọc metadata video.
# Chạy: powershell -ExecutionPolicy Bypass -File scripts\download_ffmpeg.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$toolsDir = Join-Path $root "tools\ffmpeg"
$zipPath = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
$url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

Write-Host "[..] Tai FFmpeg tu $url ..."
New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
} catch {
    Write-Host "[LOI] Khong tai duoc FFmpeg: $($_.Exception.Message)"
    Write-Host "      Cach khac: winget install Gyan.FFmpeg  (roi mo lai ung dung)"
    exit 1
}

Write-Host "[..] Giai nen ..."
Expand-Archive -Path $zipPath -DestinationPath $toolsDir -Force
Remove-Item $zipPath -Force

$probe = Get-ChildItem -Path $toolsDir -Recurse -Filter ffprobe.exe | Select-Object -First 1
if ($probe) {
    Write-Host "[OK] FFmpeg san sang: $($probe.DirectoryName)"
    Write-Host "     LiveFlow se tu nhan dien thu muc nay khi khoi dong."
} else {
    Write-Host "[LOI] Khong tim thay ffprobe.exe sau khi giai nen."
    exit 1
}
