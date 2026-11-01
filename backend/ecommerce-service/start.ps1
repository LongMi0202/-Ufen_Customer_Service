param([switch]$SkipInstall)

$ErrorActionPreference = "Stop"
$processPath = $env:Path
Remove-Item Env:Path -ErrorAction SilentlyContinue
$env:Path = $processPath
$serviceRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $serviceRoot
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
}
if (-not $SkipInstall) { uv sync }

$runtimeDir = Join-Path $serviceRoot ".runtime"
$logsDir = Join-Path $serviceRoot "logs"
New-Item -ItemType Directory -Force -Path $runtimeDir, $logsDir | Out-Null
$pidFile = Join-Path $runtimeDir "pid.json"
if (Test-Path -LiteralPath $pidFile) {
    $old = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    if (Get-Process -Id $old.pid -ErrorAction SilentlyContinue) {
        Write-Host "E-commerce service is already running (PID $($old.pid))."
        exit 0
    }
    Remove-Item -LiteralPath $pidFile -Force
}
$uvicornPath = Join-Path $serviceRoot ".venv\Scripts\uvicorn.exe"
$process = Start-Process -WindowStyle Hidden -FilePath $uvicornPath `
    -ArgumentList @("ecommerce_service.main:app", "--host", "127.0.0.1", "--port", "8001") `
    -WorkingDirectory $serviceRoot -RedirectStandardOutput (Join-Path $logsDir "service.out.log") `
    -RedirectStandardError (Join-Path $logsDir "service.err.log") -PassThru
@{ name = "api"; pid = $process.Id } | ConvertTo-Json | `
    Set-Content -Encoding UTF8 -LiteralPath $pidFile
Write-Host "E-commerce service started: http://127.0.0.1:8001"
