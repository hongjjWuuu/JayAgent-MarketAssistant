param([switch]$KeepServicesRunning)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$project = "jayagent-marketassistant-drill"
if ($env:COMPOSE_PROJECT_NAME -and $env:COMPOSE_PROJECT_NAME -ne $project) {
    throw "Refusing to operate on non-drill Compose project: $env:COMPOSE_PROJECT_NAME"
}
$env:COMPOSE_PROJECT_NAME = $project
$compose = @("compose", "--env-file", ".env.m3-drill", "-p", $project)
$api = "http://localhost:18000"
$keycloak = "http://localhost:18080"
$controlHeaders = @{"X-Simulator-Control"="m3-drill-control"}
$results = [System.Collections.Generic.List[object]]::new()

function Add-Result([string]$Name, [datetime]$Started, [string]$Evidence) {
    $results.Add([pscustomobject]@{name=$Name; status="passed"; started_at=$Started.ToString("o"); completed_at=(Get-Date).ToString("o"); evidence=$Evidence})
}
function Wait-Ready {
    $deadline = (Get-Date).AddMinutes(3)
    do {
        try { $ready = Invoke-RestMethod "$api/health/ready" -TimeoutSec 3 } catch { $ready = $null }
        if ($ready.status -eq "ready") { return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "API did not recover before the deadline"
}
function Get-Token([string]$Username) {
    $body = @{client_id="market-assistant-web";grant_type="password";username=$Username;password="demo1234"}
    $deadline = (Get-Date).AddMinutes(1)
    do {
        try {
            return (Invoke-RestMethod "$keycloak/realms/market-assistant/protocol/openid-connect/token" -Method Post -Body $body -TimeoutSec 10).access_token
        } catch {
            if ((Get-Date) -ge $deadline) { throw }
            Start-Sleep -Seconds 2
        }
    } while ($true)
}

$started = Get-Date
& docker @compose stop postgres
Start-Sleep -Seconds 2
try { Invoke-WebRequest "$api/health/ready" -TimeoutSec 5 | Out-Null; throw "Ready endpoint stayed healthy while PostgreSQL was stopped" }
catch { if ($_.Exception.Response.StatusCode.value__ -ne 503) { throw } }
& docker @compose start postgres
Wait-Ready
Add-Result "postgres-outage" $started "readiness=503; recovery=ready"

$started = Get-Date
$token = Get-Token "customer_001"
& docker @compose stop redis
$response = Invoke-WebRequest "$api/api/v1/conversations" -Headers @{Authorization="Bearer $token"} -TimeoutSec 10
if ($response.Headers["X-RateLimit-Mode"] -ne "degraded-open") { throw "Redis outage did not use degraded-open mode" }
& docker @compose start redis
Add-Result "redis-outage" $started "core read succeeded with X-RateLimit-Mode=degraded-open"

$started = Get-Date
Invoke-RestMethod "http://localhost:18090/__control/reset" -Method Post -Headers $controlHeaders | Out-Null
Invoke-RestMethod "http://localhost:18090/__control/faults" -Method Post -Headers $controlHeaders -ContentType "application/json" -Body '{"operation":"create_ticket","mode":"commit_then_timeout","value":4}' | Out-Null
$conversation = Invoke-RestMethod "$api/api/v1/conversations" -Method Post -Headers @{Authorization="Bearer $token"} -ContentType "application/json" -Body '{"customer_id":"customer_001","channel":"web"}'
$key = "m3-drill-$([guid]::NewGuid().ToString('N'))"
$planBody = @{conversation_id=$conversation.conversation_id;action_type="create_ticket";payload=@{title="M3故障演练";description="响应丢失幂等验证"};idempotency_key=$key} | ConvertTo-Json -Depth 5
$plan = Invoke-RestMethod "$api/api/v1/action-plans" -Method Post -Headers @{Authorization="Bearer $token"} -ContentType "application/json" -Body $planBody
$execution = Invoke-RestMethod "$api/api/v1/action-plans/$($plan.action_plan_id)/confirm" -Method Post -Headers @{Authorization="Bearer $token"} -ContentType "application/json" -Body '{"confirmation":"confirm"}'
$deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 2
    $execution = Invoke-RestMethod "$api/api/v1/action-executions/$($execution.execution_id)" -Headers @{Authorization="Bearer $token"}
} while ($execution.status -notin @("succeeded", "failed") -and (Get-Date) -lt $deadline)
$stats = Invoke-RestMethod "http://localhost:18090/__control/stats" -Headers $controlHeaders
if ($execution.status -ne "succeeded" -or $stats.ticket_count -ne 1) { throw "Connector reconciliation drill failed" }
Add-Result "connector-response-loss" $started "execution=succeeded; external_ticket_count=1"

$started = Get-Date
& docker @compose restart api worker
Wait-Ready
$restored = Invoke-RestMethod "$api/api/v1/conversations/$($conversation.conversation_id)" -Headers @{Authorization="Bearer $token"}
if ($restored.conversation_id -ne $conversation.conversation_id) { throw "Conversation did not survive restart" }
Add-Result "api-worker-restart" $started "conversation and durable execution recovered"

$started = Get-Date
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
& $python -m pytest -q --basetemp .pytest-m3-llm tests\test_m3_llm_failure_drills.py
if ($LASTEXITCODE -ne 0) { throw "LLM degradation drill failed" }
Add-Result "llm-safe-degradation" $started "timeout, 429, 500 and invalid JSON preserved deterministic order lookup"

$started = Get-Date
$env:RUN_POSTGRES_TESTS = "1"
$env:TEST_DATABASE_URL = "postgresql+psycopg://market_agent:market_agent@localhost:15433/market_agent"
try {
    & $python -m pytest -q --basetemp .pytest-m3-worker "tests\integration\test_worker_reliability.py::test_forced_process_termination_recovers_after_lease_expiry"
    if ($LASTEXITCODE -ne 0) { throw "Forced worker termination drill failed" }
} finally {
    Remove-Item Env:RUN_POSTGRES_TESTS,Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
}
Add-Result "forced-worker-termination" $started "lease expired; second worker recovered; recovered event persisted"

$started = Get-Date
$database = "market_agent_migration_failure_check"
& docker @compose exec -T postgres dropdb -U market_agent --if-exists $database
& docker @compose exec -T postgres createdb -U market_agent $database
try {
    $head = (& docker @compose exec -T postgres psql -U market_agent -d market_agent -Atc "select version_num from alembic_version").Trim()
    & docker @compose exec -T postgres psql -v ON_ERROR_STOP=1 -U market_agent -d $database -c "create table alembic_version(version_num varchar(32) primary key); insert into alembic_version values ('$head');"
    $before = (& docker @compose exec -T postgres psql -U market_agent -d $database -Atc "select version_num from alembic_version").Trim()
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        & docker @compose exec -T postgres psql -v ON_ERROR_STOP=1 -U market_agent -d $database -c "begin; create table m3_partial_table(id int); select * from missing_relation; commit;" 2>$null
        $expectedFailureExit = $LASTEXITCODE
    } finally {
        $PSNativeCommandUseErrorActionPreference = $true
    }
    if ($expectedFailureExit -eq 0) { throw "Intentional migration transaction unexpectedly succeeded" }
    $after = (& docker @compose exec -T postgres psql -U market_agent -d $database -Atc "select version_num from alembic_version").Trim()
    $partial = (& docker @compose exec -T postgres psql -U market_agent -d $database -Atc "select count(*) from information_schema.tables where table_name='m3_partial_table'").Trim()
    if ($before -ne $after -or $partial -ne "0") { throw "Failed migration left partial schema changes" }
} finally {
    & docker @compose exec -T postgres dropdb -U market_agent --if-exists $database
}
Add-Result "transactional-migration-failure" $started "revision unchanged; partial table absent"

$output = Join-Path $projectRoot "output\m3"
New-Item -ItemType Directory -Force $output | Out-Null
$results | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 (Join-Path $output "failure-drills.json")
Write-Host "M3 failure drills passed: $($results.Count)"
