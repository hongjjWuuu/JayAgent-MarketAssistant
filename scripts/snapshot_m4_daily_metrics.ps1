param(
    [Parameter(Mandatory=$true)][string]$PilotId,
    [string]$AdminUsername = "admin_001",
    [string]$AdminPassword = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$envFile = ".env.m4-pilot"
if (-not (Test-Path -LiteralPath $envFile)) { throw "$envFile is missing" }
Get-Content $envFile | Where-Object { $_ -match '^[A-Z_][A-Z0-9_]*=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}
if (-not $AdminPassword) { $AdminPassword = $env:PILOT_TENANT_ADMIN_PASSWORD }
if (-not $AdminPassword) { throw "PILOT_TENANT_ADMIN_PASSWORD is not configured" }
$token = Invoke-RestMethod `
    -Uri "https://auth.marketassistant.test/realms/market-assistant/protocol/openid-connect/token" `
    -Method Post -ContentType "application/x-www-form-urlencoded" `
    -Body @{client_id="market-assistant-web";grant_type="password";username=$AdminUsername;password=$AdminPassword}
$headers = @{Authorization="Bearer $($token.access_token)"}
$snapshot = Invoke-RestMethod -Uri "https://api.marketassistant.test/api/v1/admin/pilots/$PilotId/snapshots" -Method Post -Headers $headers
New-Item -ItemType Directory -Force -Path "output\m4\daily" | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$snapshot | ConvertTo-Json -Depth 20 | Set-Content -Encoding utf8 "output\m4\daily\$stamp.json"
Write-Host "Daily snapshot saved: output/m4/daily/$stamp.json" -ForegroundColor Green
