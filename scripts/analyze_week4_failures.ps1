param([string]$Profile = "bge-m3-v1", [string]$RunId = "")

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Python virtual environment is missing." }
Set-Location $projectRoot
$env:PERSISTENCE_BACKEND = "postgres"
try {
    if ($RunId) { & $python scripts\analyze_week4_failures.py --profile $Profile --run-id $RunId }
    else { & $python scripts\analyze_week4_failures.py --profile $Profile }
    if ($LASTEXITCODE -ne 0) { throw "Failure analysis failed" }
} finally {
    Remove-Item Env:PERSISTENCE_BACKEND -ErrorAction SilentlyContinue
}
