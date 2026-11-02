param(
    [string]$DatabaseUrl = "postgresql+psycopg://market_agent:market_agent@localhost:15433/market_agent",
    [string]$EmbeddingBaseUrl = "http://localhost:17997",
    [int]$EmbeddingReadyTimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Create .venv before initializing M3 retrieval." }

$env:PERSISTENCE_BACKEND = "postgres"
$env:DATABASE_URL = $DatabaseUrl

# The compatibility column on policy_chunk is vector(1536), so canonical
# policy publication first uses the deterministic Hash baseline. BGE vectors
# are then written to the multi-model policy_chunk_embedding table.
$env:EMBEDDING_PROVIDER = "hash"
Remove-Item Env:EMBEDDING_BASE_URL,Env:EMBEDDING_API_KEY,Env:EMBEDDING_MODEL,Env:EMBEDDING_DIMENSION -ErrorAction SilentlyContinue
& $python -m scripts.seed_week41_gold
& $python -m scripts.seed_week42_gold
& $python -m scripts.seed_week4_v4_reviewed_datasets
& $python -m scripts.seed_week4_gold_v4
& $python -m scripts.seed_week4_gold_v5

$readiness = (& $python -m scripts.check_m3_retrieval_readiness | ConvertFrom-Json)
if ($readiness.ready) {
    Write-Host "M3 retrieval baseline is already active and complete; no rebuild required." -ForegroundColor Green
    return
}

$deadline = (Get-Date).AddSeconds($EmbeddingReadyTimeoutSeconds)
do {
    try {
        $health = Invoke-RestMethod "$EmbeddingBaseUrl/health" -TimeoutSec 5
    } catch {
        $health = $null
    }
    if ($health) { break }
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $deadline)
if (-not $health) { throw "Embedding service did not become ready at $EmbeddingBaseUrl" }

$env:EMBEDDING_PROVIDER = "openai_compatible"
$env:EMBEDDING_BASE_URL = $EmbeddingBaseUrl
$env:EMBEDDING_MODEL = "BAAI/bge-m3"
$env:EMBEDDING_DIMENSION = "1024"
Remove-Item Env:EMBEDDING_API_KEY -ErrorAction SilentlyContinue
& (Join-Path $PSScriptRoot "verify_week4_profile_v10.ps1") -Activate

Write-Host "M3 retrieval baseline rebuilt and bge-m3-v10 activated." -ForegroundColor Green
