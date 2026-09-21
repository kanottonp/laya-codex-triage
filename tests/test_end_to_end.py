import asyncio
import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from laya_codex_triage.capture import CaptureRecord, capture_hook
from laya_codex_triage.mcp_server import create_mcp_server
from laya_codex_triage.models import ModelTier, Prediction, ReasoningEffort
from laya_codex_triage.storage import Storage
from laya_codex_triage.worker import Worker


class FakeBackend:
    def predict(self, _prompt: str) -> Prediction:
        tier_distribution = {candidate: 0.1 for candidate in ModelTier}
        effort_distribution = {candidate: 0.1 for candidate in ReasoningEffort}
        tier_distribution[ModelTier.BALANCED] = 0.8
        effort_distribution[ReasoningEffort.MEDIUM] = 0.7
        return Prediction(
            model_tier=ModelTier.BALANCED,
            reasoning_effort=ReasoningEffort.MEDIUM,
            model_tier_distribution=tier_distribution,
            reasoning_effort_distribution=effort_distribution,
            checkpoint="fake/checkpoint",
            checkpoint_revision="abc123",
        )


def test_shadow_pipeline_skips_projectless_and_never_writes_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, stdout=subprocess.DEVNULL)
    before_files = sorted(path.name for path in repository.iterdir())
    before_status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    plugin_data = tmp_path / "plugin-data"
    payload = {
        "cwd": str(repository),
        "session_id": "session-id",
        "turn_id": "turn-id",
        "model": "gpt-5.6-terra",
        "permission_mode": "default",
        "prompt": "Deploy API_KEY=top-secret safely",
    }
    assert capture_hook(io.StringIO(json.dumps(payload)), {"PLUGIN_DATA": str(plugin_data)}) == 0
    assert (
        capture_hook(
            io.StringIO(json.dumps({**payload, "cwd": str(tmp_path / "projectless")})),
            {"PLUGIN_DATA": str(plugin_data)},
        )
        == 0
    )

    storage = Storage.open(plugin_data / "triage.sqlite3", role="mcp")
    assert storage.count_records("captures") == 1
    Worker(storage, FakeBackend()).run_once()
    assert storage.count_records("predictions") == 1

    server = create_mcp_server(storage)
    recent = asyncio.run(server.call_tool("triage_recent", {"limit": 1, "include_prompt": True}))
    item = recent.structured_content["items"][0]
    assert "top-secret" not in item["prompt"]
    asyncio.run(
        server.call_tool(
            "triage_label",
            {
                "capture_id": item["capture_id"],
                "model_tier": "balanced",
                "reasoning_effort": "medium",
            },
        )
    )
    report = asyncio.run(server.call_tool("triage_report", {}))
    assert report.structured_content["sample_size"] == 1

    old_capture = CaptureRecord(
        capture_id="old-capture",
        captured_at=(datetime.now(UTC) - timedelta(days=31)).isoformat(),
        repository_name="old-repository",
        repository_fingerprint="old-fingerprint",
        session_hash="old-session",
        turn_hash="old-turn",
        active_model="gpt-5.6-terra",
        permission_mode="default",
        prompt="old redacted prompt",
        prompt_truncated=False,
        redaction_count=0,
    )
    storage.enqueue_capture(old_capture)
    old_claim = storage.claim_pending()[0]
    storage.complete_prediction(
        old_claim.capture_id,
        FakeBackend().predict(old_claim.prompt),
        queue_delay_ms=1,
        inference_latency_ms=1,
    )
    purged = asyncio.run(server.call_tool("triage_purge", {"confirmation": "PURGE"}))
    assert purged.structured_content["purged"] == 1
    assert storage.get_capture("old-capture").prompt is None

    after_files = sorted(path.name for path in repository.iterdir())
    after_status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert before_files == after_files
    assert before_status == after_status
