import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_compatibility_manifest_declares_plugin_components() -> None:
    manifest_path = REPOSITORY_ROOT / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["name"] == "knt-laya"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert (REPOSITORY_ROOT / manifest["skills"]).is_dir()
    assert (REPOSITORY_ROOT / manifest["mcpServers"]).is_file()
    assert (REPOSITORY_ROOT / "hooks").is_dir()
