from datetime import UTC, datetime
from pathlib import Path

from laya_codex_triage.capture import CaptureRecord
from laya_codex_triage.laya_backend import BackendOutputError
from laya_codex_triage.models import ModelTier, Prediction, ReasoningEffort
from laya_codex_triage.storage import Storage
from laya_codex_triage.worker import Worker, WorkerStatus


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


class FakeClock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        current = self.value
        self.value += 0.025
        return current

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SequenceBackend:
    def __init__(self, outcomes: list[Prediction | Exception]) -> None:
        self.outcomes = outcomes

    def predict(self, _prompt: str) -> Prediction:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_worker_claims_completes_and_records_latency(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("success"))
    clock = FakeClock()
    worker = Worker(storage, SequenceBackend([prediction()]), monotonic=clock)

    result = worker.run_once()

    assert result.status is WorkerStatus.COMPLETED
    assert result.capture_id == "success"
    assert result.inference_latency_ms == 25.0
    assert storage.get_capture("success").status == "completed"  # type: ignore[union-attr]
    assert storage.count_records("predictions") == 1


def test_worker_retries_three_times_with_exponential_backoff(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("retry"))
    clock = FakeClock()
    worker = Worker(
        storage,
        SequenceBackend([RuntimeError("down")] * 3),
        monotonic=clock,
    )

    results = []
    for expected_delay in (1.0, 2.0, 4.0):
        result = worker.run_once()
        results.append(result)
        assert result.retry_after_seconds == expected_delay
        clock.advance(expected_delay)

    assert [result.status for result in results] == [
        WorkerStatus.RETRY,
        WorkerStatus.RETRY,
        WorkerStatus.RETRY,
    ]
    assert storage.get_capture("retry").status == "failed"  # type: ignore[union-attr]


def test_five_consecutive_failures_open_breaker_with_documented_backoff(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("one"))
    storage.enqueue_capture(capture("two"))
    clock = FakeClock()
    worker = Worker(
        storage,
        SequenceBackend([RuntimeError("down")] * 5),
        monotonic=clock,
    )

    delays = []
    statuses = []
    for expected_delay in (1.0, 2.0, 4.0, 8.0, 300.0):
        result = worker.run_once()
        delays.append(result.retry_after_seconds)
        statuses.append(result.status)
        clock.advance(expected_delay)

    assert delays == [1.0, 2.0, 4.0, 8.0, 300.0]
    assert statuses[-1] is WorkerStatus.BREAKER_OPEN


def test_malformed_output_is_quarantined(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("bad"))
    worker = Worker(storage, SequenceBackend([BackendOutputError("bad output")]))

    result = worker.run_once()

    assert result.status is WorkerStatus.QUARANTINED
    assert storage.get_capture("bad").status == "quarantined"  # type: ignore[union-attr]


def test_success_after_breaker_cooldown_recovers_worker(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    storage.enqueue_capture(capture("one"))
    storage.enqueue_capture(capture("two"))
    storage.enqueue_capture(capture("recovery"))
    clock = FakeClock()
    backend = SequenceBackend([RuntimeError("down")] * 5 + [prediction()])
    worker = Worker(storage, backend, monotonic=clock)

    for delay in (1.0, 2.0, 4.0, 8.0, 300.0):
        result = worker.run_once()
        clock.advance(delay)
    recovered = worker.run_once()

    assert result.status is WorkerStatus.BREAKER_OPEN
    assert recovered.status is WorkerStatus.COMPLETED
    assert worker.consecutive_failures == 0
