from laya_codex_triage.adapters import build_turn_start
from laya_codex_triage.app_server import AppServerClient, LaunchResult
from laya_codex_triage.config import Settings
from laya_codex_triage.models import ModelTier, PolicyContext, Prediction, ReasoningEffort
from laya_codex_triage.policy import PolicyEngine


def decision() -> object:
    distribution = {candidate: 0.1 for candidate in ModelTier}
    effort_distribution = {candidate: 0.1 for candidate in ReasoningEffort}
    distribution[ModelTier.DEEP] = 0.8
    effort_distribution[ReasoningEffort.HIGH] = 0.7
    prediction = Prediction(
        model_tier=ModelTier.DEEP,
        reasoning_effort=ReasoningEffort.HIGH,
        model_tier_distribution=distribution,
        reasoning_effort_distribution=effort_distribution,
        checkpoint="fake/checkpoint",
        checkpoint_revision="abc123",
    )
    return PolicyEngine(Settings()).decide(prediction, PolicyContext(repository="repo"))


class RecordingClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.payloads: list[dict[str, object]] = []
        self.error = error

    def turn_start(self, payload: dict[str, object], timeout: float) -> object:
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return {"turn": {"id": "turn-1", "status": "inProgress"}}


def test_build_turn_start_contains_only_prestart_fields() -> None:
    payload = build_turn_start(decision(), "ทำต่อ", "/work/repo")  # type: ignore[arg-type]

    assert payload == {
        "method": "turn/start",
        "params": {
            "input": [{"type": "text", "text": "ทำต่อ"}],
            "cwd": "/work/repo",
            "model": "gpt-6-astra",
            "effort": "high",
        },
    }
    assert "threadId" not in payload["params"]


def test_app_server_client_uses_exact_payload_and_records_launch() -> None:
    client = RecordingClient()

    result = AppServerClient(client).start_turn(decision(), "ทำต่อ", "/work/repo")  # type: ignore[arg-type]

    assert isinstance(result, LaunchResult)
    assert result.launched is True
    assert result.turn_id == "turn-1"
    assert client.payloads[0]["method"] == "turn/start"


def test_timeout_falls_back_without_in_progress_mutation() -> None:
    client = RecordingClient(TimeoutError("app server timeout"))

    result = AppServerClient(client).start_turn(decision(), "ทำต่อ", "/work/repo")  # type: ignore[arg-type]

    assert result.launched is False
    assert result.turn_id is None
    assert result.error_code == "timeout"
    assert len(client.payloads) == 1
    assert client.payloads[0]["method"] == "turn/start"
