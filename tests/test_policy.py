from laya_codex_triage.config import Settings
from laya_codex_triage.models import (
    ModelTier,
    PolicyContext,
    Prediction,
    ReasoningEffort,
)
from laya_codex_triage.policy import PolicyEngine


def prediction(
    tier: ModelTier = ModelTier.FAST,
    effort: ReasoningEffort = ReasoningEffort.LOW,
    *,
    abstained: bool = False,
) -> Prediction:
    tier_distribution = {candidate: 0.05 for candidate in ModelTier}
    tier_distribution[tier] = 0.90
    effort_distribution = {candidate: 0.02 for candidate in ReasoningEffort}
    effort_distribution[effort] = 0.94
    return Prediction(
        model_tier=tier,
        reasoning_effort=effort,
        model_tier_distribution=tier_distribution,
        reasoning_effort_distribution=effort_distribution,
        checkpoint="fake/checkpoint",
        checkpoint_revision="abc123",
        abstained=abstained,
        abstention_reason="low-confidence" if abstained else None,
    )


def test_explicit_override_takes_precedence_over_safety_floor() -> None:
    engine = PolicyEngine(Settings())

    decision = engine.decide(
        prediction(ModelTier.DEEP, ReasoningEffort.XHIGH),
        PolicyContext(
            repository="payments",
            protected_categories=frozenset({"money"}),
            override_model_tier=ModelTier.BALANCED,
            override_reasoning_effort=ReasoningEffort.MEDIUM,
        ),
    )

    assert decision.policy_model_tier is ModelTier.BALANCED
    assert decision.policy_reasoning_effort is ReasoningEffort.MEDIUM
    assert decision.applied_rule == "user_override"


def test_repository_minimum_raises_model_tier() -> None:
    settings = Settings(repo_minimums={"production": ModelTier.DEEP})

    decision = PolicyEngine(settings).decide(
        prediction(ModelTier.FAST), PolicyContext(repository="production")
    )

    assert decision.policy_model_tier is ModelTier.DEEP
    assert decision.applied_rule == "repository_minimum"


def test_protected_category_enforces_deep_high_floor() -> None:
    decision = PolicyEngine(Settings()).decide(
        prediction(ModelTier.FAST, ReasoningEffort.LOW),
        PolicyContext(repository="repo", protected_categories=frozenset({"secrets"})),
    )

    assert decision.policy_model_tier is ModelTier.DEEP
    assert decision.policy_reasoning_effort is ReasoningEffort.HIGH
    assert decision.applied_rule == "protected_category_floor"


def test_abstention_uses_configured_default() -> None:
    settings = Settings(
        default_model_tier=ModelTier.BALANCED,
        default_reasoning_effort=ReasoningEffort.HIGH,
    )

    decision = PolicyEngine(settings).decide(
        prediction(abstained=True), PolicyContext(repository="repo")
    )

    assert decision.policy_model_tier is ModelTier.BALANCED
    assert decision.policy_reasoning_effort is ReasoningEffort.HIGH
    assert decision.applied_rule == "abstention_default"


def test_unsupported_effort_rounds_up_and_never_down() -> None:
    decision = PolicyEngine(Settings()).decide(
        prediction(ModelTier.FAST, ReasoningEffort.HIGH), PolicyContext(repository="repo")
    )

    assert decision.policy_model_tier is ModelTier.BALANCED
    assert decision.policy_reasoning_effort is ReasoningEffort.HIGH
    assert decision.model_slug == "gpt-5.6-terra"


def test_deep_prediction_never_maps_to_fast_model() -> None:
    decision = PolicyEngine(Settings()).decide(
        prediction(ModelTier.DEEP, ReasoningEffort.LOW), PolicyContext(repository="repo")
    )

    assert decision.policy_model_tier is ModelTier.DEEP
    assert decision.model_slug == "gpt-6-astra"


def test_kill_switch_forces_shadow_without_changing_audit_recommendation() -> None:
    settings = Settings(mode="active", kill_switch=True)

    decision = PolicyEngine(settings).decide(
        prediction(ModelTier.DEEP, ReasoningEffort.XHIGH), PolicyContext(repository="repo")
    )

    assert decision.effective_mode.value == "shadow"
    assert decision.policy_model_tier is ModelTier.DEEP
    assert decision.applied_rule == "kill_switch"
