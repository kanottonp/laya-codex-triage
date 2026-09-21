"""Import-isolated adapter for the official Laya runtime."""

import threading
import time
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from .models import ModelTier, Prediction, ReasoningEffort

DECISION_QUESTIONS: dict[str, object] = {
    "model_tier": {
        "type": "choice",
        "instructions": "Which Codex model tier should handle this repository-backed prompt?",
        "criteria": {
            "fast": "Direct, narrow, low-risk work with obvious verification and little ambiguity.",
            "balanced": (
                "Ordinary implementation, diagnosis, review, or research requiring exploration "
                "and judgment."
            ),
            "deep": (
                "Ambiguous, cross-system, architecture-, security-, production-, financial-, "
                "migration-, or data-sensitive work."
            ),
        },
    },
    "reasoning_effort": {
        "type": "choice",
        "instructions": "How much reasoning effort does this repository-backed prompt require?",
        "criteria": {
            "low": "Steps and validation are obvious.",
            "medium": "Some exploration and judgment are required.",
            "high": "Dependencies, trade-offs, or verification are complex.",
            "xhigh": (
                "Architecture, security, production, financial, migration, destructive, or "
                "data-sensitive decisions dominate."
            ),
        },
    },
}


class BackendOutputError(ValueError):
    pass


class CheckpointUnavailableError(FileNotFoundError):
    pass


class DecisionBackend(Protocol):
    def predict(self, prompt: str) -> Prediction: ...


class _Agent(Protocol):
    def predict(self, prompt: str, questions: Mapping[str, object]) -> object: ...


class _SnapshotResolver(Protocol):
    def __call__(self, checkpoint: str, *, revision: str, local_files_only: bool) -> str: ...


class _AgentLoader(Protocol):
    def __call__(self, path: str) -> _Agent: ...


class LayaBackend:
    def __init__(
        self,
        checkpoint: str,
        revision: str,
        local_files_only: bool = True,
        *,
        snapshot_resolver: _SnapshotResolver | None = None,
        agent_loader: _AgentLoader | None = None,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not local_files_only:
            raise ValueError("LayaBackend cannot download checkpoints; use explicit preload")
        self.checkpoint = checkpoint
        self.revision = revision
        self._timer = timer
        self._lock = threading.Lock()
        resolver = snapshot_resolver or _snapshot_download
        try:
            model_path = resolver(
                checkpoint,
                revision=revision,
                local_files_only=local_files_only,
            )
        except Exception as error:
            raise CheckpointUnavailableError(
                f"checkpoint {checkpoint}@{revision} is not available locally"
            ) from error
        loader = agent_loader or _load_laya_agent
        self._agent = loader(model_path)

    def predict(self, prompt: str) -> Prediction:
        with self._lock:
            started = self._timer()
            try:
                output = self._agent.predict(prompt, DECISION_QUESTIONS)
                answers = _mapping(output, "output")["answers"]
                answer_map = _mapping(answers, "answers")
                tier_answer = _choice_answer(answer_map.get("model_tier"), ModelTier, "model_tier")
                effort_answer = _choice_answer(
                    answer_map.get("reasoning_effort"), ReasoningEffort, "reasoning_effort"
                )
                tier_choice, tier_distribution, tier_confidence = tier_answer
                effort_choice, effort_distribution, effort_confidence = effort_answer
            except BackendOutputError:
                raise
            except Exception as error:
                raise BackendOutputError("invalid Laya decision output") from error

            finished = self._timer()
            latency_ms = max(0.0, round((finished - started) * 1_000, 3))
            abstention_reason = _abstention_reason(tier_distribution, effort_distribution)
            return Prediction(
                model_tier=ModelTier(tier_choice),
                reasoning_effort=ReasoningEffort(effort_choice),
                model_tier_distribution={
                    ModelTier(key): value for key, value in tier_distribution.items()
                },
                reasoning_effort_distribution={
                    ReasoningEffort(key): value for key, value in effort_distribution.items()
                },
                checkpoint=self.checkpoint,
                checkpoint_revision=self.revision,
                model_tier_confidence=tier_confidence,
                reasoning_effort_confidence=effort_confidence,
                latency_ms=latency_ms,
                abstained=abstention_reason is not None,
                abstention_reason=abstention_reason,
            )


def _snapshot_download(checkpoint: str, *, revision: str, local_files_only: bool) -> str:
    huggingface_hub = import_module("huggingface_hub")
    return cast(
        str,
        huggingface_hub.snapshot_download(
            checkpoint,
            revision=revision,
            local_files_only=local_files_only,
        ),
    )


def _load_laya_agent(path: str) -> _Agent:
    if not Path(path).is_dir():
        raise FileNotFoundError(path)
    laya = import_module("laya")
    return cast(_Agent, laya.load(path))


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BackendOutputError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise BackendOutputError(f"{name} keys must be strings")
    return cast(Mapping[str, object], value)


def _choice_answer(
    value: object,
    choices: type[ModelTier] | type[ReasoningEffort],
    name: str,
) -> tuple[str, dict[str, float], float]:
    answer = _mapping(value, name)
    if answer.get("type") != "choice":
        raise BackendOutputError(f"{name} must be a choice answer")
    choice = answer.get("choice")
    valid_choices = {candidate.value for candidate in choices}
    if not isinstance(choice, str) or choice not in valid_choices:
        raise BackendOutputError(f"{name} has an invalid choice")
    raw_distribution = _mapping(answer.get("probabilities"), f"{name}.probabilities")
    if set(raw_distribution) != valid_choices:
        raise BackendOutputError(f"{name} must include a full distribution")
    try:
        distribution = {key: _number(value) for key, value in raw_distribution.items()}
        confidence = _number(answer["confidence"])
    except (KeyError, TypeError, ValueError) as error:
        raise BackendOutputError(f"{name} contains non-numeric values") from error
    if any(value < 0 or value > 1 for value in distribution.values()) or not 0 <= confidence <= 1:
        raise BackendOutputError(f"{name} probabilities or confidence are out of range")
    total = sum(distribution.values())
    if total <= 0:
        raise BackendOutputError(f"{name} distribution is empty")
    normalized = {key: probability / total for key, probability in distribution.items()}
    return choice, normalized, confidence


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BackendOutputError("expected a numeric value")
    return float(value)


def _abstention_reason(
    tier_distribution: Mapping[str, float], effort_distribution: Mapping[str, float]
) -> str | None:
    for name, distribution in (
        ("model_tier", tier_distribution),
        ("reasoning_effort", effort_distribution),
    ):
        ordered = sorted(distribution.values(), reverse=True)
        if ordered[0] < 0.80:
            return f"{name}:top_probability"
        if ordered[0] - ordered[1] < 0.20:
            return f"{name}:margin"
    return None
