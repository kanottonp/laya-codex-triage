"""Held-out temperature calibration for the two triage questions."""

import math
from collections import defaultdict
from collections.abc import Sequence

from pydantic import Field

from .models import FrozenModel, ModelTier, ReasoningEffort

MIN_LABELS = 50
MIN_HOLDOUT = 10
PRECISION_THRESHOLD = 0.90
THRESHOLD_CANDIDATES = tuple(round(value / 100, 2) for value in range(50, 100, 5))


class CalibrationInputError(ValueError):
    pass


class CalibrationObservation(FrozenModel):
    capture_id: str = Field(min_length=1)
    model_tier_distribution: dict[ModelTier, float]
    reasoning_effort_distribution: dict[ReasoningEffort, float]
    human_model_tier: ModelTier
    human_reasoning_effort: ReasoningEffort


class QuestionCalibrationMetrics(FrozenModel):
    threshold: float
    accepted: int
    coverage: float
    precision: float
    dangerous_downgrades: int


type JsonCalibrationMetrics = dict[str, float | int]


class CalibrationResult(FrozenModel):
    available: bool
    labels: int
    holdout_size: int
    temperatures: dict[str, float]
    thresholds: dict[str, float]
    metrics: dict[str, JsonCalibrationMetrics]
    unavailable_reason: str | None = None
    version: str = "temperature-v1"


def fit_calibration(labels: Sequence[CalibrationObservation]) -> CalibrationResult:
    """Fit one temperature per question and evaluate on a deterministic 80/20 split."""

    if len(labels) < MIN_LABELS:
        raise CalibrationInputError(f"calibration requires at least {MIN_LABELS} labels")

    ordered = sorted(labels, key=lambda item: item.capture_id)
    training: list[CalibrationObservation] = []
    holdout: list[CalibrationObservation] = []
    stratum_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    for observation in ordered:
        stratum = (observation.human_model_tier.value, observation.human_reasoning_effort.value)
        if stratum_counts[stratum] % 5 == 4:
            holdout.append(observation)
        else:
            training.append(observation)
        stratum_counts[stratum] += 1

    training_index = 0
    represented_tiers = {observation.human_model_tier for observation in holdout}
    available_tiers = {observation.human_model_tier for observation in ordered}
    can_backfill = len(represented_tiers) >= min(2, len(available_tiers))
    while can_backfill and len(holdout) < MIN_HOLDOUT and training_index < len(training):
        holdout.append(training[training_index])
        training_index += 1
    if training_index:
        training = training[training_index:]

    if len(holdout) < MIN_HOLDOUT:
        raise CalibrationInputError(
            f"stratified holdout has {len(holdout)} examples; at least {MIN_HOLDOUT} required"
        )

    temperatures = {
        "model_tier": _fit_temperature(training, "model_tier"),
        "reasoning_effort": _fit_temperature(training, "reasoning_effort"),
    }
    metrics: dict[str, JsonCalibrationMetrics] = {}
    thresholds: dict[str, float] = {}
    for question in ("model_tier", "reasoning_effort"):
        passing = [
            evaluation
            for threshold in THRESHOLD_CANDIDATES
            if (
                evaluation := _evaluate_threshold(
                    holdout, question, temperatures[question], threshold
                )
            ).coverage
            > 0
            and evaluation.precision >= PRECISION_THRESHOLD
            and evaluation.dangerous_downgrades == 0
        ]
        if not passing:
            return CalibrationResult(
                available=False,
                labels=len(labels),
                holdout_size=len(holdout),
                temperatures=temperatures,
                thresholds={},
                metrics={},
                unavailable_reason="no_threshold_meets_precision_and_safety",
            )
        selected = min(passing, key=lambda item: (-item.coverage, item.threshold))
        metrics[question] = {
            "threshold": selected.threshold,
            "accepted": selected.accepted,
            "coverage": selected.coverage,
            "precision": selected.precision,
            "dangerous_downgrades": selected.dangerous_downgrades,
        }
        thresholds[question] = selected.threshold

    return CalibrationResult(
        available=True,
        labels=len(labels),
        holdout_size=len(holdout),
        temperatures=temperatures,
        thresholds=thresholds,
        metrics=metrics,
    )


def _fit_temperature(training: Sequence[CalibrationObservation], question: str) -> float:
    low, high = 0.05, 10.0
    for _ in range(64):
        left = low + (high - low) / 3
        right = high - (high - low) / 3
        if _negative_log_likelihood(training, question, left) < _negative_log_likelihood(
            training, question, right
        ):
            high = right
        else:
            low = left
    return round((low + high) / 2, 6)


def _negative_log_likelihood(
    observations: Sequence[CalibrationObservation], question: str, temperature: float
) -> float:
    total = 0.0
    for observation in observations:
        distribution = _distribution(observation, question)
        correct = _correct_choice(observation, question)
        scaled = {
            choice: probability ** (1 / temperature) for choice, probability in distribution.items()
        }
        denominator = sum(scaled.values())
        total -= math.log(scaled[correct] / denominator)
    return total


def _evaluate_threshold(
    observations: Sequence[CalibrationObservation],
    question: str,
    temperature: float,
    threshold: float,
) -> QuestionCalibrationMetrics:
    accepted = 0
    correct = 0
    dangerous = 0
    for observation in observations:
        calibrated = _calibrate(_distribution(observation, question), temperature)
        top_probability = max(calibrated.values())
        if top_probability < threshold:
            continue
        accepted += 1
        predicted = max(calibrated, key=lambda choice: calibrated[choice])
        human = _correct_choice(observation, question)
        if predicted == human:
            correct += 1
        if question == "model_tier" and predicted == "fast" and human == "deep":
            dangerous += 1
    return QuestionCalibrationMetrics(
        threshold=threshold,
        accepted=accepted,
        coverage=accepted / len(observations) if observations else 0.0,
        precision=correct / accepted if accepted else 0.0,
        dangerous_downgrades=dangerous,
    )


def _distribution(observation: CalibrationObservation, question: str) -> dict[str, float]:
    if question == "model_tier":
        return {
            choice.value: probability
            for choice, probability in observation.model_tier_distribution.items()
        }
    return {
        choice.value: probability
        for choice, probability in observation.reasoning_effort_distribution.items()
    }


def _correct_choice(observation: CalibrationObservation, question: str) -> str:
    if question == "model_tier":
        return observation.human_model_tier.value
    return observation.human_reasoning_effort.value


def _calibrate(distribution: dict[str, float], temperature: float) -> dict[str, float]:
    scaled = {
        choice: probability ** (1 / temperature) for choice, probability in distribution.items()
    }
    total = sum(scaled.values())
    return {choice: probability / total for choice, probability in scaled.items()}
