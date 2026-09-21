import json
import sqlite3
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import laya_codex_triage.storage as storage_module
from laya_codex_triage.capture import CaptureRecord
from laya_codex_triage.models import ModelTier, Prediction, ReasoningEffort
from laya_codex_triage.storage import Storage, UnsupportedSchemaVersion


def capture(capture_id: str, *, captured_at: datetime | None = None) -> CaptureRecord:
    return CaptureRecord(
        capture_id=capture_id,
        captured_at=(captured_at or datetime.now(UTC)).isoformat(),
        repository_name="repo",
        repository_fingerprint="repo-hash",
        session_hash="session-hash",
        turn_hash=f"turn-{capture_id}",
        active_model="gpt-5.6-terra",
        permission_mode="default",
        prompt=f"prompt-{capture_id}",
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


def test_open_sets_permissions_wal_and_role_timeouts(tmp_path: Path) -> None:
    database = tmp_path / "plugin-data" / "triage.sqlite3"

    capture_storage = Storage.open(database, role="capture")
    mcp_storage = Storage.open(database, role="mcp")

    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert capture_storage.journal_mode == "wal"
    assert capture_storage.busy_timeout_ms == 25
    assert mcp_storage.busy_timeout_ms == 2_000


def test_claim_pending_is_fifo_and_exactly_once(tmp_path: Path) -> None:
    database = tmp_path / "triage.sqlite3"
    writer = Storage.open(database, role="capture")
    reader_one = Storage.open(database, role="mcp")
    reader_two = Storage.open(database, role="mcp")
    now = datetime.now(UTC)
    assert writer.enqueue_capture(capture("second", captured_at=now + timedelta(seconds=1)))
    assert writer.enqueue_capture(capture("first", captured_at=now))

    first_claim = reader_one.claim_pending(limit=1)
    second_claim = reader_two.claim_pending(limit=1)
    no_third_claim = reader_one.claim_pending(limit=1)

    assert [item.capture_id for item in first_claim] == ["first"]
    assert [item.capture_id for item in second_claim] == ["second"]
    assert no_third_claim == []


def test_recent_predictions_includes_capture_status_for_dashboard(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("awaiting-prediction"))

    rows = storage.recent_predictions()

    assert rows[0]["capture_id"] == "awaiting-prediction"
    assert rows[0]["status"] == "pending"


def test_failed_capture_retries_three_times_then_stops(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("retry-me"))

    for expected_count in (1, 2):
        assert storage.claim_pending()[0].capture_id == "retry-me"
        storage.fail_capture("retry-me", "temporary")
        state = storage.get_capture("retry-me")
        assert state is not None
        assert state.failure_count == expected_count
        assert state.status == "pending"

    assert storage.claim_pending()[0].capture_id == "retry-me"
    storage.fail_capture("retry-me", "temporary")
    state = storage.get_capture("retry-me")
    assert state is not None
    assert state.failure_count == 3
    assert state.status == "failed"
    assert storage.claim_pending() == []


def test_queue_limit_drops_new_capture_and_counts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage_module, "MAX_PENDING_CAPTURES", 2)
    storage = Storage.open(tmp_path / "triage.sqlite3", role="capture")

    assert storage.enqueue_capture(capture("one")) is True
    assert storage.enqueue_capture(capture("two")) is True
    assert storage.enqueue_capture(capture("three")) is False
    assert storage.metadata_int("dropped_captures") == 1
    assert storage_module.DEFAULT_MAX_PENDING_CAPTURES == 10_000


def test_purge_removes_old_prompt_but_preserves_prediction_and_label(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    old = datetime.now(UTC) - timedelta(days=31)
    storage.enqueue_capture(capture("old", captured_at=old))
    storage.claim_pending()
    storage.complete_prediction("old", prediction(), queue_delay_ms=1.0, inference_latency_ms=2.0)
    storage.save_label("old", ModelTier.BALANCED, ReasoningEffort.MEDIUM, note="correct")

    purged = storage.purge_expired(now=datetime.now(UTC), retention_days=30)
    record = storage.get_capture("old")

    assert purged == 1
    assert record is not None and record.prompt is None
    assert storage.count_records("predictions") == 1
    assert storage.count_records("labels") == 1
    recent = storage.recent_predictions(limit=1)
    saved = json.loads(str(recent[0]["prediction_json"]))
    assert saved["latency_ms"] == 2.0


def test_open_rejects_newer_or_destructive_schema(tmp_path: Path) -> None:
    database = tmp_path / "triage.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 2")
    connection.close()

    with pytest.raises(UnsupportedSchemaVersion):
        Storage.open(database, role="mcp")


def test_duplicate_capture_id_is_idempotent(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="capture")
    original = capture("same")

    assert storage.enqueue_capture(original) is True
    assert storage.enqueue_capture(replace(original, prompt="different")) is True
    assert storage.count_records("captures") == 1
