# Laya Codex Triage Operations

## Current state

The implementation is **shadow-only**. It records redacted repository-backed prompts, local predictions, labels, and aggregate reports. It does not choose the active Codex model, change reasoning effort, inject context, block a prompt, or write to the repository being classified.

Advisory and active adapters exist as typed, tested future interfaces, but both remain disabled. They require explicit user approval, promotion evidence, versioned configuration, and a separately reviewed rollout. No MCP tool can promote the operating mode.

## Setup

1. Install Python 3.12 and uv.
2. From this repository, run:

   ```bash
   uv sync --dev
   uv run pytest -v
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy src
   ```

3. Validate the plugin source:

   ```bash
   python3 /path/to/plugin-creator/scripts/validate_plugin.py .
   ```

Model weights are not installed by these commands. Dependency installation downloads code packages only.

## Explicit checkpoint preload

Normal Codex turns never download model assets. After explicitly approving the download, run:

```bash
PLUGIN_ROOT="$PWD" scripts/preload-model
```

The pinned checkpoint is:

- repository: `convaiinnovations/laya-multilingual`
- revision: `052592a15d198d9ad47da779604259b10b47b7aa`
- runtime package: `laya==0.3.4`

The backend resolves that exact revision with `local_files_only=True`. If the checkpoint is absent, startup reports a local checkpoint error; it does not fetch the missing assets.

## Hook trust and shadow enablement

The bundled hook is `hooks/hooks.json`. It registers a `UserPromptSubmit` command that runs `scripts/capture-prompt` with a one-second timeout. The wrapper suppresses stdout/stderr and always exits zero.

Codex does not treat a plugin hook as trusted merely because the plugin is installed. Review the current hook definition, then use Codex's hook trust UI to enable it. Keep the plugin disabled until bring-up verification passes. Enabling the hook enables shadow capture only; it does not enable routing.

## Review workflow

Use the bundled `laya-triage-review` skill or call the MCP tools directly:

- `triage_health`: queue, database, prediction, label, dropped-capture, and retention summary.
- `triage_recent`: recent predictions. Prompt text is omitted unless `include_prompt` is explicitly true.
- `triage_sample_for_review`: repository/tier-stratified unlabeled sample with prompt text for review.
- `triage_label`: create or correct the two human labels.
- `triage_report`: exact match, within-one effort, confusion, latency, dangerous downgrade, and routing-opportunity metrics.
- `triage_purge`: immediately apply 30-day prompt retention after exact `PURGE` confirmation.

Review 10–20 predictions per week. Judge the original task complexity; never treat the active model as ground truth.

## Calibration and mode gates

Calibration requires at least 50 labels and a stratified holdout of at least 10 examples. It fits one temperature per question and accepts only a threshold with at least 90% held-out precision and zero dangerous downgrades. If no threshold passes, calibrated high-confidence coverage is reported unavailable.

Advisory requires the shadow gates plus explicit approval. Active additionally requires at least 200 advisory decisions, 100 reviewed labels, at least 90% held-out model-tier precision, zero `deep -> fast` recommendations, passing policy/fallback/kill-switch tests, and explicit user approval. Active rollout stages are 5%, 20%, 50%, and 100%.

## Kill switch and rollback

Set `kill_switch = true` in the user-only `config.toml` under the plugin data directory to force shadow behavior immediately. Rollback to shadow occurs on any dangerous downgrade, routing error rate above 1%, or user-override rate above 10% for the active stage. Rollback changes routing only; it retains evidence.

## Purge and uninstall

1. Disable the plugin and distrust its hook.
2. Stop the MCP server.
3. Optionally export aggregate metrics from `triage_report`.
4. Run confirmed purge or explicitly delete plugin data after backup/export.
5. Uninstall the plugin. Do not modify the classified repositories.

Uninstalling source code does not automatically remove plugin data. Confirm the exact plugin-data directory before any destructive cleanup.
