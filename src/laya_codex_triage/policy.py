"""Deterministic routing policy independent of the inference backend."""

from .config import Settings
from .models import Mode, ModelTier, PolicyContext, Prediction, ReasoningEffort, RouteDecision

_TIER_ORDER = tuple(ModelTier)
_EFFORT_ORDER = tuple(ReasoningEffort)


class PolicyEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def decide(self, prediction: Prediction, context: PolicyContext) -> RouteDecision:
        tier = prediction.model_tier
        effort = prediction.reasoning_effort
        applied_rule = "prediction"

        has_override = (
            context.override_model_tier is not None or context.override_reasoning_effort is not None
        )
        if has_override:
            tier = context.override_model_tier or tier
            effort = context.override_reasoning_effort or effort
            applied_rule = "user_override"
        else:
            if prediction.abstained:
                tier = self._settings.default_model_tier
                effort = self._settings.default_reasoning_effort
                applied_rule = "abstention_default"

            repo_minimum = self._settings.repo_minimums.get(context.repository)
            if repo_minimum is not None and _tier_rank(tier) < _tier_rank(repo_minimum):
                tier = repo_minimum
                applied_rule = "repository_minimum"

            if context.protected_categories & self._settings.protected_categories:
                tier = _maximum_tier(tier, ModelTier.DEEP)
                effort = _maximum_effort(effort, ReasoningEffort.HIGH)
                applied_rule = "protected_category_floor"

        tier, effort, model_slug = self._map_to_catalog(tier, effort)
        effective_mode = Mode.SHADOW if self._settings.kill_switch else self._settings.mode
        if self._settings.kill_switch:
            applied_rule = "kill_switch"

        return RouteDecision(
            configured_mode=self._settings.mode,
            effective_mode=effective_mode,
            suggested_model_tier=prediction.model_tier,
            suggested_reasoning_effort=prediction.reasoning_effort,
            policy_model_tier=tier,
            policy_reasoning_effort=effort,
            model_slug=model_slug,
            catalog_version=self._settings.model_catalog_version,
            applied_rule=applied_rule,
            abstained=prediction.abstained,
        )

    def _map_to_catalog(
        self, tier: ModelTier, effort: ReasoningEffort
    ) -> tuple[ModelTier, ReasoningEffort, str]:
        minimum_effort_rank = _effort_rank(effort)
        for candidate_tier in _TIER_ORDER[_tier_rank(tier) :]:
            entry = self._settings.model_catalog[candidate_tier]
            upward_efforts = sorted(
                (
                    supported
                    for supported in entry.supported_efforts
                    if _effort_rank(supported) >= minimum_effort_rank
                ),
                key=_effort_rank,
            )
            if upward_efforts:
                return candidate_tier, upward_efforts[0], entry.slug
        raise ValueError("model catalog cannot satisfy effort without rounding down")


def _tier_rank(tier: ModelTier) -> int:
    return _TIER_ORDER.index(tier)


def _effort_rank(effort: ReasoningEffort) -> int:
    return _EFFORT_ORDER.index(effort)


def _maximum_tier(left: ModelTier, right: ModelTier) -> ModelTier:
    return left if _tier_rank(left) >= _tier_rank(right) else right


def _maximum_effort(left: ReasoningEffort, right: ReasoningEffort) -> ReasoningEffort:
    return left if _effort_rank(left) >= _effort_rank(right) else right
