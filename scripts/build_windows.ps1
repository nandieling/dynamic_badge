$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

$AppName = "DynamicBadge"

if (-not $env:PYINSTALLER_CONFIG_DIR) {
  $env:PYINSTALLER_CONFIG_DIR = (Join-Path $RootDir ".pyinstaller")
}
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR | Out-Null

$Python =
  if ($env:PYTHON) { $env:PYTHON }
  elseif (Test-Path ".venv\\Scripts\\python.exe") { ".venv\\Scripts\\python.exe" }
  else { "python" }

try {
  & $Python -m PyInstaller --version | Out-Null
} catch {
  Write-Error "PyInstaller not found. Install it with: $Python -m pip install -U pyinstaller"
  exit 1
}

$FfmpegBinDir =
  if ($env:FFMPEG_BIN_DIR) { $env:FFMPEG_BIN_DIR }
  else { Join-Path $RootDir "ffmpeg_bin" }

$Ffmpeg = Join-Path $FfmpegBinDir "ffmpeg.exe"
$Ffprobe = Join-Path $FfmpegBinDir "ffprobe.exe"
if (!(Test-Path $Ffmpeg) -or !(Test-Path $Ffprobe)) {
  Write-Error "Missing ffmpeg/ffprobe in '$FfmpegBinDir'. Expected: ffmpeg.exe + ffprobe.exe"
  exit 1
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "build"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue ("dist\\" + $AppName)
New-Item -ItemType Directory -Force -Path "build" | Out-Null

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --distpath dist `
  --workpath build `
  --specpath build `
  --name $AppName `
  main.py

$DistAppDir = Join-Path $RootDir ("dist\\" + $AppName)
if (!(Test-Path $DistAppDir)) {
  Write-Error "Build failed: '$DistAppDir' not found."
  exit 1
}

$DestBinDir = Join-Path $DistAppDir "ffmpeg_bin"
New-Item -ItemType Directory -Force -Path $DestBinDir | Out-Null
Copy-Item -Force $Ffmpeg (Join-Path $DestBinDir "ffmpeg.exe")
Copy-Item -Force $Ffprobe (Join-Path $DestBinDir "ffprobe.exe")

$ZipPath = Join-Path $RootDir ("dist\\" + $AppName + "-windows-x64.zip")
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $DistAppDir -DestinationPath $ZipPath -Force

Write-Host ("Created: " + $ZipPath)
