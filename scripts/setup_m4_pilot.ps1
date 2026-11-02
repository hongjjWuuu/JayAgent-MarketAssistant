param(
    [switch]$InstallCertificate,
    [switch]$ConfigureHosts,
    [string]$PilotAddress = "",
    [switch]$FreezeRelease,
    [switch]$KeepServicesRunning
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$envFile = ".env.m4-pilot"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Copy .env.m4-pilot.example to .env.m4-pilot and replace every placeholder first."
}
Get-Content $envFile | Where-Object { $_ -match '^[A-Z_][A-Z0-9_]*=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}
if ($env:COMPOSE_PROJECT_NAME -ne "jayagent-marketassistant-m4") {
    throw "COMPOSE_PROJECT_NAME must be jayagent-marketassistant-m4"
}
if ($env:PILOT_HOST -ne "test") {
    throw "The frozen v1 realm supports PILOT_HOST=test; create a new realm version for another suffix."
}
foreach ($name in "POSTGRES_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD", "SIMULATOR_CONTROL_TOKEN", "PILOT_CUSTOMER_PASSWORD", "PILOT_AGENT_PASSWORD", "PILOT_TENANT_ADMIN_PASSWORD") {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if (-not $value -or $value -like "replace-*") { throw "$name must be replaced" }
}
if ($env:POSTGRES_PASSWORD -notmatch '^[A-Za-z0-9._~-]+$') {
    throw "POSTGRES_PASSWORD must be URL-safe for the pilot connection string"
}
if (-not $env:LLM_API_KEY) { throw "LLM_API_KEY is required for M4" }
if ([double]$env:LLM_INPUT_COST_PER_MILLION -le 0 -or [double]$env:LLM_OUTPUT_COST_PER_MILLION -le 0) {
    throw "Configure actual positive LLM token prices before M4"
}

$project = "jayagent-marketassistant-m4"
$compose = @(
    "compose", "--env-file", $envFile, "-p", $project,
    "-f", "docker-compose.yml", "-f", "docker-compose.m4-pilot.yml"
)
& docker @compose config --quiet
& docker @compose up -d --build --scale worker=2 `
    postgres redis keycloak embedding simulator migrate seed api worker web prometheus caddy
& docker @compose wait migrate seed
& docker @compose run --rm seed python -m scripts.seed_m4_pilot

$deadline = (Get-Date).AddMinutes(3)
do {
    & docker @compose exec -T keycloak /opt/keycloak/bin/kcadm.sh config credentials `
        --server http://localhost:8080 --realm master --user admin `
        --password $env:KEYCLOAK_ADMIN_PASSWORD 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)
if ($LASTEXITCODE -ne 0) { throw "Keycloak admin API did not become ready" }
foreach ($username in "customer_001", "pilot_customer_02", "pilot_customer_03", "pilot_customer_04", "pilot_customer_05") {
    & docker @compose exec -T keycloak /opt/keycloak/bin/kcadm.sh set-password `
        -r market-assistant --username $username --new-password $env:PILOT_CUSTOMER_PASSWORD
}
foreach ($username in "agent_001", "pilot_agent_002") {
    & docker @compose exec -T keycloak /opt/keycloak/bin/kcadm.sh set-password `
        -r market-assistant --username $username --new-password $env:PILOT_AGENT_PASSWORD
}
& docker @compose exec -T keycloak /opt/keycloak/bin/kcadm.sh set-password `
    -r market-assistant --username admin_001 --new-password $env:PILOT_TENANT_ADMIN_PASSWORD

$databaseUrl = "postgresql+psycopg://market_agent:$($env:POSTGRES_PASSWORD)@localhost:$($env:M4_POSTGRES_PORT)/market_agent"
$embeddingUrl = "http://localhost:$($env:M4_EMBEDDING_PORT)"
& (Join-Path $PSScriptRoot "initialize_m3_retrieval.ps1") `
    -DatabaseUrl $databaseUrl -EmbeddingBaseUrl $embeddingUrl

New-Item -ItemType Directory -Force -Path "output\m4" | Out-Null
$frozenRelease = $null
if ($FreezeRelease) {
    if ($env:PILOT_CONSENT_CONFIRMED -ne "true") {
        throw "Set PILOT_CONSENT_CONFIRMED=true only after every selected participant consented"
    }
    $customerCount = [int]$env:PILOT_CUSTOMER_COUNT
    $agentCount = [int]$env:PILOT_AGENT_COUNT
    if ($customerCount -lt 3 -or $customerCount -gt 5) { throw "PILOT_CUSTOMER_COUNT must be 3..5" }
    if ($agentCount -lt 1 -or $agentCount -gt 2) { throw "PILOT_AGENT_COUNT must be 1..2" }
    $gitSha = (& git rev-parse HEAD).Trim()
    function Get-ServiceImageDigest([string]$serviceName) {
        $containerId = (& docker @compose ps -q $serviceName).Trim()
        if (-not $containerId) { throw "M4 service $serviceName has no running container" }
        $digest = (& docker inspect --format '{{.Image}}' $containerId).Trim()
        if ($digest -notmatch '^sha256:[0-9a-f]{64}$') {
            throw "Unable to resolve immutable image digest for $serviceName"
        }
        return $digest
    }
    $apiDigest = Get-ServiceImageDigest "api"
    $webDigest = Get-ServiceImageDigest "web"
    $simulatorDigest = Get-ServiceImageDigest "simulator"
    $freezeOutput = & docker @compose exec -T api python -m scripts.freeze_m4_release `
        --git-sha $gitSha --api-digest $apiDigest --web-digest $webDigest `
        --simulator-digest $simulatorDigest --customer-count $customerCount `
        --agent-count $agentCount --consent-confirmed
    if ($LASTEXITCODE -ne 0) { throw "M4 release freeze failed" }
    $frozenRelease = $freezeOutput | Select-Object -Last 1 | ConvertFrom-Json
    $frozenRelease | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 "output\m4\frozen-release.json"
}
$deadline = (Get-Date).AddMinutes(2)
do {
    & docker @compose cp caddy:/data/caddy/pki/authorities/local/root.crt output/m4/caddy-root.crt 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if ($LASTEXITCODE -ne 0) { throw "Caddy local CA was not generated" }

if (-not $PilotAddress) {
    $PilotAddress = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.PrefixOrigin -ne 'WellKnown' } |
        Select-Object -First 1 -ExpandProperty IPAddress
}
$hosts = @("marketassistant.test", "api.marketassistant.test", "auth.marketassistant.test")
if ($ConfigureHosts) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if (-not $isAdmin) { throw "-ConfigureHosts requires an elevated PowerShell session" }
    $hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
    foreach ($hostName in $hosts) {
        if (-not (Select-String -LiteralPath $hostsPath -SimpleMatch $hostName -Quiet)) {
            Add-Content -LiteralPath $hostsPath -Value "$PilotAddress`t$hostName"
        }
    }
}
if ($InstallCertificate) {
    Import-Certificate -FilePath "output\m4\caddy-root.crt" -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
}

Write-Host "M4 pilot services are ready at https://marketassistant.test" -ForegroundColor Green
if ($frozenRelease) {
    Write-Host "Frozen pilot release: $($frozenRelease.pilot_id)" -ForegroundColor Green
} else {
    Write-Host "Release remains unfrozen. Confirm participants, then rerun with -FreezeRelease."
}
Write-Host "Client hosts entries: $PilotAddress $($hosts -join ', ')"
Write-Host "Install output/m4/caddy-root.crt into each participant's CurrentUser Root store."
if (-not $KeepServicesRunning) {
    Write-Host "Services intentionally remain running; use close_m4_pilot.ps1 after the pilot."
}
