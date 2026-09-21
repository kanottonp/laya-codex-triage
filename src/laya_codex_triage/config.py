"""Typed TOML configuration for the triage plugin."""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from .models import FrozenModel, Mode, ModelCatalogEntry, ModelTier, ReasoningEffort


def _default_catalog() -> dict[ModelTier, ModelCatalogEntry]:
    return {
        ModelTier.FAST: ModelCatalogEntry(
            slug="gpt-5.6-luna",
            supported_efforts=(ReasoningEffort.LOW, ReasoningEffort.MEDIUM),
        ),
        ModelTier.BALANCED: ModelCatalogEntry(
            slug="gpt-5.6-terra",
            supported_efforts=(ReasoningEffort.MEDIUM, ReasoningEffort.HIGH),
        ),
        ModelTier.DEEP: ModelCatalogEntry(
            slug="gpt-6-astra",
            supported_efforts=(ReasoningEffort.HIGH, ReasoningEffort.XHIGH),
        ),
    }


class Settings(FrozenModel):
    mode: Mode = Mode.SHADOW
    default_model_tier: ModelTier = ModelTier.BALANCED
    default_reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM
    kill_switch: bool = False
    model_catalog_version: str = "2026-09-21"
    model_catalog: dict[ModelTier, ModelCatalogEntry] = Field(default_factory=_default_catalog)
    repo_minimums: dict[str, ModelTier] = Field(default_factory=dict)
    protected_categories: frozenset[str] = frozenset(
        {
            "authentication",
            "authorization",
            "secrets",
            "money",
            "tenant_isolation",
            "data_integrity",
            "migration",
            "destructive",
            "production",
            "security",
        }
    )
    abstention_min_probability: float = Field(default=0.80, ge=0, le=1)
    abstention_min_margin: float = Field(default=0.20, ge=0, le=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> "Settings":
        if set(self.model_catalog) != set(ModelTier):
            raise ValueError("model_catalog must define fast, balanced, and deep")
        deep_efforts = self.model_catalog[ModelTier.DEEP].supported_efforts
        if ReasoningEffort.XHIGH not in deep_efforts:
            raise ValueError("model_catalog deep tier must support xhigh")
        return self


def load_settings(plugin_data: Path) -> Settings:
    """Load user-only plugin configuration, returning safe defaults when absent."""

    config_path = plugin_data / "config.toml"
    if not config_path.exists():
        return Settings()
    with config_path.open("rb") as config_file:
        data: dict[str, Any] = tomllib.load(config_file)
    return Settings.model_validate(data)
