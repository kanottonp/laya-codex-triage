"""Local, shadow-mode triage for repository-backed Codex prompts."""

from pathlib import Path

__version__ = "0.1.4"
DEFAULT_CHECKPOINT = "convaiinnovations/laya-multilingual"
DEFAULT_CHECKPOINT_REVISION = "052592a15d198d9ad47da779604259b10b47b7aa"
_LOCAL_DATA = (Path(__file__).resolve().parent.parent.parent / "data").resolve()
_USER_DATA = Path.home() / ".codex" / "plugins" / "data" / "knt-laya"
DEFAULT_PLUGIN_DATA = (
    _LOCAL_DATA if _LOCAL_DATA.exists() else _USER_DATA
)
