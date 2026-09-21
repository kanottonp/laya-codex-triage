"""Timeout-bounded Git repository discovery for prompt capture."""

import subprocess
from pathlib import Path

GIT_LOOKUP_TIMEOUT_SECONDS = 0.04


def resolve_git_root(cwd: str) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=GIT_LOOKUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root).resolve() if root else None
