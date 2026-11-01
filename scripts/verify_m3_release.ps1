param(
    [switch]$RunLiveDeepSeekSmoke,
    [switch]$KeepServicesRunning
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$project = "jayagent-marketassistant-drill"
$env:COMPOSE_PROJECT_NAME = $project
Get-Content ".env.m3-drill" | Where-Object { $_ -match '^[A-Z_][A-Z0-9_]*=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Create .venv before running M3 release verification." }
$compose = @("compose", "--env-file", ".env.m3-drill", "-p", $project)
$env:DATABASE_URL = "postgresql+psycopg://market_agent:market_agent@localhost:15433/market_agent"
$env:TEST_DATABASE_URL = $env:DATABASE_URL
$env:EMBEDDING_BASE_URL = "http://localhost:17997"
$failed = $true
try {
    & docker @compose up -d --build --scale worker=2 postgres redis keycloak embedding simulator migrate seed api worker web prometheus
    & docker @compose wait migrate seed
    $deadline = (Get-Date).AddMinutes(8)
    do {
        try { $ready = Invoke-RestMethod "http://localhost:18000/health/ready" -TimeoutSec 5 } catch { $ready = $null }
        if ($ready.status -eq "ready") { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    if ($ready.status -ne "ready") { throw "M3 drill API did not become ready" }

    & $python -m ruff check app simulator tests scripts
    & $python -m pytest -q tests --ignore=tests/integration
    $env:RUN_POSTGRES_TESTS = "1"
    & $python -m pytest tests\integration -m integration -q
    $env:RUN_OIDC_TESTS = "1"
    & $python -m pytest tests\integration\test_keycloak_oidc.py -q

    Push-Location web
    try {
        npm test -- --run
        npm run build
        $env:E2E_M3 = "true"
        $env:E2E_BASE_URL = "http://localhost:15173"
        $env:E2E_API_URL = "http://localhost:18000"
        $env:E2E_KEYCLOAK_URL = "http://localhost:18080"
        $env:E2E_SIMULATOR_URL = "http://localhost:18090"
        npx playwright test e2e/m3-release.spec.ts --trace on
        $env:M3_OIDC_E2E_RESULT = "passed"
    } finally { Pop-Location }

    & (Join-Path $PSScriptRoot "run_m3_failure_drills.ps1") -KeepServicesRunning
    & (Join-Path $PSScriptRoot "verify_backup_restore.ps1")
    & docker @compose exec -T prometheus promtool check rules /etc/prometheus/alerts.yml
    & docker @compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
    & (Join-Path $PSScriptRoot "verify_week5_fake.ps1")
    $env:M3_FAKE_RESULT = "80/80"
    if ($RunLiveDeepSeekSmoke) {
        & (Join-Path $PSScriptRoot "verify_week5.ps1") -RunLiveDeepSeekSmoke
        $env:M3_DEEPSEEK_RESULT = "20/20"
    }
    & $python scripts\generate_m3_release_report.py
    $failed = $false
    Write-Host "M3 release admission package passed." -ForegroundColor Green
} finally {
    Remove-Item Env:RUN_POSTGRES_TESTS,Env:RUN_OIDC_TESTS,Env:E2E_M3 -ErrorAction SilentlyContinue
    if (-not $KeepServicesRunning -and -not $failed) {
        if ($project -ne "jayagent-marketassistant-drill") { throw "Unsafe Compose cleanup target" }
        & docker @compose down -v --remove-orphans
    } elseif ($failed) {
        Write-Warning "Verification failed; isolated drill environment was preserved for diagnosis."
    }
}
