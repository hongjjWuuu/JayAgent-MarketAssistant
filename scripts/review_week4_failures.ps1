param([string]$Profile = "bge-m3-v2")
$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe -m scripts.review_week4_failures --profile $Profile
