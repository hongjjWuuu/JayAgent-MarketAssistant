from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = (
    re.compile(r"(?:sk-|github_pat_|gh[pousr]_)[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?im)^(?:LLM_API_KEY|DEEPSEEK_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN)="
        r"(?!\s*$|replace-|<)[^\s]+$"
    ),
)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    candidates = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode().split("\0")
    findings: list[str] = []
    for relative in candidates:
        if not relative or relative == "scripts/scan_repository_secrets.py":
            continue
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in PATTERNS:
            if pattern.search(content):
                findings.append(relative)
                break
    if findings:
        raise SystemExit("Potential committed secret in: " + ", ".join(sorted(findings)))
    print(f"Secret scan passed for {len(candidates) - 1} committed/candidate paths")


if __name__ == "__main__":
    main()
