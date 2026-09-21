from laya_codex_triage.models import ModelTier, ReasoningEffort
from laya_codex_triage.reporting import ReportObservation, build_report


def observation(
    index: int,
    *,
    predicted: ModelTier,
    human: ModelTier,
    latency_ms: float,
) -> ReportObservation:
    return ReportObservation(
        capture_id=f"capture-{index}",
        predicted_model_tier=predicted,
        predicted_reasoning_effort=ReasoningEffort.MEDIUM,
        human_model_tier=human,
        human_reasoning_effort=ReasoningEffort.HIGH,
        model_tier_top_probability=0.95,
        reasoning_effort_top_probability=0.95,
        inference_latency_ms=latency_ms,
        queue_delay_ms=10.0,
        error_code=None,
    )


def test_report_contains_confusion_dangerous_downgrade_and_percentiles() -> None:
    observations = [
        observation(1, predicted=ModelTier.BALANCED, human=ModelTier.BALANCED, latency_ms=100),
        observation(2, predicted=ModelTier.DEEP, human=ModelTier.BALANCED, latency_ms=200),
        observation(3, predicted=ModelTier.FAST, human=ModelTier.DEEP, latency_ms=300),
        observation(4, predicted=ModelTier.BALANCED, human=ModelTier.DEEP, latency_ms=400),
    ]

    report = build_report(observations)

    assert report.sample_size == 4
    assert report.confusion_matrices.model_tier.fast.deep == 1
    assert report.confusion_matrices.model_tier.balanced.deep == 1
    assert report.dangerous_downgrades == 1
    assert report.latency.p50_ms == 250.0
    assert report.latency.p95_ms == 385.0
    assert report.routing_opportunity == 0.5
