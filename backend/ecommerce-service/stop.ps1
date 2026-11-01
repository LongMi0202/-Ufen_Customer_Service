$ErrorActionPreference = "SilentlyContinue"
$serviceRoot = (Resolve-Path $PSScriptRoot).Path
$pidFile = Join-Path $serviceRoot ".runtime\pid.json"
if (Test-Path -LiteralPath $pidFile) {
    $entries = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    foreach ($entry in @($entries)) {
        Stop-Process -Id $entry.pid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "E-commerce service stopped"
}
