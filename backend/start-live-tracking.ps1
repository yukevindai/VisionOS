param(
    [switch]$AudioTest,
    [switch]$DepthCheck,
    [switch]$DepthView,
    [string]$Python = '',
    [string]$PhoneHost = '192.168.137.223',
    [ValidateRange(1,65535)][int]$Port = 8888,
    [ValidateSet(0,90,180,270)][int]$Rotation = 0,
    [string]$Request = 'Find the Red Bull can',
    [ValidateSet('gemini','hybrid')][string]$Detection = 'gemini',
    [switch]$Audio,
    [ValidateSet('lock','vit','orb')][string]$Tracker = 'lock',
    [ValidateSet('object','control')][string]$TargetKind = 'object',
    [double]$Cooldown = 20,
    [int]$PerMinute = 8,
    [int]$MaxRequests = 20,
    [ValidateRange(1,120)][double]$Timeout = 60,
    [string]$Model = 'gemini-3.8-flash',
    [ValidateSet('minimal','low','medium','high')][string]$ThinkingLevel = 'low',
    [ValidateSet('inventory','target')][string]$GeminiReply = 'inventory',
    [string]$AudioDevice = '',
    [ValidateSet('default','low','medium','high')][string]$MediaResolution = 'medium'
)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $venv = Join-Path $PSScriptRoot '..\..\DeskGuide-Laptop\.venv\Scripts\python.exe'
    $Python = if ($Detection -eq 'hybrid' -and (Test-Path -LiteralPath $venv)) { $venv } else { 'python' }
}
Push-Location $PSScriptRoot
if ($DepthCheck) {
    try { & $Python -m r3_vision.depth_check --host $PhoneHost --port $Port; exit $LASTEXITCODE }
    finally { Pop-Location }
}
if ($AudioTest) {
    try { & $Python -m r3_vision.live_tracking --audio-test $(if ($AudioDevice) { @('--audio-device', $AudioDevice) }); exit $LASTEXITCODE }
    finally { Pop-Location }
}
$previousModel = $env:GEMINI_MODEL
$previousAutoinstall = $env:YOLO_AUTOINSTALL
try {
    $env:GEMINI_MODEL = $Model
    $env:YOLO_AUTOINSTALL = 'false'
    & $Python -m r3_vision.live_tracking --host $PhoneHost --port $Port --rotation $Rotation --request $Request --detection $Detection $(if ($DepthView) { '--depth-view' }) $(if ($Audio) { '--audio' } else { '--no-audio' }) --tracker $Tracker --target-kind $TargetKind --thinking $ThinkingLevel --media-resolution $MediaResolution --gemini-reply $GeminiReply $(if ($AudioDevice) { @('--audio-device', $AudioDevice) }) --cooldown $Cooldown --per-minute $PerMinute --max-requests $MaxRequests --timeout $Timeout
    if ($LASTEXITCODE -ne 0) { throw 'R3 tracking exited with an error. See message above.' }
} finally {
    $env:GEMINI_MODEL = $previousModel
    $env:YOLO_AUTOINSTALL = $previousAutoinstall
    Pop-Location
}
