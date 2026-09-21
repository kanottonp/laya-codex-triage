"""Persistent queue consumer with bounded retries and a circuit breaker."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .laya_backend import BackendOutputError, DecisionBackend
from .storage import Storage

BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 300.0)


class WorkerStatus(StrEnum):
    IDLE = "idle"
    COMPLETED = "completed"
    RETRY = "retry"
    QUARANTINED = "quarantined"
    BREAKER_OPEN = "breaker_open"


@dataclass(frozen=True, slots=True)
class WorkerResult:
    status: WorkerStatus
    capture_id: str | None = None
    inference_latency_ms: float | None = None
    retry_after_seconds: float | None = None
    error_code: str | None = None


class Worker:
    def __init__(
        self,
        storage: Storage,
        backend: DecisionBackend,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._storage = storage
        self._backend = backend
        self._monotonic = monotonic
        self._next_attempt_at = 0.0
        self.consecutive_failures = 0

    def run_once(self) -> WorkerResult:
        now = self._monotonic()
        if now < self._next_attempt_at:
            return WorkerResult(
                status=WorkerStatus.BREAKER_OPEN,
                retry_after_seconds=max(0.0, self._next_attempt_at - now),
            )

        captures = self._storage.claim_pending(limit=1)
        if not captures:
            return WorkerResult(status=WorkerStatus.IDLE)
        capture = captures[0]
        started = self._monotonic()
        try:
            prediction = self._backend.predict(capture.prompt)
        except Exception as error:
            malformed = isinstance(error, BackendOutputError)
            error_code = "malformed_output" if malformed else type(error).__name__
            self._storage.fail_capture(capture.capture_id, error_code)
            self.consecutive_failures += 1
            delay = BACKOFF_SECONDS[min(self.consecutive_failures - 1, len(BACKOFF_SECONDS) - 1)]
            self._next_attempt_at = self._monotonic() + delay
            if malformed:
                status = WorkerStatus.QUARANTINED
            elif self.consecutive_failures >= 5:
                status = WorkerStatus.BREAKER_OPEN
            else:
                status = WorkerStatus.RETRY
            return WorkerResult(
                status=status,
                capture_id=capture.capture_id,
                retry_after_seconds=delay,
                error_code=error_code,
            )

        finished = self._monotonic()
        inference_latency_ms = round((finished - started) * 1_000, 3)
        captured_at = datetime.fromisoformat(capture.captured_at)
        queue_delay_ms = max(0.0, (datetime.now(UTC) - captured_at).total_seconds() * 1_000)
        self._storage.complete_prediction(
            capture.capture_id,
            prediction,
            queue_delay_ms=queue_delay_ms,
            inference_latency_ms=inference_latency_ms,
        )
        self.consecutive_failures = 0
        self._next_attempt_at = 0.0
        return WorkerResult(
            status=WorkerStatus.COMPLETED,
            capture_id=capture.capture_id,
            inference_latency_ms=inference_latency_ms,
        )

    def run_forever(self, idle_sleep_seconds: float = 0.25) -> None:
        while True:
            result = self.run_once()
            if result.status is WorkerStatus.IDLE:
                time.sleep(idle_sleep_seconds)
            elif result.retry_after_seconds:
                time.sleep(result.retry_after_seconds)
