from pathlib import Path

import pytest

from laya_codex_triage.adapters import (
    ActiveAdapter,
    AdvisoryAdapter,
    PromotionEvidence,
    RolloutMetrics,
    ShadowAdapter,
    should_rollback,
)
from laya_codex_triage.config import Settings
from laya_codex_triage.models import ModelTier, PolicyContext, Prediction, ReasoningEffort
from laya_codex_triage.storage import Storage


def prediction(*, abstained: bool = False) -> Prediction:
    tier_distribution = {candidate: 0.1 for candidate in ModelTier}
    effort_distribution = {candidate: 0.1 for candidate in ReasoningEffort}
    tier_distribution[ModelTier.DEEP] = 0.8
    effort_distribution[ReasoningEffort.HIGH] = 0.7
    return Prediction(
        model_tier=ModelTier.DEEP,
        reasoning_effort=ReasoningEffort.HIGH,
        model_tier_distribution=tier_distribution,
        reasoning_effort_distribution=effort_distribution,
        checkpoint="fake/checkpoint",
        checkpoint_revision="abc123",
        abstained=abstained,
        abstention_reason="low-confidence" if abstained else None,
    )


def context(**overrides: object) -> PolicyContext:
    values: dict[str, object] = {
        "repository": "repo",
        "protected_categories": frozenset(),
    }
    values.update(overrides)
    return PolicyContext(**values)  # type: ignore[arg-type]


def evidence(**overrides: object) -> PromotionEvidence:
    values: dict[str, object] = {
        "advisory_decisions": 200,
        "human_labels": 100,
        "heldout_precision": 0.91,
        "dangerous_downgrades": 0,
        "policy_tests_passed": True,
        "user_approval": True,
    }
    values.update(overrides)
    return PromotionEvidence(**values)  # type: ignore[arg-type]


def test_shadow_adapter_never_launches_and_audits_decision(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")

    result = ShadowAdapter(Settings(), storage).route(prediction(), context())

    assert result.launch is False
    assert result.decision is not None and result.decision.effective_mode.value == "shadow"
    assert storage.count_records("route_decisions") == 1


def test_advisory_adapter_records_accept_override_and_fallback(tmp_path: Path) -> None:
    storage = Storage.open(tmp_path / "triage.sqlite3", role="mcp")
    adapter = AdvisoryAdapter(Settings(), storage)

    accepted = adapter.route(prediction(), context(), user_choice="accept")
    overridden = adapter.route(
        prediction(),
        context(
            override_model_tier=ModelTier.BALANCED,
            override_reasoning_effort=ReasoningEffort.MEDIUM,
        ),
        user_choice="override",
    )
    fallback = adapter.route(prediction(abstained=True), context(), user_choice="fallback")

    assert accepted.launch is False
    assert accepted.decision is not None and accepted.decision.applied_rule == "prediction"
    assert overridden.decision is not None and overridden.decision.applied_rule == "user_override"
    assert fallback.decision is not None and fallback.decision.applied_rule == "abstention_default"
    assert storage.count_records("route_decisions") == 3


def test_active_adapter_requires_mode_evidence_and_canary() -> None:
    disabled_mode = ActiveAdapter(Settings(mode="shadow")).route(
        prediction(), context(), evidence=evidence(), canary_bucket=0, rollout_percentage=100
    )
    disabled_evidence = ActiveAdapter(Settings(mode="active")).route(
        prediction(),
        context(),
        evidence=evidence(user_approval=False),
        canary_bucket=0,
        rollout_percentage=100,
    )
    disabled_canary = ActiveAdapter(Settings(mode="active")).route(
        prediction(), context(), evidence=evidence(), canary_bucket=5, rollout_percentage=5
    )
    enabled = ActiveAdapter(Settings(mode="active")).route(
        prediction(), context(), evidence=evidence(), canary_bucket=4, rollout_percentage=5
    )

    assert disabled_mode.launch is False and disabled_mode.reason == "mode_disabled"
    assert disabled_evidence.launch is False and disabled_evidence.reason == "promotion_evidence"
    assert disabled_canary.launch is False and disabled_canary.reason == "canary_excluded"
    assert enabled.launch is True and enabled.reason == "canary_selected"


def test_kill_switch_overrides_active_mode_and_evidence() -> None:
    result = ActiveAdapter(Settings(mode="active", kill_switch=True)).route(
        prediction(), context(), evidence=evidence(), canary_bucket=0, rollout_percentage=100
    )

    assert result.launch is False
    assert result.reason == "kill_switch"


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (RolloutMetrics(dangerous_downgrades=1), True),
        (RolloutMetrics(routing_error_rate=0.011), True),
        (RolloutMetrics(user_override_rate=0.101), True),
        (RolloutMetrics(), False),
    ],
)
def test_rollback_triggers(metrics: RolloutMetrics, expected: bool) -> None:
    assert should_rollback(metrics) is expected
