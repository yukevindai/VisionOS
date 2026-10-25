param(
    [switch]$AudioTest,
    [switch]$DepthCheck,
    [switch]$DepthView,
    [switch]$WebDashboard,
    [switch]$Agents,
    [switch]$ArduinoBLE,
    [string]$ArduinoAddress = '',
    [ValidateRange(1,65535)][int]$WebPort = 8765,
    [string]$Python = '',
    [string]$PhoneHost = 'auto',
    [string]$Webcam = '',
    [ValidateRange(1,65535)][int]$Port = 8888,
    [ValidateSet(0,90,180,270)][int]$Rotation = 0,
    [string]$Request = 'Find the Red Bull can',
    [ValidateSet('gemini','hybrid','allobjects','demo')][string]$Detection = 'hybrid',
    [switch]$Demo,
    [switch]$AllObjects,
    [ValidateSet('world','yoloe')][string]$DiscoverySource = 'world',
    [double]$DiscoveryInterval = 1.0,
    [double]$VerifyInterval = 15,
    [switch]$Audio,
    [switch]$Voice,
    [string]$DistanceCalibration = '',
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
# Voice is the conversational product path. Keep basic command-only mode explicit.
if ($Voice -and -not $PSBoundParameters.ContainsKey('Agents')) { $Agents = $true }
if ($Voice) {
    Write-Host $(if ($Agents) { 'VOICE ROUTING: laptop speech-to-text -> agent coordinator' }
                 else { 'VOICE ROUTING: basic local commands only (-Agents:$false)' })
}
if ($AllObjects) { $Detection = 'allobjects' }
if ($Demo) { $Detection = 'demo' }
if (-not $Python) {
    # HYBRID and ALL OBJECTS need Ultralytics, which is installed only in the DeskGuide venv.
    $venv = Join-Path $PSScriptRoot '..\..\DeskGuide-Laptop\.venv\Scripts\python.exe'
    $Python = if ($Detection -ne 'gemini' -and (Test-Path -LiteralPath $venv)) { $venv } else { 'python' }
}
$discovery = if ($Detection -eq 'allobjects') {
    @('--discovery-source', $DiscoverySource, '--discovery-interval', $DiscoveryInterval.ToString([cultureinfo]::InvariantCulture),
      '--verify-interval', $VerifyInterval.ToString([cultureinfo]::InvariantCulture))
} else { @() }
$calibration = if ($DistanceCalibration) { @('--distance-calibration', (Resolve-Path -LiteralPath $DistanceCalibration).Path) } else { @() }

function Find-PhoneHost([int]$TcpPort, [string[]]$Candidates = @()) {
    # Probe, in parallel, the iPhone Personal Hotspot address (USB cable or iPhone Wi-Fi hotspot) first, then every
    # address on the laptop's Mobile Hotspot (192.168.137.x) and the iPhone hotspot range (172.20.10.x).
    $candidates = if ($Candidates.Count) { $Candidates } else {
        @('172.20.10.1') + (2..15 | ForEach-Object { "172.20.10.$_" }) + (2..254 | ForEach-Object { "192.168.137.$_" })
    }
    $probes = foreach ($address in $candidates) {
        $client = New-Object System.Net.Sockets.TcpClient
        [pscustomobject]@{ Address = $address; Client = $client; Task = $client.ConnectAsync($address, $TcpPort) }
    }
    $deadline = (Get-Date).AddMilliseconds(1500)
    $found = $null
    while (-not $found -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 100
        $found = $probes | Where-Object { $_.Task.Status -eq 'RanToCompletion' -and $_.Client.Connected } | Select-Object -First 1
    }
    foreach ($probe in $probes) { $probe.Client.Dispose() }
    if ($found) { return $found.Address }
    return $null
}

if ($Webcam -eq '' -and $PhoneHost -eq 'auto') {
    Write-Host "Looking for the PathOn stream on port $Port (USB Personal Hotspot or Wi-Fi hotspot)..."
    $PhoneHost = Find-PhoneHost $Port
    if (-not $PhoneHost) {
        throw "PathOn not found on port $Port. Open PathOn on the iPhone and start streaming, keep the screen on, and connect it by USB (Personal Hotspot on) or to the laptop hotspot. Or pass -PhoneHost <address>."
    }
    Write-Host "Found PathOn at $PhoneHost"
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
if ($Detection -eq 'demo' -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.runtime\demo-objects\pepsi'))) {
    # First DEMO launch: reference photos of the six objects from the recorded PathOn captures.
    & $Python -m r3_vision.demo_objects harvest
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'Could not create DEMO reference photos.' }
}
$previousModel = $env:GEMINI_MODEL
$previousAutoinstall = $env:YOLO_AUTOINSTALL
try {
    $env:GEMINI_MODEL = $Model
    $env:YOLO_AUTOINSTALL = 'false'
    # -Webcam <index> uses a local camera instead of PathOn (no LiDAR: 2D only).
    $source = if ($Webcam -ne '') { @('--video', $Webcam) } else { @('--host', $PhoneHost, '--port', $Port) }
    & $Python -m r3_vision.live_tracking @source $(if ($ArduinoBLE) { '--arduino-ble' }) $(if ($ArduinoAddress) { @('--arduino-address', $ArduinoAddress) }) $(if ($Agents) { '--agents' }) $(if ($WebDashboard) { @('--web-dashboard', '--web-port', $WebPort) }) --rotation $Rotation --request $Request --detection $Detection @discovery @calibration $(if ($DepthView) { '--depth-view' }) $(if ($Audio) { '--audio' } else { '--no-audio' }) $(if ($Voice) { '--voice' }) --tracker $Tracker --target-kind $TargetKind --thinking $ThinkingLevel --media-resolution $MediaResolution --gemini-reply $GeminiReply $(if ($AudioDevice) { @('--audio-device', $AudioDevice) }) --cooldown $Cooldown --per-minute $PerMinute --max-requests $MaxRequests --timeout $Timeout
    if ($LASTEXITCODE -ne 0) { throw 'R3 tracking exited with an error. See message above.' }
} finally {
    $env:GEMINI_MODEL = $previousModel
    $env:YOLO_AUTOINSTALL = $previousAutoinstall
    Pop-Location
}
