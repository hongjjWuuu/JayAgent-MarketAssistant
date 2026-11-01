param([int]$Chunks = 10000, [int]$Queries = 50)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Python virtual environment is missing." }
Set-Location $projectRoot
$env:PERSISTENCE_BACKEND = "postgres"
try {
    docker compose up -d postgres migrate
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL startup or migration failed" }
    & $python scripts\verify_retrieval_performance.py --chunks $Chunks --queries $Queries
    if ($LASTEXITCODE -ne 0) { throw "Retrieval performance gate failed" }
} finally {
    Remove-Item Env:PERSISTENCE_BACKEND -ErrorAction SilentlyContinue
}
