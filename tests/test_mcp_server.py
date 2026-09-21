import asyncio
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from laya_codex_triage.capture import CaptureRecord
from laya_codex_triage.dashboard import DashboardController
from laya_codex_triage.mcp_server import create_mcp_server
from laya_codex_triage.models import ModelTier, Prediction, ReasoningEffort
from laya_codex_triage.storage import Storage


def capture(capture_id: str) -> CaptureRecord:
    return CaptureRecord(
        capture_id=capture_id,
        captured_at=datetime.now(UTC).isoformat(),
        repository_name="repo",
        repository_fingerprint="repo-hash",
        session_hash="session-hash",
        turn_hash=f"turn-{capture_id}",
        active_model="gpt-5.6-terra",
        permission_mode="default",
        prompt=f"review prompt {capture_id}",
        prompt_truncated=False,
        redaction_count=0,
    )


def prediction() -> Prediction:
    return Prediction(
        model_tier=ModelTier.BALANCED,
        reasoning_effort=ReasoningEffort.MEDIUM,
        model_tier_distribution={
            ModelTier.FAST: 0.1,
            ModelTier.BALANCED: 0.8,
            ModelTier.DEEP: 0.1,
        },
        reasoning_effort_distribution={
            ReasoningEffort.LOW: 0.1,
            ReasoningEffort.MEDIUM: 0.7,
            ReasoningEffort.HIGH: 0.1,
            ReasoningEffort.XHIGH: 0.1,
        },
        checkpoint="fake/checkpoint",
        checkpoint_revision="abc123",
    )


def seeded_storage(tmp_path: Path) -> Storage:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("sample"))
    claimed = storage.claim_pending()[0]
    storage.complete_prediction(
        claimed.capture_id,
        prediction(),
        queue_delay_ms=1.0,
        inference_latency_ms=2.0,
    )
    return storage


def call(server: object, name: str, arguments: dict[str, object] | None = None) -> object:
    return asyncio.run(server.call_tool(name, arguments or {}))  # type: ignore[attr-defined]


def test_tool_schemas_hide_prompt_by_default_and_allow_explicit_review(tmp_path: Path) -> None:
    server = create_mcp_server(seeded_storage(tmp_path))
    tools = asyncio.run(server.list_tools())
    tool_map = {tool.name: tool for tool in tools}

    assert set(tool_map) == {
        "triage_health",
        "triage_recent",
        "triage_sample_for_review",
        "triage_label",
        "triage_report",
        "triage_purge",
        "triage_dashboard",
    }
    assert "mode" not in tool_map["triage_label"].input_schema.get("properties", {})
    assert not any(name.startswith("triage_set_") for name in tool_map)

    hidden = call(server, "triage_recent", {"limit": 1})
    visible = call(server, "triage_recent", {"limit": 1, "include_prompt": True})

    assert "prompt" not in hidden.structured_content["items"][0]  # type: ignore[attr-defined]
    assert visible.structured_content["items"][0]["prompt"] == "review prompt sample"  # type: ignore[attr-defined]
    assert hidden.structured_content["items"][0]["inference_latency_ms"] == 2.0  # type: ignore[attr-defined]
    assert hidden.structured_content["items"][0]["prediction"]["latency_ms"] == 2.0  # type: ignore[attr-defined]


def test_review_sample_label_upsert_and_report(tmp_path: Path) -> None:
    server = create_mcp_server(seeded_storage(tmp_path))

    sample = call(server, "triage_sample_for_review", {"limit": 1})
    item = sample.structured_content["items"][0]  # type: ignore[attr-defined]
    assert item["prompt"] == "review prompt sample"

    call(
        server,
        "triage_label",
        {
            "capture_id": item["capture_id"],
            "model_tier": "balanced",
            "reasoning_effort": "high",
            "note": "initial",
        },
    )
    updated = call(
        server,
        "triage_label",
        {
            "capture_id": item["capture_id"],
            "model_tier": "deep",
            "reasoning_effort": "high",
            "note": "corrected",
        },
    )
    report = call(server, "triage_report")

    assert updated.structured_content["note"] == "corrected"  # type: ignore[attr-defined]
    assert report.structured_content["sample_size"] == 1  # type: ignore[attr-defined]
    assert report.structured_content["confusion_matrices"]["model_tier"]["balanced"]["deep"] == 1  # type: ignore[attr-defined]


def test_purge_requires_exact_confirmation_and_reports_count(tmp_path: Path) -> None:
    server = create_mcp_server(seeded_storage(tmp_path))

    try:
        call(server, "triage_purge", {"confirmation": "DELETE"})
    except ToolError:
        pass
    else:
        raise AssertionError("purge accepted an unconfirmed request")

    result = call(server, "triage_purge", {"confirmation": "PURGE"})
    assert result.structured_content["purged"] == 0  # type: ignore[attr-defined]


def test_start_background_worker_consumes_queue(tmp_path: Path) -> None:
    import time

    from laya_codex_triage.mcp_server import start_background_worker

    class SequenceBackend:
        def __init__(self, items: list[Prediction]) -> None:
            self.items = items

        def predict(self, _p: str) -> Prediction:
            return self.items.pop(0)

    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("bg-test"))
    backend = SequenceBackend([prediction()])

    thread = start_background_worker(storage, backend=backend, idle_sleep_seconds=0.01)
    assert thread is not None
    assert thread.is_alive()

    for _ in range(50):
        if storage.pending_count() == 0 and storage.count_records("predictions") == 1:
            break
        time.sleep(0.02)

    assert storage.pending_count() == 0
    assert storage.count_records("predictions") == 1


def test_dashboard_tool_starts_once_and_returns_controller_url(tmp_path: Path) -> None:
    storage = seeded_storage(tmp_path)
    dashboard = DashboardController(storage, port=0)
    server = create_mcp_server(storage, dashboard=dashboard)
    try:
        first = call(server, "triage_dashboard")
        second = call(server, "triage_dashboard")
    finally:
        dashboard.close()

    assert first.structured_content == {"url": dashboard.url, "started": True}  # type: ignore[attr-defined]
    assert second.structured_content == {"url": dashboard.url, "started": False}  # type: ignore[attr-defined]


def test_dashboard_tool_reports_fixed_port_collision(tmp_path: Path) -> None:
    occupant = HTTPServer(("127.0.0.1", 11020), BaseHTTPRequestHandler)
    try:
        server = create_mcp_server(seeded_storage(tmp_path))
        try:
            call(server, "triage_dashboard")
        except ToolError as error:
            assert "11020" in str(error)
        else:
            raise AssertionError("dashboard tool started despite occupied port")
        assert occupant.fileno() >= 0
    finally:
        occupant.server_close()
