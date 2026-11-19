# Start the whole SoShop stack for local development on Windows.
#
#   powershell -ExecutionPolicy Bypass -File .\dev.ps1
#
# Brings up Postgres and LiveKit in Docker (creating the containers on first
# run), then opens three windows: the API (:8000), the agent worker and the web
# app (:3000). Close a window to stop that process. Safe to run again: it only
# starts what is not already running.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-Port([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-Port([int]$port, [int]$seconds, [string]$name) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Port $port) { return }
        Start-Sleep -Seconds 1
    }
    throw "$name did not come up on port $port within $seconds s."
}

# --- Docker -----------------------------------------------------------------
# Probe through cmd.exe: under $ErrorActionPreference = "Stop", Windows
# PowerShell 5.1 turns docker's stderr into a terminating error, which would
# abort the script exactly when Docker is down.
function Test-Docker {
    cmd /c "docker info >nul 2>&1"
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Docker)) {
    $desktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { throw "Docker is not running and Docker Desktop was not found." }
    Write-Host "Starting Docker Desktop..."
    Start-Process $desktop
    $up = $false
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Docker) { $up = $true; break }
    }
    if (-not $up) { throw "Docker did not start." }
}

$existing = docker ps -a --format "{{.Names}}"

if ($existing -contains "soshop-postgres") {
    docker start soshop-postgres | Out-Null
} else {
    Write-Host "Creating soshop-postgres (first run: apply server/migrations/*.up.sql and seed afterwards)."
    docker run -d --name soshop-postgres --restart unless-stopped `
        -e POSTGRES_USER=soshop -e POSTGRES_PASSWORD=soshop -e POSTGRES_DB=soshop `
        -p 5436:5432 postgres:16-alpine | Out-Null
}

# LiveKit must advertise an address the browser can reach. Inside Docker its
# default is the container IP (172.17.x.x), which makes every browser viewer
# fail WebRTC while signaling looks fine. --node-ip 127.0.0.1 plus the mapped
# RTC ports (7881/tcp, 7882/udp) fixes that for local use.
if ($existing -contains "soshop-livekit") {
    docker start soshop-livekit | Out-Null
} else {
    docker run -d --name soshop-livekit --restart unless-stopped `
        -p 7880:7880 -p 7881:7881 -p 7882:7882/udp `
        livekit/livekit-server --dev --bind 0.0.0.0 --node-ip 127.0.0.1 | Out-Null
}

Wait-Port 5436 60 "Postgres"
Wait-Port 7880 60 "LiveKit"

# --- App processes ------------------------------------------------------------
function Start-Window([string]$title, [string]$dir, [string]$command) {
    $script = "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location '$dir'; $command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $script | Out-Null
}

if (Test-Port 8000) {
    Write-Host "API already running on :8000"
} else {
    Start-Window "SoShop API" "$root\server" "uv run --group agent uvicorn soshop_server.api.app:app --port 8000"
}

# The worker registers with LiveKit over a websocket. It does not survive a
# LiveKit restart cleanly, so it is always started fresh after LiveKit is up.
if (Test-Port 8081) {
    Write-Host "Agent worker already running (its status port :8081 is open)"
} else {
    Start-Window "SoShop agent worker" "$root\server" "uv run --group agent python -m soshop_server.agent.main start"
}

if (Test-Port 3000) {
    Write-Host "Web already running on :3000"
} else {
    # `npm run dev` uses a POSIX env prefix that cmd.exe rejects, so call next directly.
    Start-Window "SoShop web" "$root\web" "npx next dev"
}

Wait-Port 8000 90 "API"
Wait-Port 3000 120 "Web"
Write-Host ""
Write-Host "Studio: http://localhost:3000/studio"
Start-Process "http://localhost:3000/studio"
