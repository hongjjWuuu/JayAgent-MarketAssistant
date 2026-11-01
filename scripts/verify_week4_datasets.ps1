$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe -m scripts.seed_week4_v4_datasets
& .\.venv\Scripts\python.exe -m scripts.verify_week4_datasets
