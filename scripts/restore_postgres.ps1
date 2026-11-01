param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
if (-not $ConfirmRestore) {
    throw "Restore replaces the pilot database. Re-run with -ConfirmRestore after verifying the backup path."
}
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$resolvedBackup = [System.IO.Path]::GetFullPath($BackupFile)
if (-not (Test-Path -LiteralPath $resolvedBackup -PathType Leaf)) {
    throw "Backup file does not exist: $resolvedBackup"
}
$containerFile = "/tmp/market-agent-restore.dump"

docker compose cp $resolvedBackup "postgres:$containerFile"
try {
    docker compose exec -T postgres pg_restore -U market_agent -d market_agent --clean --if-exists --no-owner --no-acl $containerFile
}
finally {
    docker compose exec -T postgres rm -f $containerFile
}
docker compose run --rm migrate
Write-Host "Restore and migration completed. Run verify_m3_closeout.ps1 before reopening the pilot."


