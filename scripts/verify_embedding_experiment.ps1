param(
    [string]$Profile = "bge-m3-v1",
    [switch]$KeepServicesRunning
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
try {
    docker compose up -d postgres redis embedding migrate seed worker
    if ($LASTEXITCODE -ne 0) { throw "Embedding stack startup failed" }
    $env:PERSISTENCE_BACKEND = "postgres"
    $env:EMBEDDING_PROVIDER = "openai_compatible"
    $env:EMBEDDING_BASE_URL = "http://localhost:7997"
    $env:EMBEDDING_MODEL = "BAAI/bge-m3"
    $env:EMBEDDING_DIMENSION = "1024"
    & $python -m alembic upgrade head
    & $python scripts\seed_week42_gold.py
    & $python scripts\run_embedding_experiment.py --profile $Profile
    if ($LASTEXITCODE -ne 0) { throw "BGE profile quality gate failed; keep Hash active" }
    Write-Host "Embedding experiment passed for $Profile. Activation still requires the admin API." -ForegroundColor Green
} finally {
    Remove-Item Env:PERSISTENCE_BACKEND, Env:EMBEDDING_PROVIDER, Env:EMBEDDING_BASE_URL, Env:EMBEDDING_MODEL, Env:EMBEDDING_DIMENSION -ErrorAction SilentlyContinue
    if (-not $KeepServicesRunning) { docker compose stop embedding worker }
}
