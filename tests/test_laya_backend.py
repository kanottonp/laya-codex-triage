from collections.abc import Mapping

import pytest

from laya_codex_triage.laya_backend import (
    DECISION_QUESTIONS,
    BackendOutputError,
    CheckpointUnavailableError,
    LayaBackend,
)
from laya_codex_triage.models import ModelTier, ReasoningEffort


class FakeAgent:
    def __init__(self, output: object) -> None:
        self.output = output
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def predict(self, prompt: str, questions: Mapping[str, object]) -> object:
        self.calls.append((prompt, questions))
        return self.output


def valid_output() -> dict[str, object]:
    return {
        "answers": {
            "model_tier": {
                "type": "choice",
                "choice": "balanced",
                "probabilities": {"fast": 0.1, "balanced": 0.8, "deep": 0.1},
                "confidence": 0.77,
            },
            "reasoning_effort": {
                "type": "choice",
                "choice": "high",
                "probabilities": {"low": 0.05, "medium": 0.15, "high": 0.7, "xhigh": 0.1},
                "confidence": 0.68,
            },
        }
    }


def test_backend_uses_exact_two_question_schema_and_records_metadata() -> None:
    agent = FakeAgent(valid_output())
    resolver_calls: list[dict[str, object]] = []

    def resolve(checkpoint: str, **kwargs: object) -> str:
        resolver_calls.append({"checkpoint": checkpoint, **kwargs})
        return "/models/laya"

    backend = LayaBackend(
        "convaiinnovations/laya-multilingual",
        "revision-123",
        snapshot_resolver=resolve,
        agent_loader=lambda path: agent if path == "/models/laya" else None,
        timer=lambda: 100.0,
    )
    result = backend.predict("แก้ deployment นี้")

    assert resolver_calls == [
        {
            "checkpoint": "convaiinnovations/laya-multilingual",
            "revision": "revision-123",
            "local_files_only": True,
        }
    ]
    assert agent.calls == [("แก้ deployment นี้", DECISION_QUESTIONS)]
    assert set(DECISION_QUESTIONS) == {"model_tier", "reasoning_effort"}
    assert result.model_tier is ModelTier.BALANCED
    assert result.reasoning_effort is ReasoningEffort.HIGH
    assert set(result.model_tier_distribution) == set(ModelTier)
    assert set(result.reasoning_effort_distribution) == set(ReasoningEffort)
    assert result.model_tier_confidence == 0.77
    assert result.reasoning_effort_confidence == 0.68
    assert result.checkpoint == "convaiinnovations/laya-multilingual"
    assert result.checkpoint_revision == "revision-123"
    assert result.latency_ms == 0.0


def test_backend_rejects_malformed_or_incomplete_output() -> None:
    backend = LayaBackend(
        "checkpoint",
        "revision",
        snapshot_resolver=lambda *_args, **_kwargs: "/models/laya",
        agent_loader=lambda _path: FakeAgent(
            {"answers": {"model_tier": {"choice": "fast", "probabilities": {"fast": 1.0}}}}
        ),
    )

    with pytest.raises(BackendOutputError):
        backend.predict("prompt")


def test_missing_local_checkpoint_is_reported_without_download() -> None:
    calls: list[dict[str, object]] = []

    def missing(checkpoint: str, **kwargs: object) -> str:
        calls.append({"checkpoint": checkpoint, **kwargs})
        raise FileNotFoundError("not cached")

    with pytest.raises(CheckpointUnavailableError):
        LayaBackend("checkpoint", "revision", snapshot_resolver=missing)

    assert calls == [{"checkpoint": "checkpoint", "revision": "revision", "local_files_only": True}]


def test_backend_rejects_any_implicit_download_path() -> None:
    with pytest.raises(ValueError, match="preload"):
        LayaBackend("checkpoint", "revision", local_files_only=False)
