param([switch]$SkipInstall)

$ErrorActionPreference = "Stop"
$serviceRoot = (Resolve-Path $PSScriptRoot).Path
Set-Location $serviceRoot
$pidFile = Join-Path $serviceRoot ".runtime\pids.json"
if (Test-Path -LiteralPath $pidFile) {
    $running = @(
        Get-Content -Raw -LiteralPath $pidFile |
            ConvertFrom-Json |
            Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue }
    )
    if ($running.Count -gt 0) {
        throw "E-commerce service is running. Run .\stop.ps1 before resetting data."
    }
}
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
}
if (-not $SkipInstall) { uv sync }

& (Join-Path $serviceRoot ".venv\Scripts\python.exe") -m ecommerce_service.reset_data
