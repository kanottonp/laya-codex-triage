import io
import json
import stat
from pathlib import Path

import pytest

from laya_codex_triage.capture import CaptureRecord, capture_hook


class RecordingStorage:
    def __init__(self) -> None:
        self.records: list[CaptureRecord] = []

    def enqueue_capture(self, record: CaptureRecord) -> bool:
        self.records.append(record)
        return True


def hook_payload(**overrides: str) -> io.StringIO:
    payload = {
        "cwd": "/work/example-repo",
        "session_id": "session-secret",
        "turn_id": "turn-secret",
        "model": "gpt-5.6-terra",
        "permission_mode": "default",
        "prompt": "Deploy with API_KEY=top-secret",
    }
    payload.update(overrides)
    return io.StringIO(json.dumps(payload))


def test_valid_repository_capture_is_redacted_and_hashed(tmp_path: Path) -> None:
    storage = RecordingStorage()

    exit_code = capture_hook(
        hook_payload(),
        {"PLUGIN_DATA": str(tmp_path / "plugin-data")},
        storage_factory=lambda _path: storage,
        git_resolver=lambda _cwd: Path("/work/example-repo"),
    )

    assert exit_code == 0
    assert len(storage.records) == 1
    record = storage.records[0]
    assert record.repository_name == "example-repo"
    assert record.repository_fingerprint != "/work/example-repo"
    assert record.session_hash != "session-secret"
    assert record.turn_hash != "turn-secret"
    assert "top-secret" not in record.prompt
    assert "[REDACTED_CREDENTIAL]" in record.prompt


def test_projectless_prompt_is_skipped(tmp_path: Path) -> None:
    storage = RecordingStorage()

    result = capture_hook(
        hook_payload(),
        {"PLUGIN_DATA": str(tmp_path)},
        storage_factory=lambda _path: storage,
        git_resolver=lambda _cwd: None,
    )

    assert result == 0
    assert storage.records == []


def test_malformed_json_and_missing_fields_fail_open(tmp_path: Path) -> None:
    storage = RecordingStorage()
    environment = {"PLUGIN_DATA": str(tmp_path)}

    assert capture_hook(io.StringIO("{"), environment, storage_factory=lambda _: storage) == 0
    assert (
        capture_hook(
            io.StringIO(json.dumps({"cwd": "/tmp", "prompt": "hi"})),
            environment,
            storage_factory=lambda _: storage,
        )
        == 0
    )
    assert storage.records == []


def test_unavailable_git_fails_open(tmp_path: Path) -> None:
    def unavailable(_cwd: str) -> Path | None:
        raise FileNotFoundError("git")

    assert (
        capture_hook(
            hook_payload(),
            {"PLUGIN_DATA": str(tmp_path)},
            storage_factory=lambda _: RecordingStorage(),
            git_resolver=unavailable,
        )
        == 0
    )


def test_storage_exception_fails_open_without_stdout(tmp_path: Path, capsys: object) -> None:
    class FailingStorage:
        def enqueue_capture(self, _record: CaptureRecord) -> bool:
            raise OSError("database busy")

    result = capture_hook(
        hook_payload(),
        {"PLUGIN_DATA": str(tmp_path)},
        storage_factory=lambda _: FailingStorage(),
        git_resolver=lambda _: Path("/work/example-repo"),
    )

    assert result == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.out == ""


def test_plugin_data_and_identity_salt_are_user_only(tmp_path: Path) -> None:
    plugin_data = tmp_path / "nested" / "plugin-data"

    assert (
        capture_hook(
            hook_payload(),
            {"PLUGIN_DATA": str(plugin_data)},
            storage_factory=lambda _: RecordingStorage(),
            git_resolver=lambda _: Path("/work/example-repo"),
        )
        == 0
    )

    assert stat.S_IMODE(plugin_data.stat().st_mode) == 0o700
    assert stat.S_IMODE((plugin_data / ".identity-salt").stat().st_mode) == 0o600


def test_missing_plugin_data_uses_default_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_default = tmp_path / "default-plugin-data"
    monkeypatch.setattr("laya_codex_triage.capture.DEFAULT_PLUGIN_DATA", fake_default)
    storage = RecordingStorage()

    assert (
        capture_hook(
            hook_payload(),
            {},
            storage_factory=lambda _: storage,
            git_resolver=lambda _: Path("/work/example-repo"),
        )
        == 0
    )

    assert len(storage.records) == 1
    assert (fake_default / ".identity-salt").exists()
