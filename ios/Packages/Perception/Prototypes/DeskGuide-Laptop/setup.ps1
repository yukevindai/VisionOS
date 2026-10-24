param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    if ($Python) {
        & $Python -m venv .venv
    } else {
        & py -3.12 -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 with Tcl/Tk, or pass -Python with its full python.exe path.' }
}
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed.' }
& $venvPython setup_models.py
if ($LASTEXITCODE -ne 0) { throw 'Model verification failed.' }
& $venvPython -m deskguide --check
if ($LASTEXITCODE -ne 0) { throw 'Model execution check failed.' }
Write-Host 'Ready: .\.venv\Scripts\python.exe -m deskguide --preview'
