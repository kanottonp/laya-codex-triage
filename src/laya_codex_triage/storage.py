"""Durable SQLite/WAL queue and experiment persistence."""

import json
import os
import sqlite3
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from .capture import CaptureRecord
from .models import ModelTier, Prediction, ReasoningEffort, RouteDecision

SCHEMA_VERSION = 1
DEFAULT_MAX_PENDING_CAPTURES = 10_000
MAX_PENDING_CAPTURES = DEFAULT_MAX_PENDING_CAPTURES
_SCHEMA_LOCK = threading.Lock()
_COUNTABLE_TABLES = frozenset(
    {"captures", "predictions", "labels", "calibrations", "route_decisions"}
)


class UnsupportedSchemaVersion(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QueuedCapture:
    capture_id: str
    captured_at: str
    repository_name: str
    repository_fingerprint: str
    session_hash: str
    turn_hash: str
    active_model: str
    permission_mode: str
    prompt: str
    prompt_truncated: bool
    redaction_count: int
    redaction_version: str
    schema_version: str
    failure_count: int


@dataclass(frozen=True, slots=True)
class StoredCapture:
    capture_id: str
    captured_at: str
    prompt: str | None
    status: str
    failure_count: int
    last_error_code: str | None


class Storage:
    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._connection = connection
        self.path = path

    @classmethod
    def open(cls, path: Path, role: Literal["capture", "mcp"]) -> "Storage":
        timeout_ms = 25 if role == "capture" else 2_000
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        connection = sqlite3.connect(
            path,
            timeout=timeout_ms / 1_000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
        with _SCHEMA_LOCK:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                connection.close()
                raise UnsupportedSchemaVersion(
                    f"database schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version == 0:
                schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
                connection.executescript(schema)
            elif version < SCHEMA_VERSION:
                connection.close()
                raise UnsupportedSchemaVersion("destructive or unknown migration requires approval")
        connection.execute("PRAGMA journal_mode = WAL")
        os.chmod(path, 0o600)
        return cls(connection, path)

    @property
    def journal_mode(self) -> str:
        return str(self._connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    @property
    def busy_timeout_ms(self) -> int:
        return int(self._connection.execute("PRAGMA busy_timeout").fetchone()[0])

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def enqueue_capture(self, record: CaptureRecord) -> bool:
        with self._transaction():
            existing = self._connection.execute(
                "SELECT 1 FROM captures WHERE capture_id = ?", (record.capture_id,)
            ).fetchone()
            if existing is not None:
                return True
            queued = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM captures WHERE status IN ('pending', 'claimed')"
                ).fetchone()[0]
            )
            if queued >= MAX_PENDING_CAPTURES:
                self._increment_metadata("dropped_captures")
                return False
            self._connection.execute(
                """
                INSERT INTO captures(
                    capture_id, captured_at, repository_name, repository_fingerprint,
                    session_hash, turn_hash, active_model, permission_mode, prompt,
                    prompt_truncated, redaction_count, redaction_version, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.capture_id,
                    record.captured_at,
                    record.repository_name,
                    record.repository_fingerprint,
                    record.session_hash,
                    record.turn_hash,
                    record.active_model,
                    record.permission_mode,
                    record.prompt,
                    int(record.prompt_truncated),
                    record.redaction_count,
                    record.redaction_version,
                    record.schema_version,
                ),
            )
        return True

    def claim_pending(self, limit: int = 1) -> list[QueuedCapture]:
        if limit < 1:
            return []
        with self._transaction():
            rows = self._connection.execute(
                """
                SELECT * FROM captures
                WHERE status = 'pending'
                ORDER BY captured_at, capture_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            if not rows:
                return []
            capture_ids = [str(row["capture_id"]) for row in rows]
            claimed_at = datetime.now(UTC).isoformat()
            self._connection.executemany(
                "UPDATE captures SET status = 'claimed', claimed_at = ? "
                "WHERE capture_id = ? AND status = 'pending'",
                ((claimed_at, capture_id) for capture_id in capture_ids),
            )
        return [self._queued_capture(row) for row in rows]

    def complete_prediction(
        self,
        capture_id: str,
        prediction: Prediction,
        *,
        queue_delay_ms: float,
        inference_latency_ms: float,
    ) -> None:
        if prediction.latency_ms is None:
            prediction = prediction.model_copy(update={"latency_ms": inference_latency_ms})
        predicted_at = datetime.now(UTC).isoformat()
        payload = prediction.model_dump(mode="json")
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO predictions(
                    capture_id, predicted_at, checkpoint, checkpoint_revision,
                    decision_schema_version, calibration_version, model_tier,
                    reasoning_effort, model_tier_distribution,
                    reasoning_effort_distribution, abstained, abstention_reason,
                    queue_delay_ms, inference_latency_ms, prediction_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(capture_id) DO UPDATE SET
                    predicted_at = excluded.predicted_at,
                    checkpoint = excluded.checkpoint,
                    checkpoint_revision = excluded.checkpoint_revision,
                    decision_schema_version = excluded.decision_schema_version,
                    calibration_version = excluded.calibration_version,
                    model_tier = excluded.model_tier,
                    reasoning_effort = excluded.reasoning_effort,
                    model_tier_distribution = excluded.model_tier_distribution,
                    reasoning_effort_distribution = excluded.reasoning_effort_distribution,
                    abstained = excluded.abstained,
                    abstention_reason = excluded.abstention_reason,
                    queue_delay_ms = excluded.queue_delay_ms,
                    inference_latency_ms = excluded.inference_latency_ms,
                    prediction_json = excluded.prediction_json
                """,
                (
                    capture_id,
                    predicted_at,
                    prediction.checkpoint,
                    prediction.checkpoint_revision,
                    prediction.decision_schema_version,
                    prediction.calibration_version,
                    prediction.model_tier.value,
                    prediction.reasoning_effort.value,
                    json.dumps(payload["model_tier_distribution"], sort_keys=True),
                    json.dumps(payload["reasoning_effort_distribution"], sort_keys=True),
                    int(prediction.abstained),
                    prediction.abstention_reason,
                    queue_delay_ms,
                    inference_latency_ms,
                    json.dumps(payload, sort_keys=True),
                ),
            )
            self._connection.execute(
                "UPDATE captures SET status = 'completed', last_error_code = NULL "
                "WHERE capture_id = ?",
                (capture_id,),
            )

    def fail_capture(self, capture_id: str, error_code: str) -> None:
        with self._transaction():
            row = self._connection.execute(
                "SELECT failure_count FROM captures WHERE capture_id = ?", (capture_id,)
            ).fetchone()
            if row is None:
                return
            failure_count = int(row["failure_count"]) + 1
            if error_code.startswith(("malformed", "corrupt")):
                status = "quarantined"
            else:
                status = "failed" if failure_count >= 3 else "pending"
            self._connection.execute(
                """
                UPDATE captures
                SET failure_count = ?, status = ?, last_error_code = ?, claimed_at = NULL
                WHERE capture_id = ?
                """,
                (failure_count, status, error_code, capture_id),
            )

    def save_label(
        self,
        capture_id: str,
        model_tier: ModelTier,
        reasoning_effort: ReasoningEffort,
        *,
        note: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> None:
        reviewed = (reviewed_at or datetime.now(UTC)).isoformat()
        self._connection.execute(
            """
            INSERT INTO labels(capture_id, model_tier, reasoning_effort, note, reviewed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(capture_id) DO UPDATE SET
                model_tier = excluded.model_tier,
                reasoning_effort = excluded.reasoning_effort,
                note = excluded.note,
                reviewed_at = excluded.reviewed_at
            """,
            (capture_id, model_tier.value, reasoning_effort.value, note, reviewed),
        )

    def save_route_decision(
        self,
        decision: RouteDecision,
        *,
        capture_id: str | None = None,
        user_override: dict[str, str] | None = None,
        launch_outcome: str | None = None,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO route_decisions(
                capture_id, decided_at, operating_mode, suggested_tier, suggested_effort,
                policy_tier, policy_effort, model_slug, catalog_version, applied_rule,
                user_override_json, launch_outcome, decision_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capture_id,
                datetime.now(UTC).isoformat(),
                decision.effective_mode.value,
                decision.suggested_model_tier.value,
                decision.suggested_reasoning_effort.value,
                decision.policy_model_tier.value,
                decision.policy_reasoning_effort.value,
                decision.model_slug,
                decision.catalog_version,
                decision.applied_rule,
                json.dumps(user_override, sort_keys=True) if user_override else None,
                launch_outcome,
                decision.model_dump_json(),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("route decision insert did not return an id")
        return cursor.lastrowid

    def purge_expired(self, *, now: datetime, retention_days: int = 30) -> int:
        cutoff = (now - timedelta(days=retention_days)).isoformat()
        cursor = self._connection.execute(
            "UPDATE captures SET prompt = NULL WHERE captured_at < ? AND prompt IS NOT NULL",
            (cutoff,),
        )
        return int(cursor.rowcount)

    def get_capture(self, capture_id: str) -> StoredCapture | None:
        row = self._connection.execute(
            """
            SELECT capture_id, captured_at, prompt, status, failure_count, last_error_code
            FROM captures WHERE capture_id = ?
            """,
            (capture_id,),
        ).fetchone()
        if row is None:
            return None
        return StoredCapture(
            capture_id=str(row["capture_id"]),
            captured_at=str(row["captured_at"]),
            prompt=str(row["prompt"]) if row["prompt"] is not None else None,
            status=str(row["status"]),
            failure_count=int(row["failure_count"]),
            last_error_code=(
                str(row["last_error_code"]) if row["last_error_code"] is not None else None
            ),
        )

    def pending_count(self) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) FROM captures WHERE status = 'pending'"
            ).fetchone()[0]
        )

    def count_records(self, table: str) -> int:
        if table not in _COUNTABLE_TABLES:
            raise ValueError(f"unsupported table: {table}")
        return int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def metadata_int(self, key: str) -> int:
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return int(row["value"]) if row is not None else 0

    def recent_predictions(self, limit: int = 20) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT c.capture_id, c.captured_at, c.repository_name, c.prompt,
                   p.prediction_json, p.inference_latency_ms, p.queue_delay_ms,
                   l.model_tier AS label_model_tier,
                   l.reasoning_effort AS label_reasoning_effort
            FROM captures AS c
            JOIN predictions AS p USING(capture_id)
            LEFT JOIN labels AS l USING(capture_id)
            ORDER BY c.captured_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _increment_metadata(self, key: str) -> None:
        self._connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES (?, '1')
            ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1
            """,
            (key,),
        )

    def _transaction(self) -> AbstractContextManager[None]:
        return _ImmediateTransaction(self._connection)

    @staticmethod
    def _queued_capture(row: sqlite3.Row) -> QueuedCapture:
        prompt = row["prompt"]
        if prompt is None:
            raise ValueError("pending capture cannot have a purged prompt")
        return QueuedCapture(
            capture_id=str(row["capture_id"]),
            captured_at=str(row["captured_at"]),
            repository_name=str(row["repository_name"]),
            repository_fingerprint=str(row["repository_fingerprint"]),
            session_hash=str(row["session_hash"]),
            turn_hash=str(row["turn_hash"]),
            active_model=str(row["active_model"]),
            permission_mode=str(row["permission_mode"]),
            prompt=str(prompt),
            prompt_truncated=bool(row["prompt_truncated"]),
            redaction_count=int(row["redaction_count"]),
            redaction_version=str(row["redaction_version"]),
            schema_version=str(row["schema_version"]),
            failure_count=int(row["failure_count"]),
        )


class _ImmediateTransaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def __exit__(self, exception_type: object, _exception: object, _traceback: object) -> None:
        if exception_type is None:
            self._connection.execute("COMMIT")
        else:
            self._connection.execute("ROLLBACK")


def purge_main() -> int:
    """Console entry point; destructive scope is added with confirmed MCP operations."""

    return 0
