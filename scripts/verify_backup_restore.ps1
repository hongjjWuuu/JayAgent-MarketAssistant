$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$database = "market_agent_restore_check"
$containerFile = "/tmp/market-agent-restore-check.dump"
$backupDirectory = Join-Path $projectRoot "backups\verification"

$backupOutput = & (Join-Path $PSScriptRoot "backup_postgres.ps1") -OutputDirectory $backupDirectory
$backupFile = @($backupOutput | Where-Object { Test-Path -LiteralPath $_ })[-1]
if (-not $backupFile) { throw "Backup verification could not locate the generated dump." }

docker compose exec -T postgres dropdb -U market_agent --if-exists $database
docker compose exec -T postgres createdb -U market_agent $database
docker compose cp $backupFile "postgres:$containerFile"
try {
    docker compose exec -T postgres pg_restore -U market_agent -d $database --no-owner --no-acl $containerFile
    $revision = docker compose exec -T postgres psql -U market_agent -d $database -Atc "select version_num from alembic_version"
    $tenantCount = docker compose exec -T postgres psql -U market_agent -d $database -Atc "select count(*) from tenant"
    if ($revision.Trim() -ne "20261031_0019" -or [int]$tenantCount -lt 1) {
        throw "Restored database failed revision or tenant verification."
    }
    Write-Host "Backup and isolated restore verification passed: $backupFile"
}
finally {
    docker compose exec -T postgres rm -f $containerFile
    docker compose exec -T postgres dropdb -U market_agent --if-exists $database
}

