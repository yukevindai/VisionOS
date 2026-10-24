param([string]$PhoneIP = '', [int]$Port = 8888, [switch]$Preview)
Set-Location -LiteralPath $PSScriptRoot
if (-not $PhoneIP) { $PhoneIP = Read-Host 'Current IP displayed in PathOn (iPhone connected to TONY 0864)' }
if (-not $PhoneIP) { throw 'A current PathOn IP is required. No saved venue address will be used.' }
$launchArgs = @('-m', 'deskguide', '--host', $PhoneIP, '--port', "$Port")
if ($Preview) { $launchArgs += '--preview' }
& '.\.venv\Scripts\python.exe' @launchArgs
exit $LASTEXITCODE
