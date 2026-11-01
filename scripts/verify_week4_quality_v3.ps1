param([switch]$KeepServicesRunning)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Python virtual environment is missing." }
Set-Location $projectRoot
try {
    docker compose up -d postgres redis embedding
    if ($LASTEXITCODE -ne 0) { throw "Quality infrastructure startup failed" }
    $env:PERSISTENCE_BACKEND = "postgres"
    $env:EMBEDDING_BASE_URL = "http://localhost:7997"
    $env:EMBEDDING_MODEL = "BAAI/bge-m3"
    $env:EMBEDDING_DIMENSION = "1024"
    & $python -m alembic upgrade head
    & $python scripts\seed_week41_gold.py
    & $python scripts\seed_week42_gold.py
    & $python scripts\seed_week4_hard_negative.py
    if ($LASTEXITCODE -ne 0) { throw "Dataset preparation failed" }
    & $python scripts\run_week4_quality_closure.py
    if ($LASTEXITCODE -ne 0) { throw "BGE v2 quality gate failed; Hash remains active" }
    & $python -m ruff check app tests scripts alembic\versions\20261028_0009_week4_quality_gate.py
    & $python -m pytest -q tests\test_week4_quality_closure.py tests\test_week4_rag.py tests\test_week41_quality.py tests\test_week42_week5.py
    if ($LASTEXITCODE -ne 0) { throw "Week 4 quality regression failed" }
    Write-Host "Week 4 quality closure passed; bge-m3-v2 is eligible for activation." -ForegroundColor Green
} finally {
    Remove-Item Env:PERSISTENCE_BACKEND, Env:EMBEDDING_BASE_URL, Env:EMBEDDING_MODEL, Env:EMBEDDING_DIMENSION -ErrorAction SilentlyContinue
    if (-not $KeepServicesRunning) { docker compose stop embedding postgres redis }
}
