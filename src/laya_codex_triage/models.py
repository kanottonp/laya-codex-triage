"""Typed contracts shared by capture, inference, policy, and routing."""

from collections.abc import Mapping, Set
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChoiceT = TypeVar("ChoiceT", bound=StrEnum)


class FrozenModel(BaseModel):
    """Base model whose public fields cannot be reassigned."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Mode(StrEnum):
    SHADOW = "shadow"
    ADVISORY = "advisory"
    ACTIVE = "active"


class ModelTier(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    DEEP = "deep"


class ReasoningEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ModelCatalogEntry(FrozenModel):
    slug: str = Field(min_length=1)
    supported_efforts: tuple[ReasoningEffort, ...]

    @model_validator(mode="after")
    def validate_supported_efforts(self) -> "ModelCatalogEntry":
        if not self.supported_efforts:
            raise ValueError("supported_efforts must not be empty")
        if len(set(self.supported_efforts)) != len(self.supported_efforts):
            raise ValueError("supported_efforts must not contain duplicates")
        return self


class Prediction(FrozenModel):
    model_tier: ModelTier
    reasoning_effort: ReasoningEffort
    model_tier_distribution: dict[ModelTier, float]
    reasoning_effort_distribution: dict[ReasoningEffort, float]
    checkpoint: str = Field(min_length=1)
    checkpoint_revision: str = Field(min_length=1)
    decision_schema_version: str = "1"
    calibration_version: str | None = None
    model_tier_confidence: float | None = Field(default=None, ge=0, le=1)
    reasoning_effort_confidence: float | None = Field(default=None, ge=0, le=1)
    latency_ms: float | None = Field(default=None, ge=0)
    abstained: bool = False
    abstention_reason: str | None = None

    @model_validator(mode="after")
    def validate_distributions(self) -> "Prediction":
        self._validate_distribution(
            self.model_tier_distribution, set(ModelTier), "model_tier_distribution"
        )
        self._validate_distribution(
            self.reasoning_effort_distribution,
            set(ReasoningEffort),
            "reasoning_effort_distribution",
        )
        if self.abstained and not self.abstention_reason:
            raise ValueError("abstained predictions require an abstention_reason")
        return self

    @staticmethod
    def _validate_distribution(
        distribution: Mapping[ChoiceT, float], expected: Set[ChoiceT], field_name: str
    ) -> None:
        if set(distribution) != expected:
            raise ValueError(f"{field_name} must contain every choice")
        if any(value < 0 or value > 1 for value in distribution.values()):
            raise ValueError(f"{field_name} probabilities must be between 0 and 1")
        if abs(sum(distribution.values()) - 1.0) > 1e-6:
            raise ValueError(f"{field_name} probabilities must sum to 1")


class PolicyContext(FrozenModel):
    repository: str = Field(min_length=1)
    protected_categories: frozenset[str] = frozenset()
    override_model_tier: ModelTier | None = None
    override_reasoning_effort: ReasoningEffort | None = None


class RouteDecision(FrozenModel):
    configured_mode: Mode
    effective_mode: Mode
    suggested_model_tier: ModelTier
    suggested_reasoning_effort: ReasoningEffort
    policy_model_tier: ModelTier
    policy_reasoning_effort: ReasoningEffort
    model_slug: str
    catalog_version: str
    applied_rule: str
    abstained: bool
