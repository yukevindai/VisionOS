param(
    [string]$Python = 'python',
    [string]$PhoneHost = '192.168.137.223',
    [ValidateRange(1,65535)][int]$Port = 8888,
    [ValidateSet(0,90,180,270)][int]$Rotation = 0
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m r3_vision.live_camera --host $PhoneHost --port $Port --rotation $Rotation
    if ($LASTEXITCODE -ne 0) { throw 'R3 live camera exited with an error. See the message above.' }
} finally { Pop-Location }

