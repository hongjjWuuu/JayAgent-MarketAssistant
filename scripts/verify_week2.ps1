[CmdletBinding()]
param(
    [ValidateRange(2, 16)]
    [int]$WorkerCount = 4,
    [string]$DatabaseUrl = "postgresql+psycopg://market_agent:market_agent@localhost:5433/market_agent",
    [switch]$KeepPostgresRunning
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $dockerCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"),
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    )
    foreach ($dockerCandidate in $dockerCandidates) {
        if (Test-Path -LiteralPath $dockerCandidate) {
            $env:Path = "$(Split-Path -Parent $dockerCandidate);$env:Path"
            break
        }
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop, then rerun this script."
}
if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    throw ".venv was not found. Run: python -m venv .venv"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

Write-Host "[1/8] Starting PostgreSQL/pgvector"
Invoke-Checked { docker compose up -d postgres } "Starting PostgreSQL"

Write-Host "[2/8] Waiting for PostgreSQL health check"
$ready = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    $containerId = docker compose ps -q postgres
    $status = docker inspect --format "{{.State.Health.Status}}" $containerId 2>$null
    if ($status -eq "healthy") {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 2
}
if (-not $ready) {
    throw "PostgreSQL did not become healthy in 60 seconds. Run: docker compose logs postgres"
}

$env:DATABASE_URL = $DatabaseUrl
$env:TEST_DATABASE_URL = $DatabaseUrl
$env:PERSISTENCE_BACKEND = "postgres"
$env:RUN_POSTGRES_TESTS = "1"
$env:WORKER_TEST_COUNT = $WorkerCount.ToString()

Write-Host "[3/8] Applying reliability migration"
Invoke-Checked { & .\.venv\Scripts\python.exe -m alembic upgrade head } "Alembic migration"

Write-Host "[4/8] Seeding repeatable demo data"
Invoke-Checked { & .\.venv\Scripts\python.exe scripts\seed_demo.py } "First demo seed"
Invoke-Checked { & .\.venv\Scripts\python.exe scripts\seed_demo.py } "Second demo seed"

Write-Host "[5/8] Running PostgreSQL reliability and forced-kill tests"
Invoke-Checked {
    & .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\integration -m integration -q
} "PostgreSQL reliability tests"

Write-Host "[6/8] Running backend checks"
Invoke-Checked { & .\.venv\Scripts\python.exe -m ruff check . } "Ruff"
$env:PERSISTENCE_BACKEND = "memory"
$env:APP_ENV = "dev"
$env:AUTH_MODE = "development_headers"
$env:ALLOW_DEV_AUTH = "true"
Invoke-Checked {
    & .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -m "not integration" -q
} "Backend unit and API tests"

Write-Host "[7/8] Running frontend tests and production build"
Push-Location -LiteralPath ".\web"
try {
    Invoke-Checked { npm test -- --run } "Frontend tests"
    Invoke-Checked { npm run build } "Frontend production build"
}
finally {
    Pop-Location
}

Write-Host "[8/8] Showing migration status"
$env:PERSISTENCE_BACKEND = "postgres"
Invoke-Checked { & .\.venv\Scripts\python.exe -m alembic current } "Alembic status"

Write-Host "Week 2 verification completed: concurrency, heartbeat, fencing, forced-kill recovery, replay, and reconciliation passed."
if (-not $KeepPostgresRunning) {
    Invoke-Checked { docker compose stop postgres } "Stopping PostgreSQL"
    Write-Host "PostgreSQL stopped; its persistent volume was retained."
}
