param([switch]$KeepDatabase)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$database = "market_agent_migration_check"
if ($database -notmatch '^market_agent_[a-z_]+$') { throw "Unsafe verification database name." }

docker compose up -d --wait postgres
docker compose exec -T postgres dropdb -U market_agent --if-exists $database
docker compose exec -T postgres createdb -U market_agent $database
try {
    docker compose run --build --rm -e "DATABASE_URL=postgresql+psycopg://market_agent:market_agent@postgres:5432/$database" migrate
    docker compose run --build --rm -e "DATABASE_URL=postgresql+psycopg://market_agent:market_agent@postgres:5432/$database" seed
    $revision = docker compose exec -T postgres psql -U market_agent -d $database -Atc "select version_num from alembic_version"
    if (-not $revision -or $revision.Trim() -ne "20261031_0019") {
        throw "Unexpected Alembic revision: $revision"
    }
    $tableCount = docker compose exec -T postgres psql -U market_agent -d $database -Atc "select count(*) from information_schema.tables where table_schema='public'"
    if ([int]$tableCount -lt 25) { throw "Fresh schema is incomplete: only $tableCount tables." }
    $requiredIndexes = docker compose exec -T postgres psql -U market_agent -d $database -Atc "select count(*) from pg_indexes where indexname in ('ix_policy_chunk_hash_cosine','ix_policy_chunk_embedding_bge_v10_cosine')"
    if ([int]$requiredIndexes -ne 2) { throw "Required retrieval indexes are missing." }
    $memberCount = docker compose exec -T postgres psql -U market_agent -d $database -Atc "select count(*) from tenant_member"
    if ([int]$memberCount -lt 4) { throw "Demo seed did not create all identities." }
    Write-Host "Fresh 0001 -> 0019 migration and seed passed."
}
finally {
    if (-not $KeepDatabase) { docker compose exec -T postgres dropdb -U market_agent --if-exists $database }
}

