# Draws desktop/assets/soshop.ico: the brand gradient (web/lib/branding/colors.json)
# in a rounded square with a white "S", at 16 to 256 px, as PNG-in-ICO.
# The .ico is committed; rerun this only to change the icon.
#
#   powershell -ExecutionPolicy Bypass -File desktop\make-icon.ps1

Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = "Stop"
$out = Join-Path $PSScriptRoot "assets\soshop.ico"
New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null

function New-IconPng([int]$size) {
    $bmp = New-Object System.Drawing.Bitmap $size, $size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = "AntiAlias"
    $g.TextRenderingHint = "AntiAliasGridFit"
    $g.Clear([System.Drawing.Color]::Transparent)

    $r = [Math]::Max(2, [int]($size * 0.22))
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $w = $size - 1
    $path.AddArc(0, 0, $r * 2, $r * 2, 180, 90)
    $path.AddArc($w - $r * 2, 0, $r * 2, $r * 2, 270, 90)
    $path.AddArc($w - $r * 2, $w - $r * 2, $r * 2, $r * 2, 0, 90)
    $path.AddArc(0, $w - $r * 2, $r * 2, $r * 2, 90, 90)
    $path.CloseFigure()

    $rect = New-Object System.Drawing.Rectangle 0, 0, $size, $size
    $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, ([System.Drawing.Color]::Black), ([System.Drawing.Color]::Black), 45.0
    $blend = New-Object System.Drawing.Drawing2D.ColorBlend 3
    $blend.Colors = @(
        [System.Drawing.ColorTranslator]::FromHtml("#FF4D6D"),
        [System.Drawing.ColorTranslator]::FromHtml("#C13C9F"),
        [System.Drawing.ColorTranslator]::FromHtml("#5B2FD9"))
    $blend.Positions = @(0.0, 0.5, 1.0)
    $brush.InterpolationColors = $blend
    $g.FillPath($brush, $path)

    $font = New-Object System.Drawing.Font "Segoe UI", ([single]($size * 0.62)), ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
    $format = New-Object System.Drawing.StringFormat
    $format.Alignment = "Center"
    $format.LineAlignment = "Center"
    $textRect = New-Object System.Drawing.RectangleF 0, ([single](-$size * 0.03)), $size, $size
    $g.DrawString("S", $font, [System.Drawing.Brushes]::White, $textRect, $format)

    # A small spark-yellow dot: the "live" light.
    $d = [Math]::Max(3, [int]($size * 0.17))
    $spark = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml("#FFD23F"))
    $g.FillEllipse($spark, [int]($size * 0.70), [int]($size * 0.13), $d, $d)

    $g.Dispose()
    return $bmp
}

# 256 px is stored as PNG (Windows' format for large icons). Smaller sizes are
# 32-bit DIBs, which every Windows and .NET icon reader handles.
function ConvertTo-IconImage([System.Drawing.Bitmap]$bmp) {
    $size = $bmp.Width
    $ms = New-Object System.IO.MemoryStream
    if ($size -ge 256) {
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        return , $ms.ToArray()
    }
    $maskStride = [int]([Math]::Ceiling($size / 32.0)) * 4
    $bw = New-Object System.IO.BinaryWriter $ms
    $bw.Write([UInt32]40); $bw.Write([Int32]$size); $bw.Write([Int32]($size * 2))
    $bw.Write([UInt16]1); $bw.Write([UInt16]32); $bw.Write([UInt32]0)
    $bw.Write([UInt32]($size * $size * 4 + $maskStride * $size))
    $bw.Write([Int32]0); $bw.Write([Int32]0); $bw.Write([UInt32]0); $bw.Write([UInt32]0)
    for ($y = $size - 1; $y -ge 0; $y--) {
        for ($x = 0; $x -lt $size; $x++) {
            $c = $bmp.GetPixel($x, $y)
            $bw.Write([byte]$c.B); $bw.Write([byte]$c.G); $bw.Write([byte]$c.R); $bw.Write([byte]$c.A)
        }
    }
    $bw.Write((New-Object byte[] ($maskStride * $size)))
    $bw.Flush()
    return , $ms.ToArray()
}

$sizes = @(16, 24, 32, 48, 64, 128, 256)
$images = @($sizes | ForEach-Object {
    $bmp = New-IconPng $_
    if ($_ -eq 256) { $bmp.Save((Join-Path $PSScriptRoot "assets\soshop-256.png"), [System.Drawing.Imaging.ImageFormat]::Png) }
    , (ConvertTo-IconImage $bmp)
    $bmp.Dispose()
})

$ms = New-Object System.IO.MemoryStream
$bw = New-Object System.IO.BinaryWriter $ms
$bw.Write([UInt16]0); $bw.Write([UInt16]1); $bw.Write([UInt16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i]
    $dim = if ($s -ge 256) { 0 } else { $s }
    $bw.Write([byte]$dim); $bw.Write([byte]$dim); $bw.Write([byte]0); $bw.Write([byte]0)
    $bw.Write([UInt16]1); $bw.Write([UInt16]32)
    $bw.Write([UInt32]$images[$i].Length); $bw.Write([UInt32]$offset)
    $offset += $images[$i].Length
}
foreach ($img in $images) { $bw.Write([byte[]]$img) }
$bw.Flush()
[System.IO.File]::WriteAllBytes($out, $ms.ToArray())
Write-Host "Wrote $out"
