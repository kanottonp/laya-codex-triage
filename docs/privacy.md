# Laya Codex Triage Privacy

## Local processing

Prompt redaction, inference, storage, human review, calibration, and reporting run on the user's machine. The pilot has no external telemetry or upload path.

## Collected fields

For repository-backed prompts only:

- redacted, NFC-normalized prompt text, limited to 4,000 Unicode characters;
- capture and prediction timestamps;
- salted hashes of session and turn IDs;
- repository basename and salted fingerprint of the canonical root path;
- active Codex model slug and permission mode;
- complete model-tier and reasoning-effort probability distributions;
- inference/queue latency and bounded error codes;
- optional human labels and short review note.

The plugin does not read repository files, remote URLs, environment variables, or full transcripts for classification.

## Redaction and retention

Redaction happens before queue insertion and inference. It replaces bearer/basic values, common API-key formats, private-key blocks, credential-like assignments, URL userinfo, and configured custom patterns with type markers.

The plugin data directory and database are user-only. Redacted prompt text is removed after 30 days or immediately after confirmed purge. Predictions, labels, calibrations, and aggregate metrics may remain until explicitly purged so review evidence is not silently destroyed.

## Review access

`triage_recent` omits prompt text by default. Only `triage_sample_for_review` and an explicit `include_prompt: true` review request expose the stored redacted text. Reports contain aggregates, not prompts.

## Model acquisition

The official Laya checkpoint is downloaded only by the explicit preload command after user approval. Installation, tests, hooks, and normal turns must not download weights. Runtime inference uses the exact pinned revision from local storage only.

## Removal

Disable the plugin and hook, stop the MCP server, export desired aggregates, then purge or delete the exact plugin-data directory after confirmation. Removing the plugin does not rewrite classified repositories.
