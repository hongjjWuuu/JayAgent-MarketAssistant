param([switch]$Activate)
$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe -m scripts.seed_week4_gold_v5
$arguments = @(
    "-m", "scripts.run_week4_profile_v3",
    "--profile", "bge-m3-v10",
    "--parent", "bge-m3-v9",
    "--calibration", "week4_gate_calibration/v2",
    "--gold", "week4_gold/v5",
    "--holdout", "week4_hard_negative/v3"
)
if ($Activate) { $arguments += "--activate" }
& .\.venv\Scripts\python.exe @arguments
