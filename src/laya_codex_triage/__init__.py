"""Local, shadow-mode triage for repository-backed Codex prompts."""

from collections.abc import Mapping
from pathlib import Path

__version__ = "0.1.4"
DEFAULT_CHECKPOINT = "convaiinnovations/laya-multilingual"
DEFAULT_CHECKPOINT_REVISION = "052592a15d198d9ad47da779604259b10b47b7aa"
DEFAULT_PLUGIN_DATA = Path.home() / ".codex" / "plugins" / "data" / "knt-laya"


def resolve_plugin_data(environ: Mapping[str, str]) -> Path:
    """Return Codex's plugin-data directory, or the local development fallback."""

    value = environ.get("PLUGIN_DATA")
    return Path(value) if value else DEFAULT_PLUGIN_DATA
