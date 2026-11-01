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
    $dockerPath = $dockerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $dockerPath) {
        throw "Docker CLI was not found. Start Docker Desktop and add docker.exe to PATH."
    }
    $docker = $dockerPath
} else {
    $docker = $dockerCommand.Source
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment is missing. Run: python -m venv .venv"
}

try {
    & $docker compose up -d --build postgres redis keycloak migrate seed api worker web
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

    $deadline = (Get-Date).AddMinutes(4)
    do {
        try {
            $ready = Invoke-RestMethod -Uri "http://localhost:8000/health/ready" -TimeoutSec 5
            $realm = Invoke-RestMethod -Uri "http://localhost:8080/realms/market-assistant" -TimeoutSec 5
        } catch {
            $ready = $null
            $realm = $null
        }
        if ($ready.status -eq "ready" -and $realm.realm -eq "market-assistant") { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    if ($ready.status -ne "ready" -or $realm.realm -ne "market-assistant") {
        throw "API or Keycloak did not become ready"
    }

    & $python -m ruff check app tests scripts
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
        Remove-Item Env:E2E_OIDC -ErrorAction SilentlyContinue
    } finally {
        Pop-Location
    }
    & $python -m alembic current
    & $docker compose ps
    Write-Host "Week 3 verification passed" -ForegroundColor Green
} finally {
    Remove-Item Env:RUN_POSTGRES_TESTS -ErrorAction SilentlyContinue
    Remove-Item Env:RUN_OIDC_TESTS -ErrorAction SilentlyContinue
    Remove-Item Env:E2E_OIDC -ErrorAction SilentlyContinue
    if (-not $KeepServicesRunning) {
        & $docker compose stop api worker web keycloak
    }
}
