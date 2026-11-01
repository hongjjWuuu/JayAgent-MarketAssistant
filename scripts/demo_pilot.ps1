$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

docker compose up -d --wait postgres redis keycloak
docker compose up -d migrate seed api worker web prometheus
Write-Host "Pilot stack started."
Write-Host "Web:        http://localhost:5173"
Write-Host "API health: http://localhost:8000/health/ready"
Write-Host "Metrics:    http://localhost:9090"
Write-Host "Demo flow: customer login -> order -> policy citation -> ticket preview -> confirm -> handoff -> admin metrics."

