param(
    [Parameter(Mandatory=$true)][string]$PilotId,
    [string]$AdminUsername = "admin_001",
    [string]$AdminPassword = "",
    [switch]$StopServices
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
$report = Invoke-RestMethod -Uri "https://api.marketassistant.test/api/v1/admin/pilots/$PilotId/close" -Method Post -Headers $headers
New-Item -ItemType Directory -Force -Path "output\m4" | Out-Null
$report | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 "output\m4\m4-pilot-final-report.json"
$decision = $report.decision
@"
# M4 Go/No-Go ADR

- Pilot: $PilotId
- Decision: **$decision**
- Generated: $(Get-Date -Format o)
- Hard gates: $($report.hard_gates | ConvertTo-Json -Compress)
- Product gates: $($report.product_gates | ConvertTo-Json -Compress)

The decision is generated from frozen pilot facts. Any failed safety hard gate is NO-GO.
"@ | Set-Content -Encoding utf8 "docs\M4_GO_NO_GO_ADR.md"
Write-Host "M4 pilot closed with decision: $decision" -ForegroundColor Green
if ($StopServices) {
    $compose = @("compose","--env-file",$envFile,"-p","jayagent-marketassistant-m4","-f","docker-compose.yml","-f","docker-compose.m4-pilot.yml")
    & docker @compose down
}
