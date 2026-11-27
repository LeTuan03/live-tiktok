# Packs the SoShop data of this machine (products, Flow videos, live sessions,
# uploads) into one zip that the desktop app restores with its
# "Khoi phuc du lieu" button. Same format as the app's own backups
# (desktop/launcher/DataBackup.cs): manifest.json, db.dump, data/...
#
#   powershell -ExecutionPolicy Bypass -File tools\export-data.ps1                # developer setup (dev.ps1)
#   powershell -ExecutionPolicy Bypass -File tools\export-data.ps1 -From docker   # Docker bundle (soshop-start.cmd)
#   ... -Out D:\SoShop-du-lieu.zip
#   ... -Baseline 0006   last migration the database has, when it predates schema_migrations
#                        (detected from its tables when left out)

param(
    [ValidateSet("dev", "docker")][string]$From = "dev",
    [string]$Out = "",
    [string]$Baseline = ""
)

$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Out) {
    $Out = Join-Path ([Environment]::GetFolderPath("MyDocuments")) ("SoShop-du-lieu-" + (Get-Date -Format "yyyy-MM-dd-HHmm") + ".zip")
}
$Out = [System.IO.Path]::GetFullPath($Out)

function Step([string]$text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail([string]$text) { Write-Host $text -ForegroundColor Red; exit 1 }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE)." } }

cmd /c "docker info >nul 2>&1"
if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop and run this again." }

# --- Find the database and the media folder ---------------------------------------------
$work = Join-Path $env:TEMP ("soshop-export-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force (Join-Path $work "data") | Out-Null
Push-Location $root
try {
    if ($From -eq "dev") {
        $db = "soshop-postgres"
        Step "Developer database (container $db)"
        docker start $db | Out-Null
        Check "docker start $db"
        $mediaDir = Join-Path $root "server\data"
    } else {
        Step "Docker bundle database"
        docker compose up -d postgres | Out-Null
        Check "docker compose up postgres"
        $db = (docker compose ps -q postgres | Select-Object -First 1)
        if (-not $db) { Fail "The Docker bundle has no postgres container. Was it ever started?" }
        $mediaDir = $null
    }

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        docker exec $db pg_isready -U soshop -d soshop 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { Fail "Postgres in $db did not become ready." }
    function Sql([string]$query) { @(docker exec $db psql -U soshop -d soshop -tAc $query) | ForEach-Object { "$_".Trim() } }

    # --- Database ---------------------------------------------------------------------------
    Step "Dumping the database"
    docker exec $db pg_dump -U soshop -d soshop -Fc -f /tmp/soshop-export.dump
    Check "pg_dump"
    docker cp "${db}:/tmp/soshop-export.dump" (Join-Path $work "db.dump") | Out-Null
    Check "docker cp"
    docker exec $db rm -f /tmp/soshop-export.dump | Out-Null

    $hasMigrations = (Sql "SELECT to_regclass('public.schema_migrations') IS NOT NULL") -eq "t"
    if (-not $hasMigrations -and -not $Baseline) {
        # Applied by hand before migrate.py existed: the last migration whose tables all exist.
        $tables = Sql "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        foreach ($file in Get-ChildItem (Join-Path $root "server\migrations") -Filter *.up.sql | Sort-Object Name) {
            $created = @([regex]::Matches((Get-Content $file.FullName -Raw -Encoding UTF8),
                    '(?i)create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?"?(\w+)') |
                ForEach-Object { $_.Groups[1].Value.ToLower() })
            if ($created.Count -gt 0 -and @($created | Where-Object { $tables -contains $_ }).Count -eq $created.Count) {
                $Baseline = $file.Name.Split("_")[0]
            }
        }
        if (-not $Baseline) { Fail "Cannot tell which migrations this database has. Pass -Baseline <version>." }
        Write-Host "No schema_migrations table: the schema matches migrations up to $Baseline."
    }

    # --- Media --------------------------------------------------------------------------------
    Step "Copying videos and images"
    $dataOut = Join-Path $work "data"
    if ($From -eq "dev") {
        $skip = @("models", "work", "cache", "tmp", "avatars-v1-backup") | ForEach-Object { Join-Path $mediaDir $_ }
        & robocopy $mediaDir $dataOut /E /NFL /NDL /NJH /NJS /NP /R:2 /W:1 /XD @skip | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "Copying $mediaDir failed (robocopy $LASTEXITCODE)." }
    } else {
        docker compose cp "api:/app/data/." $dataOut | Out-Null
        Check "docker compose cp (is the bundle's api container there?)"
        foreach ($name in @("models", "work", "cache", "tmp")) {
            $p = Join-Path $dataOut $name
            if (Test-Path $p) { Remove-Item -Recurse -Force $p }
        }
    }

    # Media keys the database points at that are not on disk: those pictures and
    # videos will be missing on the other machine too.
    $keys = @(docker exec $db sh -c "pg_dump -U soshop -d soshop --data-only | grep -oE '(uploads|promo)/[A-Za-z0-9._/-]+\.(png|jpe?g|webp|gif|mp4|webm|mov|json)' | sort -u")
    $missing = @($keys | Where-Object { $_ -and -not (Test-Path (Join-Path $dataOut ($_ -replace "/", "\"))) })
} finally {
    Pop-Location
}

$manifest = [ordered]@{ format = 1; app = "SoShop"; source = $From; created = (Get-Date).ToString("o") }
if ($Baseline -and -not $hasMigrations) { $manifest.baseline = $Baseline }
[System.IO.File]::WriteAllText((Join-Path $work "manifest.json"), ($manifest | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))

# --- Zip ("/" separators; media is stored, not deflated) ---------------------------------------
Step "Writing $Out"
Add-Type -ReferencedAssemblies System.IO.Compression, System.IO.Compression.FileSystem -TypeDefinition @"
using System;
using System.IO;
using System.IO.Compression;
public static class SoShopExportPack {
    public static int Pack(string dir, string zipPath) {
        var stored = new[] { ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".mp3", ".m4a", ".zip" };
        var root = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
        int count = 0;
        using (var zip = ZipFile.Open(zipPath, ZipArchiveMode.Create)) {
            foreach (var file in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)) {
                var ext = Path.GetExtension(file).ToLowerInvariant();
                var level = Array.IndexOf(stored, ext) >= 0 ? CompressionLevel.NoCompression : CompressionLevel.Optimal;
                zip.CreateEntryFromFile(file, file.Substring(root.Length).Replace('\\', '/'), level);
                count++;
            }
        }
        return count;
    }
}
"@
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
if (Test-Path $Out) { Remove-Item -Force $Out }
$files = [SoShopExportPack]::Pack($work, $Out)
$mediaCount = @(Get-ChildItem (Join-Path $work "data") -Recurse -File).Count
Remove-Item -Recurse -Force $work

Write-Host ""
Write-Host ("Done: {0} ({1:N1} MB, {2} media files)" -f $Out, ((Get-Item $Out).Length / 1MB), $mediaCount) -ForegroundColor Green
if ($missing.Count -gt 0) {
    Write-Host ("Warning: the database points at {0} media files that are not on this machine, for example:" -f $missing.Count) -ForegroundColor Yellow
    $missing | Select-Object -First 5 | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    Write-Host "Those pictures and videos will be blank after the restore. Re-render or re-upload them." -ForegroundColor Yellow
}
Write-Host "On the other machine: open SoShop, click 'Khoi phuc du lieu' and pick this file."
