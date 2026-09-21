"""Bounded App Server client helper for pre-turn launch only."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .adapters import build_turn_start
from .models import RouteDecision

DEFAULT_TURN_START_TIMEOUT_SECONDS = 0.25


class TurnStartClient(Protocol):
    def turn_start(self, payload: dict[str, object], timeout: float) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class LaunchResult:
    launched: bool
    turn_id: str | None
    payload: dict[str, object] | None
    error_code: str | None = None


class AppServerClient:
    def __init__(self, client: TurnStartClient) -> None:
        self._client = client

    def start_turn(
        self,
        route: RouteDecision,
        prompt: str,
        cwd: str,
        *,
        timeout: float = DEFAULT_TURN_START_TIMEOUT_SECONDS,
    ) -> LaunchResult:
        payload = build_turn_start(route, prompt, cwd)
        try:
            response = self._client.turn_start(payload, timeout)
        except TimeoutError:
            return LaunchResult(
                launched=False,
                turn_id=None,
                payload=payload,
                error_code="timeout",
            )
        except Exception:
            return LaunchResult(
                launched=False,
                turn_id=None,
                payload=payload,
                error_code="app_server_error",
            )

        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, Mapping) else None
        if not isinstance(turn_id, str):
            return LaunchResult(
                launched=False,
                turn_id=None,
                payload=payload,
                error_code="invalid_response",
            )
        return LaunchResult(launched=True, turn_id=turn_id, payload=payload)
