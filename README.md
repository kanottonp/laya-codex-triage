# Laya Codex Triage

Laya Codex Triage is a personal Codex plugin for evaluating whether local Laya inference can
recommend a model tier and reasoning effort for repository-backed prompts.

The initial release is **shadow-only**. It records local recommendations for later review but does
not change the active model, alter reasoning effort, block prompts, steer the agent, or write to the
repository being classified. Advisory and active routing remain disabled until separately approved
evidence gates pass.

## Privacy and runtime boundaries

- Only prompts whose working directory resolves to a Git repository are eligible.
- Secrets are redacted before inference or persistence, then prompts are truncated to 4,000 Unicode
  characters.
- Prompt data, predictions, labels, and reports stay local.
- Model assets are downloaded only by an explicit preload command, never during a Codex turn.
- Hook failures are fail-open, emit no model-visible output, and must not interrupt Codex work.

## Development

The project targets Python 3.12 and uses `uv`.

The runtime package is pinned to `laya==0.3.4`. Explicit preload uses
`convaiinnovations/laya-multilingual` at revision
`052592a15d198d9ad47da779604259b10b47b7aa`; normal installation and Codex turns must not resolve
or download a model checkpoint.

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

The MCP server, hook, preload command, review skill, and operational documentation are implemented
in this repository. The initial release is shadow-only: advisory and active routing remain disabled
pending separately approved evidence gates.

See [operations](docs/operations.md) for setup, explicit checkpoint preload, hook trust, review,
calibration, kill switch, rollback, purge, and uninstall. See [privacy](docs/privacy.md) for the
local-processing, redaction, retention, review-access, and model-acquisition boundaries.
