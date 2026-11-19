param(
    [int]$Port = 8000,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not $Python) {
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
}

function Test-TcpPortAvailable([int]$Candidate) {
    $Listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $Candidate
    )
    try {
        $Listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        try { $Listener.Stop() } catch { }
    }
}

$SelectedPort = $Port
while (-not (Test-TcpPortAvailable $SelectedPort)) {
    Write-Host "Port $SelectedPort is busy; trying $($SelectedPort + 1)..." -ForegroundColor Yellow
    $SelectedPort += 1
    if ($SelectedPort -gt ($Port + 50)) {
        throw "No available port found near the requested port."
    }
}

$Address = "http://127.0.0.1:$SelectedPort"
Write-Host "Personal-Radio is starting at $Address" -ForegroundColor Green
Write-Host "The NetEase gateway also selects a free port automatically."
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port $SelectedPort
