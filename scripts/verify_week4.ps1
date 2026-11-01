param(
    [switch]$KeepServicesRunning
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($null -eq $dockerCommand) {
    $dockerCandidates = @(
        "C:\Users\JayWuuu\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe",
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    )
    $docker = $dockerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $docker) { throw "Docker CLI was not found. Start Docker Desktop first." }
} else {
    $docker = $dockerCommand.Source
}
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Python virtual environment is missing." }

try {
    & $docker compose up -d --build postgres redis keycloak migrate seed api worker web
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
    $deadline = (Get-Date).AddMinutes(4)
    do {
        try { $ready = Invoke-RestMethod -Uri "http://localhost:8000/health/ready" -TimeoutSec 5 }
        catch { $ready = $null }
        if ($ready.status -eq "ready") { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    if ($ready.status -ne "ready") { throw "API did not become ready" }

    $env:PERSISTENCE_BACKEND = "postgres"
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Alembic upgrade failed" }
    & $python scripts\seed_week4_gold.py
    if ($LASTEXITCODE -ne 0) { throw "Week 4 Gold Set seed failed" }
    & $python scripts\seed_week41_gold.py
    if ($LASTEXITCODE -ne 0) { throw "Week 4.1 Gold Set seed failed" }
    & $python scripts\verify_week41_quality.py
    if ($LASTEXITCODE -ne 0) { throw "Week 4.1 quality gate failed" }
    Remove-Item Env:PERSISTENCE_BACKEND -ErrorAction SilentlyContinue
    & $python -m ruff check app tests scripts `
        alembic\versions\20261025_0004_rag_quality.py `
        alembic\versions\20261026_0005_rag_quality_closure.py
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed" }
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed" }
    $env:RUN_POSTGRES_TESTS = "1"
    & $python -m pytest tests\integration -m integration -q
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL tests failed" }
    $env:RUN_OIDC_TESTS = "1"
    & $python -m pytest tests\integration\test_keycloak_oidc.py -q
    if ($LASTEXITCODE -ne 0) { throw "Keycloak OIDC tests failed" }
    Push-Location web
    try {
        npm test -- --run
        if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
        $env:E2E_OIDC = "true"
        npx playwright test --grep "assigned workspace"
        if ($LASTEXITCODE -ne 0) { throw "OIDC browser E2E failed" }
    } finally { Pop-Location }
    $env:PERSISTENCE_BACKEND = "postgres"
    & $python -m alembic current
    & $docker compose ps
    Write-Host "Week 4 verification passed" -ForegroundColor Green
} finally {
    Remove-Item Env:PERSISTENCE_BACKEND -ErrorAction SilentlyContinue
    Remove-Item Env:RUN_POSTGRES_TESTS -ErrorAction SilentlyContinue
    Remove-Item Env:RUN_OIDC_TESTS -ErrorAction SilentlyContinue
    Remove-Item Env:E2E_OIDC -ErrorAction SilentlyContinue
    if (-not $KeepServicesRunning) { & $docker compose stop api worker web keycloak }
}
