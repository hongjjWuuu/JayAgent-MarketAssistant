param([switch]$KeepServicesRunning)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Python virtual environment is missing." }

try {
    docker compose up -d postgres redis migrate seed
    if ($LASTEXITCODE -ne 0) { throw "Infrastructure startup failed" }
    $env:PERSISTENCE_BACKEND = "postgres"
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Alembic upgrade failed" }
    & $python scripts\seed_week41_gold.py
    & $python scripts\seed_week42_gold.py
    if ($LASTEXITCODE -ne 0) { throw "Week 4.2 dataset seed failed" }
    & $python scripts\verify_week42_quality.py
    if ($LASTEXITCODE -ne 0) { throw "Week 4.2 quality gate failed" }
    & $python -m ruff check app tests scripts alembic\versions\20261027_0006_policy_identity.py
    & $python -m pytest -q tests\test_week42_week5.py tests\test_week4_rag.py tests\test_week41_quality.py
    if ($LASTEXITCODE -ne 0) { throw "Week 4.2 tests failed" }
    Write-Host "Week 4.2 verification passed" -ForegroundColor Green
} finally {
    Remove-Item Env:PERSISTENCE_BACKEND -ErrorAction SilentlyContinue
    if (-not $KeepServicesRunning) { docker compose stop postgres redis }
}
