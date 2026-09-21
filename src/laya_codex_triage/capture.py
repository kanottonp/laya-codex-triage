"""Fail-open UserPromptSubmit capture adapter."""

import hashlib
import io
import json
import os
import secrets
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Protocol, TextIO, cast

from . import DEFAULT_PLUGIN_DATA
from .git_context import resolve_git_root
from .redaction import REDACTION_VERSION, redact_prompt


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    capture_id: str
    captured_at: str
    repository_name: str
    repository_fingerprint: str
    session_hash: str
    turn_hash: str
    active_model: str
    permission_mode: str
    prompt: str
    prompt_truncated: bool
    redaction_count: int
    redaction_version: str = REDACTION_VERSION
    schema_version: str = "1"


class CaptureStorage(Protocol):
    def enqueue_capture(self, record: CaptureRecord) -> bool: ...


StorageFactory = Callable[[Path], CaptureStorage]
GitResolver = Callable[[str], Path | None]


def capture_hook(
    stdin: TextIO,
    environ: Mapping[str, str],
    *,
    storage_factory: StorageFactory | None = None,
    git_resolver: GitResolver = resolve_git_root,
) -> int:
    """Capture one hook payload; every internal failure deliberately exits successfully."""

    try:
        payload = json.load(stdin)
        if not isinstance(payload, dict):
            return 0
        required = ("cwd", "session_id", "turn_id", "model", "permission_mode", "prompt")
        if any(not isinstance(payload.get(field), str) for field in required):
            return 0

        git_root = git_resolver(payload["cwd"])
        if git_root is None:
            return 0

        plugin_data_value = environ.get("PLUGIN_DATA")
        plugin_data = Path(plugin_data_value) if plugin_data_value else DEFAULT_PLUGIN_DATA
        salt = _load_or_create_identity_salt(plugin_data)
        redacted = redact_prompt(payload["prompt"])
        record = CaptureRecord(
            capture_id=str(uuid.uuid4()),
            captured_at=datetime.now(UTC).isoformat(),
            repository_name=git_root.name,
            repository_fingerprint=_salted_hash(salt, str(git_root.resolve())),
            session_hash=_salted_hash(salt, payload["session_id"]),
            turn_hash=_salted_hash(salt, payload["turn_id"]),
            active_model=payload["model"],
            permission_mode=payload["permission_mode"],
            prompt=redacted.text,
            prompt_truncated=redacted.truncated,
            redaction_count=redacted.redaction_count,
        )
        factory = storage_factory or _open_capture_storage
        factory(plugin_data / "triage.sqlite3").enqueue_capture(record)
    except Exception:
        return 0
    return 0


def _load_or_create_identity_salt(plugin_data: Path) -> bytes:
    plugin_data.mkdir(mode=0o700, parents=True, exist_ok=True)
    plugin_data.chmod(0o700)
    salt_path = plugin_data / ".identity-salt"
    try:
        descriptor = os.open(salt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as salt_file:
            salt_file.write(secrets.token_bytes(32))
    salt_path.chmod(0o600)
    return salt_path.read_bytes()


def _salted_hash(salt: bytes, value: str) -> str:
    digest = hashlib.sha256()
    digest.update(salt)
    digest.update(b"\0")
    digest.update(value.encode("utf-8"))
    return digest.hexdigest()


def _open_capture_storage(path: Path) -> CaptureStorage:
    storage_module = import_module("laya_codex_triage.storage")
    return cast(CaptureStorage, storage_module.Storage.open(path, role="capture"))


def main() -> int:
    return capture_hook(sys.stdin, os.environ)


def capture_bytes(payload: bytes, environ: Mapping[str, str]) -> int:
    """Small adapter for embedders that already hold the hook payload as bytes."""

    return capture_hook(io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8"), environ)
