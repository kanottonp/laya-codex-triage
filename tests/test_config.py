from pathlib import Path

import pytest
from pydantic import ValidationError

from laya_codex_triage.config import load_settings
from laya_codex_triage.models import Mode, ModelTier, ReasoningEffort


def test_missing_config_uses_shadow_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path)

    assert settings.mode is Mode.SHADOW
    assert settings.default_model_tier is ModelTier.BALANCED
    assert settings.default_reasoning_effort is ReasoningEffort.MEDIUM
    assert settings.kill_switch is False


def test_invalid_mode_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('mode = "automatic"\n', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_settings(tmp_path)


def test_catalog_requires_every_tier(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        """
[model_catalog.fast]
slug = "fast-model"
supported_efforts = ["low"]

[model_catalog.balanced]
slug = "balanced-model"
supported_efforts = ["medium", "high"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="model_catalog"):
        load_settings(tmp_path)


def test_toml_values_override_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        """
mode = "advisory"
default_model_tier = "deep"
default_reasoning_effort = "high"
kill_switch = true

[repo_minimums]
payments = "deep"
""",
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings.mode is Mode.ADVISORY
    assert settings.default_model_tier is ModelTier.DEEP
    assert settings.default_reasoning_effort is ReasoningEffort.HIGH
    assert settings.kill_switch is True
    assert settings.repo_minimums["payments"] is ModelTier.DEEP


def test_settings_are_immutable(tmp_path: Path) -> None:
    settings = load_settings(tmp_path)

    with pytest.raises(ValidationError):
        settings.mode = Mode.ACTIVE
