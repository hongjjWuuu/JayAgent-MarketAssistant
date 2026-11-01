$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$env:PERSISTENCE_BACKEND = "postgres"
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = "postgresql+psycopg://market_agent:market_agent@localhost:5433/market_agent" }
if (-not $env:EMBEDDING_BASE_URL) { $env:EMBEDDING_BASE_URL = "http://localhost:7997" }
if (-not $env:EMBEDDING_MODEL) { $env:EMBEDDING_MODEL = "BAAI/bge-m3" }
if (-not $env:EMBEDDING_DIMENSION) { $env:EMBEDDING_DIMENSION = "1024" }
docker compose up -d postgres redis embedding migrate
if ($LASTEXITCODE -ne 0) { throw "Week 5 services or migration failed" }
& $python -m scripts.seed_week5_scenarios_v2
if ($LASTEXITCODE -ne 0) { throw "Week 5 historical v2 seed failed" }
& $python -m scripts.seed_week5_scenarios_v3
if ($LASTEXITCODE -ne 0) { throw "Week 5 dataset seed failed" }
& $python -m scripts.run_week5_agent_evaluation --provider fake --split all --dataset-version v3
if ($LASTEXITCODE -ne 0) { throw "Week 5 Fake Provider evaluation failed" }
Write-Host "Week 5 Fake Provider 80-case evaluation passed" -ForegroundColor Green
