$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$py = (Get-Command python -ErrorAction Stop).Source
Write-Host "Using $py"

& $py -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "Building app.ico from app.svg..."
& $py scripts\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "make_icon failed" }
$ico = "ghostport\resources\icons\app.ico"
if (-not (Test-Path $ico)) { throw "app.ico was not produced" }

Write-Host "Building Ghostport.exe (app)..."
& $py -m PyInstaller --noconfirm --clean ghostport.spec
if ($LASTEXITCODE -ne 0) { throw "Ghostport spec failed" }

$flag = Join-Path "dist\Ghostport" "portable.flag"
if (-not (Test-Path $flag)) {
    New-Item -ItemType File -Path $flag | Out-Null
}

Write-Host "Building GhostportSetup.exe (installer, no admin)..."
& $py -m PyInstaller --noconfirm --clean ghostport-setup.spec
if ($LASTEXITCODE -ne 0) { throw "Setup spec failed" }

$setup = "dist\GhostportSetup\GhostportSetup.exe"
if (-not (Test-Path $setup)) { throw "GhostportSetup.exe was not produced" }

Write-Host ""
Write-Host "Done. Double-click:"
Write-Host "  $((Resolve-Path $setup).Path)"
Write-Host "No administrator password. Installs to your user folder."
