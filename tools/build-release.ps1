# Build the SoShop release: prebuilt images on a registry plus a small zip for
# end users that holds no source code.
#
#   powershell -ExecutionPolicy Bypass -File tools\build-release.ps1 -Version 1.0.0
#   ... -NoPush        build and zip only (images stay on this machine)
#   ... -Offline       put every image inside the bundle (images\soshop-images.tar,
#                      a few GB): no registry, no token, the other machine
#                      needs no fast internet. Implies -NoPush.
#   ... -Registry ghcr.io/<owner>
#
# Pushing to GHCR needs a one-time login with a GitHub token that has the
# write:packages scope:
#   docker login ghcr.io -u <github user>
#
# Output: dist\SoShop-<version>.zip with docker-compose.yml (image names
# pinned to this version, no build sections), the launchers, the settings
# template, the Vietnamese guide and the Chrome extension. The server image
# carries our Python code as bytecode only (Dockerfile.local, STRIP_SOURCE=1).

param(
    [string]$Version = (Get-Date -Format "yyyy.MM.dd-HHmm"),
    [string]$Registry = "ghcr.io/letuan03",
    [switch]$NoPush,
    [switch]$Offline
)
if ($Offline) { $NoPush = $true }

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Step([string]$text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail([string]$text) { Write-Host $text -ForegroundColor Red; exit 1 }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed." } }

$Registry = $Registry.TrimEnd("/").ToLower()
$server = "$Registry/soshop-server"
$web = "$Registry/soshop-web"

Step "Building $server`:$Version (bytecode only)"
docker build -f server/Dockerfile.local --build-arg STRIP_SOURCE=1 `
    -t "$server`:$Version" -t "$server`:latest" server
Check "Server image build"

Step "Building $web`:$Version"
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 `
    -t "$web`:$Version" -t "$web`:latest" web
Check "Web image build"

Step "Checking that no Python source is left in the server image"
$left = docker run --rm --entrypoint sh "$server`:$Version" -c "find /app/src /app/scripts -name '*.py' | wc -l"
Check "Source check"
if ([int]$left -ne 0) { Fail "$left .py files are still in the server image." }

if (-not $NoPush) {
    foreach ($tag in @("$server`:$Version", "$server`:latest", "$web`:$Version", "$web`:latest")) {
        Step "Pushing $tag"
        docker push $tag
        Check "Push of $tag (run 'docker login $($Registry.Split('/')[0])' first)"
    }
}

Step "Making the user bundle"
$name = "SoShop-$Version"
$dist = Join-Path $root "dist"
$out = Join-Path $dist $name
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force (Join-Path $out "tools") | Out-Null

$compose = Get-Content docker-compose.yml -Raw -Encoding UTF8
$compose = $compose.Replace("soshop-server:local", "$server`:$Version").Replace("soshop-web:local", "$web`:$Version")
[System.IO.File]::WriteAllText((Join-Path $out "docker-compose.yml"), $compose, (New-Object System.Text.UTF8Encoding $false))

Copy-Item soshop.env.example, soshop-start.cmd, soshop-stop.cmd, HUONG-DAN.md $out
Copy-Item tools\soshop.ps1 (Join-Path $out "tools")
Copy-Item -Recurse server\flow_extension (Join-Path $out "flow_extension")

if ($Offline) {
    # Every image the bundle runs, third-party ones too, so it works offline.
    $images = @($compose -split "`n" | Where-Object { $_ -match "^\s*image:\s*(\S+)" } | ForEach-Object { $Matches[1] }) |
        Select-Object -Unique
    foreach ($img in $images) {
        if (-not (docker images -q $img 2>$null)) { Step "Pulling $img"; docker pull $img; Check "Pull of $img" }
    }
    Step "Saving images into the bundle (a few GB, takes a while)"
    New-Item -ItemType Directory -Force (Join-Path $out "images") | Out-Null
    docker save -o (Join-Path $out "images\soshop-images.tar") @images
    Check "docker save"
}

# tar.exe (bsdtar, built into Windows 10+) writes zip64, so a multi-GB offline
# bundle works; Compress-Archive in Windows PowerShell stops at 2 GB.
$zip = Join-Path $dist "$name.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
& tar.exe -a -c -f $zip -C $dist $name
Check "Zip"

Write-Host ""
Write-Host "Done: $zip" -ForegroundColor Green
if ($Offline) {
    Write-Host "Send the zip (or copy the folder $out). It holds the images, no registry needed."
} elseif ($NoPush) {
    Write-Host "Images were not pushed (-NoPush). Users can only run this bundle on this machine." -ForegroundColor Yellow
} else {
    Write-Host "Images: $server`:$Version, $web`:$Version"
    Write-Host "Private package? Give users a GitHub token with only the read:packages scope;"
    Write-Host "soshop-start.cmd asks for it once."
}
