# Build PakInspector.exe with PyInstaller (run from repo root).
# Prerequisite: pip install ".[exe]"

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$dist = Join-Path $root "dist"
$build = Join-Path $root "build"
if (Test-Path $dist) {
  Remove-Item -Recurse -Force $dist
}
if (Test-Path $build) {
  Remove-Item -Recurse -Force $build
}

$main = Join-Path $root "pakinspector\__main__.py"
python -m PyInstaller `
  --clean `
  --noconfirm `
  --onefile `
  --windowed `
  --name PakInspector `
  --paths $root `
  $main

Write-Host "完成: $dist\PakInspector.exe"
