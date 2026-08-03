# Builds a release: frontend -> PyInstaller bundle -> installer.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "Building frontend..."
Push-Location "$repoRoot\frontend"
bun run build
Pop-Location

Write-Host "Building PyInstaller bundle..."
Push-Location "$repoRoot\desktop"
& "$repoRoot\backend\.venv\Scripts\python.exe" -m PyInstaller launcher.spec --distpath dist --workpath build --noconfirm

Write-Host "Compiling installer..."
& iscc.exe installer.iss
Pop-Location

Write-Host "Done: desktop\installer_output\3DVaultkeeper-Setup.exe"
