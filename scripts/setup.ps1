param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$NpmCache = Join-Path $ProjectRoot ".cache\npm"

# PowerShell 5.1 不会把外部程序的非零退出码自动转换成终止错误。
# 因此逐项检查退出码，避免依赖安装失败后脚本仍然报告成功。
function Invoke-ExternalChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

Write-Host "[1/5] Checking Python and Node.js" -ForegroundColor Cyan
Invoke-ExternalChecked -FilePath $Python -ArgumentList @("--version") -Description "Python check"
Invoke-ExternalChecked -FilePath "node" -ArgumentList @("--version") -Description "Node.js check"
Invoke-ExternalChecked -FilePath "npm" -ArgumentList @("--version") -Description "npm check"

Write-Host "[2/5] Creating an isolated Python environment" -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    Invoke-ExternalChecked -FilePath $Python -ArgumentList @("-m", "venv", ".venv") -Description "Virtual environment creation"
}
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Invoke-ExternalChecked -FilePath $VenvPython -ArgumentList @("-m", "pip", "install", "-r", "requirements.txt") -Description "Python dependency installation"

Write-Host "[3/5] Installing the local NetEase gateway" -ForegroundColor Cyan
Invoke-ExternalChecked -FilePath "npm" -ArgumentList @("--prefix", "gateway", "ci", "--cache", $NpmCache) -Description "NetEase gateway dependency installation"

Write-Host "[4/5] Installing and building the frontend" -ForegroundColor Cyan
Invoke-ExternalChecked -FilePath "npm" -ArgumentList @("--prefix", "frontend", "ci", "--cache", $NpmCache) -Description "Frontend dependency installation"
Invoke-ExternalChecked -FilePath "npm" -ArgumentList @("--prefix", "frontend", "run", "build") -Description "Frontend production build"

Write-Host "[5/5] Preparing local configuration" -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env. API keys can be configured later in the web UI."
}

Write-Host "Setup complete. Run: powershell -ExecutionPolicy Bypass -File scripts/start.ps1" -ForegroundColor Green
