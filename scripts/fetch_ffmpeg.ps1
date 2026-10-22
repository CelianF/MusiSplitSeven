# Download a static Windows ffmpeg.exe into vendor/ffmpeg/ for bundling.
# LGPL build from https://github.com/BtbN/FFmpeg-Builds (includes the libmp3lame MP3 encoder).
#
#   pwsh scripts/fetch_ffmpeg.ps1
$ErrorActionPreference = "Stop"

$Url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-lgpl-8.1.zip"
$Root = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $Root "vendor/ffmpeg"
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("ffmpeg_" + [guid]::NewGuid())

New-Item -ItemType Directory -Force -Path $Dest, $Tmp | Out-Null
$Zip = Join-Path $Tmp "ffmpeg.zip"
Write-Host "Downloading $Url"
Invoke-WebRequest -Uri $Url -OutFile $Zip
Expand-Archive -Path $Zip -DestinationPath $Tmp
$Dir = Get-ChildItem -Path $Tmp -Directory | Select-Object -First 1
Copy-Item (Join-Path $Dir.FullName "bin/ffmpeg.exe") $Dest -Force
Copy-Item (Join-Path $Dir.FullName "LICENSE.txt") (Join-Path $Dest "LICENSE.txt") -Force
Remove-Item -Recurse -Force $Tmp

$Exe = Join-Path $Dest "ffmpeg.exe"
$Encoders = & $Exe -hide_banner -encoders
if (-not ($Encoders -match "libmp3lame")) { throw "ffmpeg build has no libmp3lame encoder" }
& $Exe -hide_banner -version | Select-Object -First 1
