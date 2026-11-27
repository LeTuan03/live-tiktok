# Builds the SoShop Windows desktop app: dist\desktop\SoShop-Setup-<version>.exe,
# one file that installs everything (Python and its packages, Postgres, LiveKit,
# Node, ffmpeg, the server as bytecode and the built web app). The machine that
# installs it needs nothing else: no Docker, no Python, no admin rights.
#
#   powershell -ExecutionPolicy Bypass -File desktop\build-app.ps1 -Version 1.0.0
#
#   -SkipWeb / -SkipPython   reuse that part of the previous stage (faster rebuilds)
#   -NoSetup                 stop after staging dist\desktop\SoShop (run SoShop.exe there)
#
# Needs on this machine: uv (server/), Node + the web/ node_modules, the .NET SDK
# (builds the two small exes, which target .NET Framework 4.7.2), ffmpeg on PATH,
# and internet for the first build (Postgres and LiveKit downloads are cached in
# desktop\.cache). See desktop\README.md.

param(
    [string]$Version = (Get-Date -Format "yyyy.M.d"),
    [switch]$SkipWeb,
    [switch]$SkipPython,
    [switch]$NoSetup
)

# Native tools (npx, uv) write progress to stderr, which Windows PowerShell turns
# into errors when output is redirected, so their exit codes are checked instead
# (Check). Cmdlets still stop the script on any error.
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues["*:ErrorAction"] = "Stop"
$ProgressPreference = "SilentlyContinue"
$desktop = $PSScriptRoot
$root = Split-Path -Parent $desktop
$cache = Join-Path $desktop ".cache"
$out = Join-Path $root "dist\desktop"
$stage = Join-Path $out "SoShop"     # exactly what gets installed
$app = Join-Path $stage "app"

$PgVersion = "16.10-1"
$LiveKitVersion = "1.13.11"
$PythonVersion = "3.11"

function Step([string]$text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail([string]$text) { Write-Host $text -ForegroundColor Red; exit 1 }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE)." } }

# robocopy exit codes below 8 mean success.
function Mirror([string]$from, [string]$to, [string[]]$extra = @()) {
    & robocopy $from $to /MIR /NFL /NDL /NJH /NJS /NP /R:2 /W:1 @extra | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "Copy $from -> $to failed (robocopy $LASTEXITCODE)." }
    $global:LASTEXITCODE = 0
}

function Get-Download([string]$url, [string]$file) {
    if (Test-Path $file) { return }
    Step "Downloading $url"
    New-Item -ItemType Directory -Force (Split-Path $file) | Out-Null
    & curl.exe -L --fail --retry 3 -o "$file.partial" $url
    Check "Download of $url"
    Move-Item -Force "$file.partial" $file
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

# Unzips the entries under $prefix (e.g. "pgsql/bin/") into $to, dropping the prefix.
function Expand-Part([string]$zipFile, [string]$prefix, [string]$to, [string[]]$skip = @()) {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipFile)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace("\", "/")
            if (-not $name.StartsWith($prefix) -or $name.EndsWith("/")) { continue }
            $rel = $name.Substring($prefix.Length)
            if ($skip | Where-Object { $rel -like $_ }) { continue }
            $target = Join-Path $to $rel.Replace("/", "\")
            New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally { $zip.Dispose() }
}

# Zips a folder with "/" separators (PowerShell 5.1's own zip helpers may write
# "\", which other zip readers reject). Media and archives are stored, not deflated.
Add-Type -ReferencedAssemblies System.IO.Compression, System.IO.Compression.FileSystem -TypeDefinition @"
using System;
using System.IO;
using System.IO.Compression;
public static class SoShopPack {
    public static long Pack(string dir, string zipPath) {
        var stored = new[] { ".zip", ".gz", ".7z", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mp3", ".woff2" };
        var root = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
        long total = 0;
        using (var zip = ZipFile.Open(zipPath, ZipArchiveMode.Create)) {
            foreach (var file in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)) {
                var name = file.Substring(root.Length).Replace('\\', '/');
                var ext = Path.GetExtension(file).ToLowerInvariant();
                var level = Array.IndexOf(stored, ext) >= 0 ? CompressionLevel.NoCompression : CompressionLevel.Optimal;
                zip.CreateEntryFromFile(file, name, level);
                total += new FileInfo(file).Length;
            }
        }
        return total;
    }
}
"@

New-Item -ItemType Directory -Force $app, $cache | Out-Null

# --- Python, its packages and the server ------------------------------------------
if (-not $SkipPython) {
    Step "Python $PythonVersion (relocatable build from uv)"
    $python = (& uv python find $PythonVersion | Select-Object -First 1)
    if (-not $python) { & uv python install $PythonVersion; $python = (& uv python find $PythonVersion | Select-Object -First 1) }
    $pythonHome = Split-Path $python.Trim()
    $py = Join-Path $app "python"
    Mirror $pythonHome $py @("/XD", "__pycache__")
    # Not used at run time; the test suite alone is ~30 MB.
    foreach ($part in @("include", "libs", "tcl", "Lib\test", "Lib\idlelib", "Lib\tkinter", "Lib\turtledemo", "Lib\ensurepip")) {
        $p = Join-Path $py $part
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
    Remove-Item -Force (Join-Path $py "Lib\EXTERNALLY-MANAGED") -ErrorAction SilentlyContinue
    # The C++ runtime that OpenVINO, OpenCV and onnxruntime need, in case the
    # target machine never installed the Visual C++ Redistributable.
    foreach ($dll in @("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll")) {
        $src = Join-Path $env:WINDIR "System32\$dll"
        if ((Test-Path $src) -and -not (Test-Path (Join-Path $py $dll))) { Copy-Item $src $py }
    }

    Step "Server packages (runtime and agent groups, no dev tools)"
    $req = Join-Path $cache "requirements.txt"
    & uv export --project (Join-Path $root "server") --frozen --no-dev --group agent --no-emit-project --no-hashes --quiet -o $req
    Check "uv export"
    $env:UV_LINK_MODE = "copy"
    & uv pip install --python (Join-Path $py "python.exe") --break-system-packages --quiet -r $req
    Check "uv pip install"
    Remove-Item Env:UV_LINK_MODE

    Step "Server code (bytecode only)"
    $srv = Join-Path $app "server"
    foreach ($part in @("src", "migrations", "seed", "scripts")) {
        Mirror (Join-Path $root "server\$part") (Join-Path $srv $part) @("/XD", "__pycache__", "/XF", "*.pyc")
    }
    & (Join-Path $py "python.exe") -m compileall -q -b -f (Join-Path $srv "src") (Join-Path $srv "scripts")
    Check "compileall"
    Get-ChildItem (Join-Path $srv "src"), (Join-Path $srv "scripts") -Recurse -Filter *.py | Remove-Item -Force
    Get-ChildItem (Join-Path $srv "src"), (Join-Path $srv "scripts") -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

    Step "Checking that the server imports from the bundle"
    $env:PYTHONPATH = Join-Path $srv "src"
    & (Join-Path $py "python.exe") -c "import soshop_server.api.app, soshop.agent.main, soshop_server.db.migrate; print('imports ok')"
    $code = $LASTEXITCODE
    Remove-Item Env:PYTHONPATH
    if ($code -ne 0) { Fail "The bundled server does not import." }
}

# --- Web app ------------------------------------------------------------------------
if (-not $SkipWeb) {
    # Built in a copy of web\ with a clean `npm ci`: the release does not depend on
    # the state of the developer's node_modules, and building never disturbs a
    # running `next dev`. Local .env files stay behind so no NEXT_PUBLIC_* value
    # from this machine is baked in.
    Step "Web app (npm ci and next build in a clean copy)"
    $webBuild = Join-Path $out "web-build"
    Mirror (Join-Path $root "web") $webBuild @("/XD", "node_modules", ".next", "test-results", "playwright-report", "/XF", ".env*")
    Push-Location $webBuild
    try {
        # --os/--cpu: pick Windows native packages (SWC, esbuild) even when ~/.npmrc says otherwise.
        & npm ci --no-audit --no-fund --loglevel=error --os=win32 --cpu=x64
        Check "npm ci"
        # The browser reaches the API on 127.0.0.1: the launcher binds every service there.
        $env:NEXT_OUTPUT = "standalone"
        $env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000"
        $env:NEXT_TELEMETRY_DISABLED = "1"
        & node "node_modules\next\dist\bin\next" build
        Check "next build"
    } finally {
        Remove-Item Env:NEXT_OUTPUT, Env:NEXT_PUBLIC_API_BASE_URL -ErrorAction SilentlyContinue
        Pop-Location
    }
    $web = Join-Path $app "web"
    Mirror (Join-Path $webBuild ".next\standalone") $web
    Mirror (Join-Path $webBuild ".next\static") (Join-Path $web ".next\static")
    Mirror (Join-Path $webBuild "public") (Join-Path $web "public")
    # Never ship local secrets (.env.local) that Next may copy next to server.js.
    Get-ChildItem $web -Force -Filter ".env*" | Remove-Item -Force
}

# --- Node, Postgres, LiveKit, ffmpeg --------------------------------------------------
Step "Node.js runtime"
$node = (Get-Command node).Source
New-Item -ItemType Directory -Force (Join-Path $app "node") | Out-Null
Copy-Item -Force $node (Join-Path $app "node\node.exe")
Write-Host ("node " + (& (Join-Path $app "node\node.exe") -v))

$pgZip = Join-Path $cache "postgresql-$PgVersion-windows-x64-binaries.zip"
Get-Download "https://get.enterprisedb.com/postgresql/postgresql-$PgVersion-windows-x64-binaries.zip" $pgZip
$pg = Join-Path $app "pgsql"
if (-not (Test-Path (Join-Path $pg "bin\pg_ctl.exe"))) {
    Step "Postgres $PgVersion (server and client tools only)"
    foreach ($part in @("bin", "lib", "share")) {
        Expand-Part $pgZip "pgsql/$part/" (Join-Path $pg $part) @("*.pdb", "*.lib", "*.a", "pgxs/*")
    }
}

$lkZip = Join-Path $cache "livekit_${LiveKitVersion}_windows_amd64.zip"
Get-Download "https://github.com/livekit/livekit/releases/download/v$LiveKitVersion/livekit_${LiveKitVersion}_windows_amd64.zip" $lkZip
Step "LiveKit $LiveKitVersion"
New-Item -ItemType Directory -Force (Join-Path $app "livekit") | Out-Null
$zip = [System.IO.Compression.ZipFile]::OpenRead($lkZip)
try {
    $exe = $zip.Entries | Where-Object { $_.Name -eq "livekit-server.exe" } | Select-Object -First 1
    if (-not $exe) { Fail "livekit-server.exe is not in $lkZip" }
    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($exe, (Join-Path $app "livekit\livekit-server.exe"), $true)
} finally { $zip.Dispose() }

Step "ffmpeg"
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) { Fail "ffmpeg is not on PATH. Install a static build (e.g. gyan.dev) and rerun." }
$ff = Join-Path $app "ffmpeg"
New-Item -ItemType Directory -Force $ff | Out-Null
Copy-Item -Force $ffmpeg (Join-Path $ff "ffmpeg.exe")
# A shared (non-static) build keeps its libraries next to the exe.
Get-ChildItem (Split-Path $ffmpeg) -Filter *.dll -ErrorAction SilentlyContinue | Copy-Item -Destination $ff -Force
Write-Host ((& (Join-Path $ff "ffmpeg.exe") -hide_banner -version | Select-Object -First 1))

Step "Chrome extension, settings template, version"
Mirror (Join-Path $root "server\flow_extension") (Join-Path $app "flow_extension")
Copy-Item -Force (Join-Path $root "soshop.env.example") (Join-Path $app "soshop.env.example")
Set-Content -Path (Join-Path $app "version.txt") -Value $Version -Encoding ASCII -NoNewline

# --- The two exes -------------------------------------------------------------------------
Step "SoShop.exe (launcher)"
$bin = Join-Path $out "bin"
& dotnet build (Join-Path $desktop "launcher\SoShop.csproj") -c Release -nologo -v q "-p:Version=$($Version -replace '[^0-9.]', '')" -o (Join-Path $bin "launcher")
Check "Launcher build"
Copy-Item -Force (Join-Path $bin "launcher\SoShop.exe") (Join-Path $stage "SoShop.exe")

$longest = Get-ChildItem $stage -Recurse -File | ForEach-Object { $_.FullName.Length - $stage.Length } | Measure-Object -Maximum
Write-Host ("Longest path inside the install folder: {0} characters" -f $longest.Maximum)
if ($longest.Maximum -gt 190) { Write-Host "Warning: deep paths; installs under long folder names may hit the 260-character limit." -ForegroundColor Yellow }

$size = (Get-ChildItem $stage -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("Staged {0:N0} MB in {1}" -f ($size / 1MB), $stage)
if ($NoSetup) { Write-Host "Run $stage\SoShop.exe to try it." -ForegroundColor Green; exit 0 }

Step "Packing (a few minutes)"
$payload = Join-Path $out "payload.zip"
if (Test-Path $payload) { Remove-Item -Force $payload }
[void][SoShopPack]::Pack($stage, $payload)
Write-Host ("payload.zip: {0:N0} MB" -f ((Get-Item $payload).Length / 1MB))

Step "SoShop-Setup.exe"
& dotnet build (Join-Path $desktop "setup\SoShopSetup.csproj") -c Release -nologo -v q "-p:Version=$($Version -replace '[^0-9.]', '')" -o (Join-Path $bin "setup")
Check "Setup build"
$setup = Join-Path $out "SoShop-Setup-$Version.exe"
Copy-Item -Force (Join-Path $bin "setup\SoShop-Setup.exe") $setup
# [setup exe][payload zip][zip offset, 8 bytes][magic "SOSHOPZ1"]; see setup/Setup.cs.
$fs = [System.IO.File]::Open($setup, [System.IO.FileMode]::Append)
try {
    $offset = $fs.Length
    $src = [System.IO.File]::OpenRead($payload)
    try { $src.CopyTo($fs) } finally { $src.Dispose() }
    $bw = New-Object System.IO.BinaryWriter $fs
    $bw.Write([Int64]$offset)
    $bw.Write([System.Text.Encoding]::ASCII.GetBytes("SOSHOPZ1"))
    $bw.Flush()
} finally { $fs.Dispose() }
Remove-Item -Force $payload

Write-Host ""
Write-Host ("Done: {0} ({1:N0} MB)" -f $setup, ((Get-Item $setup).Length / 1MB)) -ForegroundColor Green
Write-Host "Send that one file. The other machine double-clicks it; nothing else to install."
