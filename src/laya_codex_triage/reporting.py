"""Aggregate review and routing evidence without exposing prompt text."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import Field

from .models import FrozenModel, ModelTier, ReasoningEffort

TIER_VALUES = tuple(candidate.value for candidate in ModelTier)
EFFORT_VALUES = tuple(candidate.value for candidate in ReasoningEffort)


class ReportObservation(FrozenModel):
    capture_id: str = Field(min_length=1)
    predicted_model_tier: ModelTier
    predicted_reasoning_effort: ReasoningEffort
    human_model_tier: ModelTier
    human_reasoning_effort: ReasoningEffort
    model_tier_top_probability: float = Field(ge=0, le=1)
    reasoning_effort_top_probability: float = Field(ge=0, le=1)
    inference_latency_ms: float = Field(ge=0)
    queue_delay_ms: float = Field(ge=0)
    error_code: str | None = None


@dataclass(slots=True)
class TierCounts:
    fast: int = 0
    balanced: int = 0
    deep: int = 0


@dataclass(slots=True)
class EffortCounts:
    low: int = 0
    medium: int = 0
    high: int = 0
    xhigh: int = 0


@dataclass(slots=True)
class TierConfusion:
    fast: TierCounts = field(default_factory=TierCounts)
    balanced: TierCounts = field(default_factory=TierCounts)
    deep: TierCounts = field(default_factory=TierCounts)


@dataclass(slots=True)
class EffortConfusion:
    low: EffortCounts = field(default_factory=EffortCounts)
    medium: EffortCounts = field(default_factory=EffortCounts)
    high: EffortCounts = field(default_factory=EffortCounts)
    xhigh: EffortCounts = field(default_factory=EffortCounts)


@dataclass(slots=True)
class ConfusionMatrices:
    model_tier: TierConfusion = field(default_factory=TierConfusion)
    reasoning_effort: EffortConfusion = field(default_factory=EffortConfusion)


@dataclass(frozen=True, slots=True)
class LatencySummary:
    p50_ms: float
    p95_ms: float


@dataclass(frozen=True, slots=True)
class TriageReport:
    sample_size: int
    labeled_size: int
    model_tier_exact_match: float
    reasoning_effort_exact_match: float
    reasoning_effort_within_one: float
    dangerous_downgrades: int
    routing_opportunity: float
    confusion_matrices: ConfusionMatrices
    latency: LatencySummary


def build_report(observations: Sequence[ReportObservation]) -> TriageReport:
    if not observations:
        return TriageReport(
            sample_size=0,
            labeled_size=0,
            model_tier_exact_match=0.0,
            reasoning_effort_exact_match=0.0,
            reasoning_effort_within_one=0.0,
            dangerous_downgrades=0,
            routing_opportunity=0.0,
            confusion_matrices=ConfusionMatrices(),
            latency=LatencySummary(p50_ms=0.0, p95_ms=0.0),
        )

    tier_confusion = TierConfusion()
    effort_confusion = EffortConfusion()
    labeled = [observation for observation in observations if observation.error_code is None]
    tier_matches = 0
    effort_matches = 0
    effort_within_one = 0
    dangerous = 0
    downgrades = 0
    for observation in labeled:
        predicted_tier = observation.predicted_model_tier.value
        human_tier = observation.human_model_tier.value
        predicted_effort = observation.predicted_reasoning_effort.value
        human_effort = observation.human_reasoning_effort.value
        getattr(tier_confusion, predicted_tier).__setattr__(
            human_tier, getattr(getattr(tier_confusion, predicted_tier), human_tier) + 1
        )
        getattr(effort_confusion, predicted_effort).__setattr__(
            human_effort, getattr(getattr(effort_confusion, predicted_effort), human_effort) + 1
        )
        tier_matches += predicted_tier == human_tier
        effort_matches += predicted_effort == human_effort
        effort_within_one += (
            abs(EFFORT_VALUES.index(predicted_effort) - EFFORT_VALUES.index(human_effort)) <= 1
        )
        dangerous += predicted_tier == "fast" and human_tier == "deep"
        downgrades += TIER_VALUES.index(predicted_tier) < TIER_VALUES.index(human_tier)

    latencies = sorted(observation.inference_latency_ms for observation in observations)
    return TriageReport(
        sample_size=len(observations),
        labeled_size=len(labeled),
        model_tier_exact_match=tier_matches / len(labeled) if labeled else 0.0,
        reasoning_effort_exact_match=effort_matches / len(labeled) if labeled else 0.0,
        reasoning_effort_within_one=effort_within_one / len(labeled) if labeled else 0.0,
        dangerous_downgrades=dangerous,
        routing_opportunity=downgrades / len(labeled) if labeled else 0.0,
        confusion_matrices=ConfusionMatrices(
            model_tier=tier_confusion,
            reasoning_effort=effort_confusion,
        ),
        latency=LatencySummary(
            p50_ms=_percentile(latencies, 0.50),
            p95_ms=_percentile(latencies, 0.95),
        ),
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile
    lower = math_floor(position)
    upper = math_ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def math_floor(value: float) -> int:
    return int(value)


def math_ceil(value: float) -> int:
    lower = int(value)
    return lower if lower == value else lower + 1
