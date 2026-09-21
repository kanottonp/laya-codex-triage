import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from laya_codex_triage.capture import CaptureRecord
from laya_codex_triage.storage import Storage


def capture(index: int) -> CaptureRecord:
    return CaptureRecord(
        capture_id=f"capture-{index:03d}",
        captured_at=datetime.now(UTC).isoformat(),
        repository_name="repo",
        repository_fingerprint="repo-hash",
        session_hash="session-hash",
        turn_hash=f"turn-{index}",
        active_model="gpt-5.6-terra",
        permission_mode="default",
        prompt=f"prompt-{index}",
        prompt_truncated=False,
        redaction_count=0,
    )


def test_separate_wal_writer_and_readers_claim_without_duplicates(tmp_path: Path) -> None:
    database = tmp_path / "triage.sqlite3"
    writer_done = threading.Event()
    claimed: list[str] = []
    claimed_lock = threading.Lock()

    def write_records() -> None:
        storage = Storage.open(database, role="capture")
        for index in range(100):
            storage.enqueue_capture(capture(index))
        writer_done.set()

    def claim_records() -> None:
        storage = Storage.open(database, role="mcp")
        while not writer_done.is_set() or storage.pending_count() > 0:
            batch = storage.claim_pending(limit=5)
            with claimed_lock:
                claimed.extend(item.capture_id for item in batch)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(write_records), executor.submit(claim_records)]
        for future in futures:
            future.result(timeout=10)

    assert len(claimed) == 100
    assert len(set(claimed)) == 100
