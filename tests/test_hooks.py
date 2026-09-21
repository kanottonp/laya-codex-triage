import json
from pathlib import Path


def test_bundled_user_prompt_submit_hook_uses_capture_wrapper() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    hook_config = json.loads((repository_root / "hooks" / "hooks.json").read_text(encoding="utf-8"))

    handlers = hook_config["hooks"]["UserPromptSubmit"][0]["hooks"]

    assert len(handlers) == 1
    assert handlers[0]["type"] == "command"
    assert "PLUGIN_ROOT" in handlers[0]["command"]
    assert "scripts/capture-prompt" in handlers[0]["command"]
    assert handlers[0]["timeout"] <= 1
    assert "additionalContext" not in handlers[0]
