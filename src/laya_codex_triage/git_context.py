"""Timeout-bounded Git repository discovery for prompt capture."""

import os
import subprocess
from pathlib import Path

DEFAULT_GIT_LOOKUP_TIMEOUT_SECONDS = 0.1
GIT_LOOKUP_TIMEOUT_SECONDS = DEFAULT_GIT_LOOKUP_TIMEOUT_SECONDS


def resolve_git_root(cwd: str, *, timeout: float | None = None) -> Path | None:
    effective_timeout = (
        timeout
        if timeout is not None
        else float(
            os.environ.get("LAYA_GIT_LOOKUP_TIMEOUT_SECONDS", DEFAULT_GIT_LOOKUP_TIMEOUT_SECONDS)
        )
    )
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=effective_timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root).resolve() if root else None
