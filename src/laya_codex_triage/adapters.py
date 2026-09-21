"""Operating-mode adapters for shadow review and future pre-turn routing."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from .config import Settings
from .models import FrozenModel, PolicyContext, Prediction, RouteDecision
from .policy import PolicyEngine
from .storage import Storage

UserChoice = Literal["accept", "override", "fallback"]


class PromotionEvidence(FrozenModel):
    advisory_decisions: int = Field(ge=0)
    human_labels: int = Field(ge=0)
    heldout_precision: float = Field(ge=0, le=1)
    dangerous_downgrades: int = Field(ge=0)
    policy_tests_passed: bool
    user_approval: bool


class RolloutMetrics(FrozenModel):
    dangerous_downgrades: int = 0
    routing_error_rate: float = 0.0
    user_override_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class RoutingResult:
    launch: bool
    decision: RouteDecision | None
    reason: str
    user_choice: UserChoice | None = None


class ShadowAdapter:
    def __init__(self, settings: Settings, storage: Storage | None = None) -> None:
        self._engine = PolicyEngine(settings)
        self._storage = storage

    def route(self, prediction: Prediction, context: PolicyContext) -> RoutingResult:
        decision = self._engine.decide(prediction, context)
        self._audit(decision, context)
        return RoutingResult(launch=False, decision=decision, reason="shadow_mode")

    def _audit(self, decision: RouteDecision, context: PolicyContext) -> None:
        if self._storage is not None:
            self._storage.save_route_decision(
                decision,
                user_override=_override_payload(context),
                launch_outcome="shadow_recorded",
            )


class AdvisoryAdapter:
    def __init__(self, settings: Settings, storage: Storage | None = None) -> None:
        self._engine = PolicyEngine(settings)
        self._storage = storage

    def route(
        self,
        prediction: Prediction,
        context: PolicyContext,
        *,
        user_choice: UserChoice,
    ) -> RoutingResult:
        effective_prediction = (
            prediction.model_copy(update={"abstained": True, "abstention_reason": "user_fallback"})
            if user_choice == "fallback"
            else prediction
        )
        decision = self._engine.decide(effective_prediction, context)
        if self._storage is not None:
            self._storage.save_route_decision(
                decision,
                user_override=_override_payload(context) or {"choice": user_choice},
                launch_outcome=f"advisory_{user_choice}",
            )
        return RoutingResult(
            launch=False,
            decision=decision,
            reason=f"advisory_{user_choice}",
            user_choice=user_choice,
        )


class ActiveAdapter:
    def __init__(self, settings: Settings, storage: Storage | None = None) -> None:
        self._settings = settings
        self._engine = PolicyEngine(settings)
        self._storage = storage

    def route(
        self,
        prediction: Prediction,
        context: PolicyContext,
        *,
        evidence: PromotionEvidence,
        canary_bucket: int,
        rollout_percentage: int,
    ) -> RoutingResult:
        if self._settings.kill_switch:
            return self._disabled(prediction, context, "kill_switch")
        if self._settings.mode.value != "active":
            return self._disabled(prediction, context, "mode_disabled")
        if not _promotion_passed(evidence):
            return self._disabled(prediction, context, "promotion_evidence")
        if canary_bucket >= rollout_percentage:
            return self._disabled(prediction, context, "canary_excluded")

        decision = self._engine.decide(prediction, context)
        if self._storage is not None:
            self._storage.save_route_decision(
                decision,
                user_override=_override_payload(context),
                launch_outcome="canary_eligible",
            )
        return RoutingResult(
            launch=True,
            decision=decision,
            reason="canary_selected",
        )

    def _disabled(
        self, prediction: Prediction, context: PolicyContext, reason: str
    ) -> RoutingResult:
        decision = self._engine.decide(prediction, context)
        if self._storage is not None:
            self._storage.save_route_decision(
                decision,
                user_override=_override_payload(context),
                launch_outcome=reason,
            )
        return RoutingResult(launch=False, decision=decision, reason=reason)


def build_turn_start(route: RouteDecision, prompt: str, cwd: str) -> dict[str, object]:
    """Build the pre-start request template; callers add request/thread identity."""

    return {
        "method": "turn/start",
        "params": {
            "input": [{"type": "text", "text": prompt}],
            "cwd": cwd,
            "model": route.model_slug,
            "effort": route.policy_reasoning_effort.value,
        },
    }


def should_rollback(metrics: RolloutMetrics) -> bool:
    return (
        metrics.dangerous_downgrades > 0
        or metrics.routing_error_rate > 0.01
        or metrics.user_override_rate > 0.10
    )


def _promotion_passed(evidence: PromotionEvidence) -> bool:
    return (
        evidence.advisory_decisions >= 200
        and evidence.human_labels >= 100
        and evidence.heldout_precision >= 0.90
        and evidence.dangerous_downgrades == 0
        and evidence.policy_tests_passed
        and evidence.user_approval
    )


def _override_payload(context: PolicyContext) -> dict[str, str] | None:
    if context.override_model_tier is None and context.override_reasoning_effort is None:
        return None
    payload: dict[str, str] = {}
    if context.override_model_tier is not None:
        payload["model_tier"] = context.override_model_tier.value
    if context.override_reasoning_effort is not None:
        payload["reasoning_effort"] = context.override_reasoning_effort.value
    return payload
