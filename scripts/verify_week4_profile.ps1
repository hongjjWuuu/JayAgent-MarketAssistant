param(
    [string]$Profile = "bge-m3-v3",
    [string]$Calibration = "week4_gate_calibration/v1",
    [string]$Gold = "week4_gold/v3",
    [string]$Holdout = "week4_hard_negative/v2",
    [string]$Parent = "bge-m3-v2",
    [switch]$Activate
)
$ErrorActionPreference = "Stop"
if ($Profile -in @("bge-m3-v1", "bge-m3-v2")) {
    throw "Historical rejected profiles are read-only."
}
& .\.venv\Scripts\python.exe -m scripts.seed_week4_v4_datasets
& .\.venv\Scripts\python.exe -m scripts.verify_week4_datasets
$arguments = @("-m", "scripts.run_week4_profile_v3", "--profile", $Profile, "--calibration", $Calibration, "--gold", $Gold, "--holdout", $Holdout, "--parent", $Parent)
if ($Activate) { $arguments += "--activate" }
& .\.venv\Scripts\python.exe @arguments
