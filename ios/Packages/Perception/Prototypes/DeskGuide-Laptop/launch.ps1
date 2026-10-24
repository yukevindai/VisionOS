param([string]$PhoneIP = '', [int]$Port = 8888, [switch]$Preview, [string]$Python = '')
$ErrorActionPreference = 'Stop'
if (-not $Python) { $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run setup.ps1 first, or pass -Python with a compatible installed environment.' }
if (-not $PhoneIP) { $PhoneIP = Read-Host 'Current IP displayed in PathOn' }
if (-not $PhoneIP) { throw 'A current PathOn IP is required. No saved venue address will be used.' }
$launchArgs = @('-m', 'deskguide', '--host', $PhoneIP, '--port', "$Port")
if ($Preview) { $launchArgs += '--preview' }
Push-Location -LiteralPath $PSScriptRoot
try {
    & $Python @launchArgs
    $exitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $exitCode
