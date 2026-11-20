# SoShop launcher for the Docker bundle (docker-compose.yml).
#
#   soshop-start.cmd                         double-click: start everything
#   soshop-stop.cmd                          double-click: stop everything
#   powershell -File tools\soshop.ps1 logs   follow the logs
#   powershell -File tools\soshop.ps1 status show what is running
#
# Messages are in Vietnamese for end users. Keep this file saved as UTF-8 with
# BOM: Windows PowerShell 5.1 reads BOM-less scripts as ANSI and garbles them.

param(
    [ValidateSet("start", "stop", "logs", "status")]
    [string]$Action = "start"
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$envFile = Join-Path $root "soshop.env"
$envExample = Join-Path $root "soshop.env.example"
$studioUrl = "http://localhost:3000/studio"

function Say([string]$text, [string]$color = "Gray") { Write-Host $text -ForegroundColor $color }
function Fail([string]$text) { Say ""; Say $text "Red"; exit 1 }

function Test-Port([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", $port); return $true } catch { return $false } finally { $client.Close() }
}

function Test-Url([string]$url) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -lt 500
    } catch { return $false }
}

# --- Docker ---------------------------------------------------------------------
function Test-Docker {
    cmd /c "docker info >nul 2>&1"
    return $LASTEXITCODE -eq 0
}

function Initialize-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Say "Chưa cài Docker Desktop. Đang mở trang tải về..." "Yellow"
        Start-Process "https://www.docker.com/products/docker-desktop/"
        Fail "Cài Docker Desktop, mở nó lên một lần, rồi chạy lại soshop-start.cmd."
    }
    if (Test-Docker) { return }

    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { Fail "Docker chưa chạy. Hãy mở Docker Desktop rồi chạy lại." }
    Say "Đang khởi động Docker Desktop (có thể mất 1-2 phút)..."
    Start-Process $desktop
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Docker) { return }
    }
    Fail "Docker Desktop không khởi động được. Hãy mở nó bằng tay rồi chạy lại."
}

# --- soshop.env -----------------------------------------------------------------
function Get-EnvValue([string]$name) {
    $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return ($line -replace "^\s*$name\s*=", "").Trim()
}

function Set-EnvValue([string]$name, [string]$value) {
    $lines = Get-Content $envFile -Encoding UTF8
    $found = $false
    $lines = $lines | ForEach-Object {
        if ($_ -match "^\s*$name\s*=") { $found = $true; "$name=$value" } else { $_ }
    }
    if (-not $found) { $lines += "$name=$value" }
    # UTF-8 without BOM so Docker reads the first key correctly.
    [System.IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding $false))
}

function Initialize-Settings {
    if (-not (Test-Path $envFile)) {
        Copy-Item $envExample $envFile
        Say "Đã tạo file cấu hình soshop.env." "Green"
    }
    if (-not (Get-EnvValue "GROQ_API_KEY")) {
        Say ""
        Say "SoShop cần một Groq API key (miễn phí) để AI dẫn live và viết kịch bản." "Yellow"
        Say "Lấy key tại: https://console.groq.com/keys"
        $key = Read-Host "Dán Groq API key vào đây rồi bấm Enter (bỏ trống để nhập sau)"
        if ($key) {
            Set-EnvValue "GROQ_API_KEY" $key.Trim()
            Say "Đã lưu key vào soshop.env." "Green"
        } else {
            Say "Chưa có key: giao diện vẫn chạy nhưng AI sẽ không nói được. Mở soshop.env để thêm sau." "Yellow"
        }
    }
}

# --- Ports ----------------------------------------------------------------------
function Test-StackRunning {
    $id = docker compose ps -q --status running net 2>$null
    return [bool]$id
}

function Assert-PortsFree {
    if (Test-StackRunning) { return }
    # The developer setup (dev.ps1) uses these containers and the same LiveKit ports.
    foreach ($name in @("soshop-livekit", "soshop-postgres")) {
        $running = docker ps -q --filter "name=^$name$" 2>$null
        if ($running) {
            Say "Đang dừng container phát triển $name để giải phóng cổng..."
            docker stop $name | Out-Null
        }
    }
    $busy = @(3000, 8000, 7880, 7881) | Where-Object { Test-Port $_ }
    if ($busy) {
        Fail ("Các cổng sau đang bị chương trình khác dùng: " + ($busy -join ", ") +
            ". Hãy tắt chương trình đó (ví dụ một bản SoShop khác đang chạy) rồi thử lại.")
    }
}

# --- Release images -------------------------------------------------------------
# A private registry (e.g. a private GHCR package) needs a login once; Docker
# remembers it afterwards.
function Get-Images {
    # Offline bundle (build-release.ps1 -Offline): the images ship in the folder.
    $tar = Join-Path $root "images\soshop-images.tar"
    if (Test-Path $tar) {
        Say "Đang cài bộ cài có sẵn trong thư mục (vài phút)..."
        docker load -i $tar
        if ($LASTEXITCODE -ne 0) { Fail "Không đọc được images\soshop-images.tar. File có thể bị hỏng khi sao chép." }
        return
    }

    docker compose pull
    if ($LASTEXITCODE -eq 0) { return }

    $image = (docker compose config --images 2>$null | Where-Object { $_ -match "/" } | Select-Object -First 1)
    $registry = ($image -split "/")[0]
    $owner = ($image -split "/")[1]
    Say ""
    Say "Không tải được bộ cài từ $registry. Nếu đây là bản riêng tư, hãy nhập token được cấp." "Yellow"
    $token = Read-Host "Token (bỏ trống để thoát)"
    if (-not $token) { Fail "Không có token, dừng lại. Kiểm tra mạng rồi chạy lại." }
    $token.Trim() | docker login $registry -u $owner --password-stdin
    if ($LASTEXITCODE -ne 0) { Fail "Token không đúng hoặc đã hết hạn. Hãy xin token mới." }
    docker compose pull
    if ($LASTEXITCODE -ne 0) { Fail "Vẫn không tải được bộ cài. Xem lỗi ở trên." }
}

# --- Actions --------------------------------------------------------------------
function Start-SoShop {
    Say "=== SoShop ===" "Cyan"
    Initialize-Docker
    Initialize-Settings
    Assert-PortsFree

    # The source repo has docker-compose.override.yml (build from source); the
    # release bundle does not and pulls prebuilt images instead.
    $fromSource = Test-Path (Join-Path $root "docker-compose.override.yml")
    $missing = @(docker compose config --images 2>$null | Where-Object {
        $_ -and -not (docker images -q $_ 2>$null)
    })
    if ($missing) {
        Say ""
        Say "Lần chạy đầu: đang tải và cài đặt (khoảng 10-20 phút tùy mạng). Các lần sau chỉ vài giây." "Yellow"
    }
    Say ""
    if ($fromSource) {
        docker compose up -d --build
    } else {
        if ($missing) { Get-Images }
        docker compose up -d
    }
    if ($LASTEXITCODE -ne 0) { Fail "Khởi động thất bại. Xem lỗi ở trên, hoặc chạy: powershell -File tools\soshop.ps1 logs" }

    Say ""
    Say "Đang chờ SoShop sẵn sàng..."
    $ready = $false
    for ($i = 0; $i -lt 150; $i++) {
        if ((Test-Url "http://localhost:8000/healthz") -and (Test-Url "http://localhost:3000")) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { Fail "SoShop chưa phản hồi sau 5 phút. Xem nhật ký: powershell -File tools\soshop.ps1 logs" }

    Say ""
    Say "SoShop đã chạy!" "Green"
    Say "  Studio:      $studioUrl"
    Say "  Feed:        http://localhost:3000"
    Say "  Dừng:        bấm đúp soshop-stop.cmd"
    Say ""
    Say "Lần đầu mở phiên live, máy sẽ tải thêm model avatar (khoảng 900 MB) nên có thể chờ vài phút."
    Start-Process $studioUrl
}

switch ($Action) {
    "start" { Start-SoShop }
    "stop" {
        Initialize-Docker
        docker compose down
        Say "Đã dừng SoShop. Dữ liệu vẫn được giữ lại." "Green"
    }
    "logs" { docker compose logs -f --tail 100 }
    "status" { docker compose ps }
}
