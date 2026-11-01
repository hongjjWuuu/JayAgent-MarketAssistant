param(
    [switch]$SkipDocker,
    [switch]$RunLiveDeepSeekSmoke
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Create .venv before running M3 verification." }

& $python -m ruff check app tests
& $python -m pytest -q tests --ignore=tests/integration
Push-Location (Join-Path $projectRoot "web")
try {
    npm test -- --run
    npm run build
}
finally { Pop-Location }

if (-not $SkipDocker) {
    docker compose config --quiet
    & (Join-Path $PSScriptRoot "verify_fresh_migration.ps1")
}
if ($RunLiveDeepSeekSmoke) {
    & (Join-Path $PSScriptRoot "verify_week5.ps1") -RunLiveDeepSeekSmoke
}
Write-Host "M3 closeout verification passed."

