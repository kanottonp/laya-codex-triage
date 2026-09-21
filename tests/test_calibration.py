import pytest

from laya_codex_triage.calibration import (
    CalibrationInputError,
    CalibrationObservation,
    fit_calibration,
)
from laya_codex_triage.models import ModelTier, ReasoningEffort


def observation(
    index: int,
    *,
    tier: ModelTier = ModelTier.BALANCED,
    effort: ReasoningEffort = ReasoningEffort.MEDIUM,
    incorrect: bool = False,
) -> CalibrationObservation:
    tier_distribution = {candidate: 0.01 for candidate in ModelTier}
    effort_distribution = {candidate: 0.01 for candidate in ReasoningEffort}
    predicted_tier = ModelTier.FAST if incorrect and tier is ModelTier.DEEP else tier
    predicted_effort = (
        ReasoningEffort.LOW if incorrect and effort is ReasoningEffort.HIGH else effort
    )
    tier_distribution[predicted_tier] = 0.97
    effort_distribution[predicted_effort] = 0.96
    return CalibrationObservation(
        capture_id=f"capture-{index:03d}",
        model_tier_distribution=tier_distribution,
        reasoning_effort_distribution=effort_distribution,
        human_model_tier=tier,
        human_reasoning_effort=effort,
    )


def balanced_labels(count: int = 50) -> list[CalibrationObservation]:
    tiers = [ModelTier.FAST, ModelTier.BALANCED, ModelTier.DEEP]
    efforts = [ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH]
    return [
        observation(index, tier=tiers[index % 3], effort=efforts[index % 3])
        for index in range(count)
    ]


def test_calibration_requires_fifty_labels() -> None:
    with pytest.raises(CalibrationInputError, match="at least 50"):
        fit_calibration(balanced_labels(49))


def test_calibration_requires_ten_holdout_examples_per_question() -> None:
    labels = [
        observation(index, tier=ModelTier.BALANCED, effort=ReasoningEffort.MEDIUM)
        for index in range(49)
    ] + [observation(49, tier=ModelTier.DEEP, effort=ReasoningEffort.HIGH)]

    with pytest.raises(CalibrationInputError, match="holdout"):
        fit_calibration(labels)


def test_calibration_fits_one_temperature_per_question_on_stratified_holdout() -> None:
    result = fit_calibration(balanced_labels())

    assert result.available is True
    assert result.holdout_size == 10
    assert set(result.temperatures) == {"model_tier", "reasoning_effort"}
    assert all(value > 0 for value in result.temperatures.values())
    assert result.metrics["model_tier"]["precision"] >= 0.90
    assert result.metrics["reasoning_effort"]["precision"] >= 0.90


def test_no_threshold_reaching_precision_and_safety_reports_unavailable() -> None:
    labels = balanced_labels(50)
    labels[4] = observation(4, tier=ModelTier.DEEP, effort=ReasoningEffort.HIGH, incorrect=True)
    labels[9] = observation(9, tier=ModelTier.DEEP, effort=ReasoningEffort.HIGH, incorrect=True)

    result = fit_calibration(labels)

    assert result.available is False
    assert result.unavailable_reason == "no_threshold_meets_precision_and_safety"
