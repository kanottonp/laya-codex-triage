"""Explicit checkpoint acquisition command; never imported by the hook."""

from argparse import ArgumentParser
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

from . import DEFAULT_CHECKPOINT, DEFAULT_CHECKPOINT_REVISION


class _SnapshotResolver(Protocol):
    def __call__(self, checkpoint: str, *, revision: str, local_files_only: bool) -> str: ...


@dataclass(frozen=True, slots=True)
class PreloadResult:
    checkpoint: str
    revision: str
    local_path: str


def preload_model(
    checkpoint: str,
    revision: str,
    *,
    snapshot_resolver: _SnapshotResolver | None = None,
) -> PreloadResult:
    resolver = snapshot_resolver or _snapshot_download
    local_path = resolver(checkpoint, revision=revision, local_files_only=False)
    return PreloadResult(checkpoint=checkpoint, revision=revision, local_path=local_path)


def _snapshot_download(checkpoint: str, *, revision: str, local_files_only: bool) -> str:
    huggingface_hub = import_module("huggingface_hub")
    return cast(
        str,
        huggingface_hub.snapshot_download(
            checkpoint,
            revision=revision,
            local_files_only=local_files_only,
        ),
    )


def main() -> int:
    parser = ArgumentParser(description="Explicitly preload the pinned Laya checkpoint")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--revision", default=DEFAULT_CHECKPOINT_REVISION)
    arguments = parser.parse_args()
    result = preload_model(arguments.checkpoint, arguments.revision)
    print(f"Preloaded {result.checkpoint}@{result.revision}")
    return 0
