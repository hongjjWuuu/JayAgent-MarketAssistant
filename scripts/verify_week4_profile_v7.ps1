param([switch]$Activate)
$ErrorActionPreference = "Stop"
$arguments = @(
    "-m", "scripts.run_week4_profile_v3",
    "--profile", "bge-m3-v7",
    "--parent", "bge-m3-v6",
    "--calibration", "week4_gate_calibration/v2",
    "--gold", "week4_gold/v3",
    "--holdout", "week4_hard_negative/v3"
)
if ($Activate) { $arguments += "--activate" }
& .\.venv\Scripts\python.exe @arguments
