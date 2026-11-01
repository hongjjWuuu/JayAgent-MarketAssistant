param([switch]$Activate)
$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe -m scripts.seed_week4_v4_reviewed_datasets
$arguments = @(
    "-m", "scripts.run_week4_profile_v3",
    "--profile", "bge-m3-v4",
    "--parent", "bge-m3-v3",
    "--calibration", "week4_gate_calibration/v2",
    "--gold", "week4_gold/v3",
    "--holdout", "week4_hard_negative/v3"
)
if ($Activate) { $arguments += "--activate" }
& .\.venv\Scripts\python.exe @arguments
