param(
    [string]$OutputDirectory = ".\backups"
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$candidateOutput = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $projectRoot $OutputDirectory
}
$resolvedOutput = [System.IO.Path]::GetFullPath($candidateOutput)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path $resolvedOutput "market-agent-$stamp.dump"
$containerFile = "/tmp/market-agent-backup.dump"

docker compose exec -T postgres pg_dump -U market_agent -d market_agent -Fc --no-owner --no-acl -f $containerFile
docker compose cp "postgres:$containerFile" $target
docker compose exec -T postgres rm -f $containerFile
if (-not (Test-Path -LiteralPath $target) -or (Get-Item -LiteralPath $target).Length -eq 0) {
    throw "PostgreSQL backup was not created."
}
Write-Host "Backup created: $target"
Write-Output $target

