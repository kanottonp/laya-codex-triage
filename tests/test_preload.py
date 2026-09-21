from laya_codex_triage.preload import preload_model


def test_explicit_preload_is_the_only_remote_snapshot_path() -> None:
    calls: list[dict[str, object]] = []

    result = preload_model(
        "checkpoint",
        "revision",
        snapshot_resolver=lambda checkpoint, **kwargs: (
            calls.append({"checkpoint": checkpoint, **kwargs}) or "/models/laya"
        ),
    )

    assert calls == [
        {"checkpoint": "checkpoint", "revision": "revision", "local_files_only": False}
    ]
    assert result.local_path == "/models/laya"
