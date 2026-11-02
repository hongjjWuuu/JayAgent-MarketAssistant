param([switch]$RunLiveDeepSeekSmoke)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Create .venv before verification" }
if (-not (Test-Path -LiteralPath ".env.m4-pilot")) { throw ".env.m4-pilot is missing" }
$envFile = ".env.m4-pilot"
Get-Content $envFile | Where-Object { $_ -match '^[A-Z_][A-Z0-9_]*=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}
$project = "jayagent-marketassistant-m4"
$compose = @(
    "compose", "--env-file", $envFile, "-p", $project,
    "-f", "docker-compose.yml", "-f", "docker-compose.m4-pilot.yml"
)

& docker @compose config --quiet
& $python -m ruff check app simulator tests scripts alembic
& $python -m scripts.scan_repository_secrets
& $python -m pip_audit --local --skip-editable
& $python -m pytest -q tests --ignore=tests/integration
& $python -m pytest -q tests/test_m4_pilot.py
Push-Location web
try {
    npm test -- --run
    npm run build
    npm audit --audit-level=high
} finally { Pop-Location }

foreach ($serviceName in "api", "web", "simulator", "caddy") {
    $containerId = (& docker @compose ps -q $serviceName).Trim()
    if (-not $containerId) { throw "Cannot scan missing service image: $serviceName" }
    $imageId = (& docker inspect --format '{{.Image}}' $containerId).Trim()
    # Gate on actionable application-layer CVEs. A full non-blocking report is
    # still emitted so unfixed base-image findings remain visible in evidence.
    & docker scout cves --ignore-base --only-fixed --only-severity critical,high --exit-code $imageId
    & docker scout cves --only-severity critical,high $imageId
}

$certificate = Resolve-Path "output\m4\caddy-root.crt"
& curl.exe --fail --silent --show-error --cacert $certificate `
    --resolve "api.marketassistant.test:443:127.0.0.1" `
    https://api.marketassistant.test/health/ready | Out-Null
& curl.exe --fail --silent --show-error --cacert $certificate `
    --resolve "auth.marketassistant.test:443:127.0.0.1" `
    https://auth.marketassistant.test/realms/market-assistant/.well-known/openid-configuration | Out-Null

$env:PERSISTENCE_BACKEND = "postgres"
$env:DATABASE_URL = "postgresql+psycopg://market_agent:$($env:POSTGRES_PASSWORD)@localhost:$($env:M4_POSTGRES_PORT)/market_agent"
& $python -m scripts.check_m3_retrieval_readiness | ConvertFrom-Json | ForEach-Object {
    if (-not $_.ready) { throw "Active bge-m3-v10 readiness failed" }
}
& $python -c "from app.pilot_dataset import build_m4_orders,m4_dataset_hash; assert len(build_m4_orders()) == 30; print(m4_dataset_hash())"

if ($RunLiveDeepSeekSmoke) {
    & (Join-Path $PSScriptRoot "verify_week5.ps1") -RunLiveDeepSeekSmoke
}
Write-Host "M4 readiness verification passed." -ForegroundColor Green
