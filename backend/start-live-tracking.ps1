param(
    [string]$Python = 'python',
    [string]$PhoneHost = '192.168.137.223',
    [ValidateRange(1,65535)][int]$Port = 8888,
    [ValidateSet(0,90,180,270)][int]$Rotation = 0,
    [string]$Request = 'Find the Red Bull can',
    [ValidateSet('lock','vit','orb')][string]$Tracker = 'lock',
    [ValidateSet('object','control')][string]$TargetKind = 'object',
    [double]$Cooldown = 20,
    [int]$PerMinute = 3,
    [int]$MaxRequests = 20,
    [ValidateRange(1,120)][double]$Timeout = 60,
    [string]$Model = 'gemini-3.8-flash',
    [ValidateSet('low','medium','high')][string]$ThinkingLevel = 'low'
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
$previousModel = $env:GEMINI_MODEL
try {
    $env:GEMINI_MODEL = $Model
    & $Python -m r3_vision.live_tracking --host $PhoneHost --port $Port --rotation $Rotation --request $Request --tracker $Tracker --target-kind $TargetKind --thinking $ThinkingLevel --cooldown $Cooldown --per-minute $PerMinute --max-requests $MaxRequests --timeout $Timeout
    if ($LASTEXITCODE -ne 0) { throw 'R3 tracking exited with an error. See message above.' }
} finally {
    $env:GEMINI_MODEL = $previousModel
    Pop-Location
}
