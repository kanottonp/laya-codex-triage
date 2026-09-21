import json
import socket
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from urllib.request import urlopen

from laya_codex_triage.capture import CaptureRecord
from laya_codex_triage.dashboard import DashboardController
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
        prompt="review prompt",
        prompt_truncated=False,
        redaction_count=0,
    )


def free_loopback_port() -> int:
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        return int(socket_.getsockname()[1])


def test_dashboard_includes_pending_capture_from_shared_storage(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("pending"))
    dashboard = DashboardController(storage, port=free_loopback_port())
    dashboard.start()
    try:
        with urlopen(f"{dashboard.url}/api/captures") as response:
            item = json.load(response)["items"][0]
    finally:
        dashboard.close()

    assert item["capture_id"] == "pending"
    assert item["status"] == "pending"
    assert item["prediction"] == {}


def test_dashboard_html_is_bundled_package_resource() -> None:
    assert files("laya_codex_triage.resources").joinpath("label_dashboard.html").is_file()
