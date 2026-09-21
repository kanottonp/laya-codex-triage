"""Local, shadow-mode triage for repository-backed Codex prompts."""

from pathlib import Path

__version__ = "0.1.2"
DEFAULT_CHECKPOINT = "convaiinnovations/laya-multilingual"
DEFAULT_CHECKPOINT_REVISION = "052592a15d198d9ad47da779604259b10b47b7aa"
DEFAULT_PLUGIN_DATA = Path.home() / ".codex" / "plugins" / "data" / "knt-laya"
