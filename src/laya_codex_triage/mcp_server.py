"""MCP tools for local triage review, reporting, and confirmed purge."""

import json
import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from . import DEFAULT_PLUGIN_DATA
from .laya_backend import DecisionBackend
from .models import ModelTier, ReasoningEffort
from .reporting import ReportObservation, build_report
from .storage import Storage


class TriageService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def health(self) -> dict[str, object]:
        return {
            "database": "ok",
            "captures": self._storage.count_records("captures"),
            "pending": self._storage.pending_count(),
            "predictions": self._storage.count_records("predictions"),
            "labels": self._storage.count_records("labels"),
            "dropped_captures": self._storage.metadata_int("dropped_captures"),
            "retention_days": 30,
            "mode": "shadow",
        }

    def recent(self, *, limit: int = 20, include_prompt: bool = False) -> dict[str, object]:
        rows = self._storage.recent_predictions(limit=max(1, min(limit, 100)))
        items = [self._item(row, include_prompt=include_prompt) for row in rows]
        return {"items": items}

    def sample_for_review(self, *, limit: int = 10) -> dict[str, object]:
        rows = [
            row
            for row in self._storage.recent_predictions(limit=1_000)
            if row["label_model_tier"] is None
        ]
        sampled: list[dict[str, object]] = []
        groups: dict[tuple[object, object], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            prediction = self._prediction_json(row)
            groups[(row["repository_name"], prediction.get("model_tier"))].append(row)
        for group_rows in groups.values():
            group_rows.sort(key=self._margin)
        while len(sampled) < min(max(1, limit), 100):
            extended = False
            for group_rows in groups.values():
                if group_rows:
                    sampled.append(self._item(group_rows.pop(0), include_prompt=True))
                    extended = True
                    if len(sampled) >= limit:
                        break
            if not extended:
                break
        return {"items": sampled}

    def label(
        self,
        capture_id: str,
        model_tier: ModelTier,
        reasoning_effort: ReasoningEffort,
        *,
        note: str | None = None,
    ) -> dict[str, object]:
        rows = self._storage.recent_predictions(limit=1_000)
        if not any(row["capture_id"] == capture_id for row in rows):
            raise ValueError(f"unknown completed capture: {capture_id}")
        self._storage.save_label(capture_id, model_tier, reasoning_effort, note=note)
        return {
            "capture_id": capture_id,
            "model_tier": model_tier.value,
            "reasoning_effort": reasoning_effort.value,
            "note": note,
        }

    def report(self) -> dict[str, object]:
        rows = self._storage.recent_predictions(limit=1_000)
        observations = [
            self._report_observation(row) for row in rows if row["label_model_tier"] is not None
        ]
        report = build_report(observations)
        return {
            "sample_size": report.sample_size,
            "labeled_size": report.labeled_size,
            "model_tier_exact_match": report.model_tier_exact_match,
            "reasoning_effort_exact_match": report.reasoning_effort_exact_match,
            "reasoning_effort_within_one": report.reasoning_effort_within_one,
            "dangerous_downgrades": report.dangerous_downgrades,
            "routing_opportunity": report.routing_opportunity,
            "confusion_matrices": {
                "model_tier": _nested_counts(report.confusion_matrices.model_tier, "model_tier"),
                "reasoning_effort": _nested_counts(
                    report.confusion_matrices.reasoning_effort, "reasoning_effort"
                ),
            },
            "latency": {"p50_ms": report.latency.p50_ms, "p95_ms": report.latency.p95_ms},
        }

    def purge(self, confirmation: Literal["PURGE"]) -> dict[str, object]:
        from datetime import UTC, datetime

        purged = self._storage.purge_expired(now=datetime.now(UTC), retention_days=30)
        return {"confirmation": confirmation, "purged": purged}

    @staticmethod
    def _item(row: dict[str, object], *, include_prompt: bool) -> dict[str, object]:
        prediction = TriageService._prediction_json(row)
        item: dict[str, object] = {
            "capture_id": row["capture_id"],
            "captured_at": row["captured_at"],
            "repository_name": row["repository_name"],
            "prediction": prediction,
            "label_model_tier": row["label_model_tier"],
            "label_reasoning_effort": row["label_reasoning_effort"],
            "inference_latency_ms": row["inference_latency_ms"],
        }
        if include_prompt:
            item["prompt"] = row["prompt"]
        return item

    @staticmethod
    def _report_observation(row: dict[str, object]) -> ReportObservation:
        prediction = TriageService._prediction_json(row)
        return ReportObservation(
            capture_id=str(row["capture_id"]),
            predicted_model_tier=ModelTier(prediction["model_tier"]),
            predicted_reasoning_effort=ReasoningEffort(prediction["reasoning_effort"]),
            human_model_tier=ModelTier(_string(row["label_model_tier"])),
            human_reasoning_effort=ReasoningEffort(_string(row["label_reasoning_effort"])),
            model_tier_top_probability=max(prediction["model_tier_distribution"].values()),
            reasoning_effort_top_probability=max(
                prediction["reasoning_effort_distribution"].values()
            ),
            inference_latency_ms=_float(row["inference_latency_ms"]),
            queue_delay_ms=_float(row["queue_delay_ms"]),
            error_code=None,
        )

    @staticmethod
    def _prediction_json(row: dict[str, object]) -> dict[str, Any]:
        value = json.loads(str(row["prediction_json"]))
        if not isinstance(value, dict):
            raise ValueError("prediction_json must contain an object")
        if value.get("latency_ms") is None and row.get("inference_latency_ms") is not None:
            value["latency_ms"] = _float(row["inference_latency_ms"])
        return value

    @staticmethod
    def _margin(row: dict[str, object]) -> float:
        prediction = TriageService._prediction_json(row)
        probabilities = sorted(prediction["model_tier_distribution"].values(), reverse=True)
        return float(probabilities[0]) - float(probabilities[1])


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string database value")
    return value


def _float(value: object) -> float:
    if not isinstance(value, int | float):
        raise ValueError("expected a numeric database value")
    return float(value)


def create_mcp_server(storage: Storage) -> MCPServer[None]:
    service = TriageService(storage)
    server: MCPServer[None] = MCPServer(
        "laya-codex-triage",
        description="Local shadow-mode triage review and reporting",
        version="0.1.3",
    )

    def triage_health() -> dict[str, object]:
        """Return queue, prediction, label, retention, and local mode health."""

        return service.health()

    def triage_recent(limit: int = 20, include_prompt: bool = False) -> dict[str, object]:
        """Return recent predictions; prompt text is omitted unless explicitly requested."""

        return service.recent(limit=limit, include_prompt=include_prompt)

    def triage_sample_for_review(limit: int = 10) -> dict[str, object]:
        """Return a repository- and tier-stratified sample of unlabeled predictions."""

        return service.sample_for_review(limit=limit)

    def triage_label(
        capture_id: str,
        model_tier: ModelTier,
        reasoning_effort: ReasoningEffort,
        note: str | None = None,
    ) -> dict[str, object]:
        """Create or update the two human labels for one completed prediction."""

        return service.label(capture_id, model_tier, reasoning_effort, note=note)

    def triage_report() -> dict[str, object]:
        """Return aggregate accuracy, confusion, latency, safety, and opportunity metrics."""

        return service.report()

    def triage_purge(confirmation: Literal["PURGE"]) -> dict[str, object]:
        """Apply 30-day prompt retention after exact confirmation."""

        return service.purge(confirmation)

    for tool in (
        triage_health,
        triage_recent,
        triage_sample_for_review,
        triage_label,
        triage_report,
        triage_purge,
    ):
        server.add_tool(tool)
    return server


def _nested_counts(counts: object, question: str) -> dict[str, dict[str, int]]:
    choices = _count_fields(question)
    return {
        predicted: {human: int(getattr(getattr(counts, predicted), human)) for human in choices}
        for predicted in choices
        if hasattr(counts, predicted)
    }


def _count_fields(question: str) -> tuple[str, ...]:
    if question == "model_tier":
        return ("fast", "balanced", "deep")
    return ("low", "medium", "high", "xhigh")


def start_background_worker(
    storage: Storage,
    backend: DecisionBackend | None = None,
    *,
    idle_sleep_seconds: float = 0.25,
) -> threading.Thread | None:
    if os.environ.get("LAYA_DISABLE_BACKGROUND_WORKER", "").lower() in ("1", "true", "yes"):
        return None
    from . import DEFAULT_CHECKPOINT, DEFAULT_CHECKPOINT_REVISION
    from .laya_backend import LayaBackend
    from .worker import Worker

    try:
        resolved_backend = backend or LayaBackend(
            DEFAULT_CHECKPOINT, DEFAULT_CHECKPOINT_REVISION, local_files_only=True
        )
    except Exception:
        return None

    worker = Worker(storage, resolved_backend)
    thread = threading.Thread(
        target=worker.run_forever,
        kwargs={"idle_sleep_seconds": idle_sleep_seconds},
        daemon=True,
        name="laya-worker-daemon",
    )
    thread.start()
    return thread


def main() -> int:
    plugin_data_value = os.environ.get("PLUGIN_DATA")
    plugin_data = Path(plugin_data_value) if plugin_data_value else DEFAULT_PLUGIN_DATA
    storage = Storage.open(plugin_data / "triage.sqlite3", role="mcp")
    start_background_worker(storage)
    create_mcp_server(storage).run("stdio")
    return 0
