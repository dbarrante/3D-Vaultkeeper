# Builds a release: frontend -> PyInstaller bundle -> installer.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "Building frontend..."
Push-Location "$repoRoot\frontend"
bun run build
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE" }
Pop-Location

Write-Host "Building PyInstaller bundle..."
Push-Location "$repoRoot\desktop"
& "$repoRoot\backend\.venv\Scripts\python.exe" -m PyInstaller launcher.spec --distpath dist --workpath build --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE" }

Write-Host "Compiling installer..."
$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    $fallback = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    if (Test-Path $fallback) {
        $iscc = $fallback
    } else {
        throw "iscc.exe not found on PATH or at the default winget install location ($fallback). Install Inno Setup: winget install JRSoftware.InnoSetup"
    }
} else {
    $iscc = $iscc.Source
}
& $iscc installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed with exit code $LASTEXITCODE" }
Pop-Location

Write-Host "Done: desktop\installer_output\3DVaultkeeper-Setup.exe"
