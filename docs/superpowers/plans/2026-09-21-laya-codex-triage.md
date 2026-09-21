# Laya Codex Triage Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal Codex plugin that classifies repository-backed prompts with local Laya inference in shadow mode while preserving advisory and active pre-turn routing interfaces for later rollout.

**Architecture:** A fail-open `UserPromptSubmit` hook redacts and queues prompts in SQLite. A persistent Python MCP server consumes the queue, runs a version-pinned Laya backend, exposes review/report tools, and keeps policy/routing adapters disabled until later mode promotion. The initial release enables only shadow mode.

**Tech Stack:** Python 3.12, `uv`, SQLite/WAL, Pydantic 2, MCP Python SDK, official `laya` package/checkpoint, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-21-laya-codex-triage-design.md`

## Global Constraints

- Initial mode is `shadow`; MCP tools cannot enable advisory or active mode.
- Process only prompts whose `cwd` resolves to a Git repository.
- Redact before inference/storage and truncate to 4,000 Unicode characters.
- Redacted prompt retention is 30 days.
- Hook failures exit `0`, emit no stdout, and never block Codex.
- Capture p95 target is 50 ms; warm inference p95 target is 250 ms.
- Start with the official multilingual Laya checkpoint pinned to an exact revision.
- Model download occurs only through explicit preload, never during a Codex turn.
- Future active routing selects model/effort before App Server/SDK `turn/start`.

## Review Focus

- Bearer tokens, private keys, credential assignments, URL credentials, and mixed Unicode are redacted before queue insertion.
- Missing Git, non-repository directories, busy/read-only SQLite, and malformed hook input fail open without stdout.
- Concurrent hook writes and worker reads do not duplicate accepted captures under WAL.
- Unsupported effort rounds upward; protected tasks never route below `deep/high`.
- Missing/invalid Laya output and routing timeouts preserve the configured default and bounded retry policy.

---

### Task 1: Repository and plugin scaffold

**Files:** Create `pyproject.toml`, `.python-version`, `.gitignore`, `.codex-plugin/plugin.json`, `README.md`, `src/laya_codex_triage/__init__.py`, `tests/test_manifest.py`, and the approved spec under `docs/superpowers/specs/`.

**Interfaces:** Package `laya_codex_triage`; console scripts `laya-codex-capture`, `laya-codex-mcp`, `laya-codex-preload`, and `laya-codex-purge`.

- [ ] Write `tests/test_manifest.py` to parse the manifest and assert its name plus MCP, hook, and skill paths.
- [ ] Run `uv run pytest tests/test_manifest.py -v`; expect missing-file failure.
- [ ] Add Python 3.12 metadata, dependencies, entry points, manifest, README boundaries, and approved spec.
- [ ] Run `uv sync --dev` and the focused test; expect pass.
- [ ] Commit `chore: scaffold laya codex triage plugin`.

### Task 2: Typed configuration and policy engine

**Files:** Create `src/laya_codex_triage/models.py`, `config.py`, `policy.py`, `tests/test_config.py`, and `tests/test_policy.py`.

**Interfaces:** `Mode`, `ModelTier`, `ReasoningEffort`, `Prediction`, `PolicyContext`, `RouteDecision`; `load_settings(plugin_data: Path) -> Settings`; `PolicyEngine.decide(prediction, context) -> RouteDecision`.

- [ ] Write failing tests for shadow default, invalid mode, catalog validation, override precedence, repo minimum, protected-category floor, abstention fallback, upward effort rounding, and prevention of `deep -> fast`.
- [ ] Run focused tests; expect import failures.
- [ ] Implement immutable Pydantic models, TOML config, deterministic policy ordering, safety floors, model catalog, and kill switch.
- [ ] Run focused tests; expect pass.
- [ ] Commit `feat: add triage policy and configuration`.

### Task 3: Redaction and fail-open prompt capture

**Files:** Create `redaction.py`, `git_context.py`, `capture.py`, `scripts/capture-prompt`, `tests/test_redaction.py`, and `tests/test_capture.py`.

**Interfaces:** `redact_prompt(text, patterns=()) -> RedactedPrompt`; `resolve_git_root(cwd) -> Path | None`; `capture_hook(stdin, environ) -> int` always returns `0` for internal failures. Storage is injected through a protocol.

- [ ] Test bearer/basic auth, API-key prefixes, PEM keys, credential assignments, URL userinfo, custom patterns, Thai/English Unicode, and redaction-before-truncation.
- [ ] Test valid repo, projectless skip, malformed JSON, missing fields, unavailable Git, storage exception, no stdout, and plugin-data permissions.
- [ ] Run focused tests; expect failures.
- [ ] Implement pure redaction, timeout-bounded Git lookup, fail-open adapter, and wrapper.
- [ ] Run focused tests; expect pass; commit `feat: capture and redact repository prompts`.

### Task 4: SQLite queue, retention, and concurrency

**Files:** Create `storage.py`, `schema.sql`, `tests/test_storage.py`, and `tests/test_storage_concurrency.py`.

**Interfaces:** `Storage.open(path, role)`; `enqueue_capture`, `claim_pending`, `complete_prediction`, `fail_capture`, `save_label`, `save_route_decision`, `purge_expired`, and report queries. Schema v1 includes captures, predictions, labels, calibrations, route decisions, and metadata.

- [ ] Test permissions, WAL, 25 ms/2 s busy timeouts, FIFO claim exactly once, three retries, 10,000 queue limit, 30-day purge, preserved aggregates, and rejection of destructive migrations.
- [ ] Add concurrent separate-connection writer/reader test.
- [ ] Run focused tests; expect failures.
- [ ] Implement schema and bounded transactions with `sqlite3`.
- [ ] Run tests; expect pass; commit `feat: add durable triage queue and retention`.

### Task 5: Laya backend and worker

**Files:** Create `laya_backend.py`, `worker.py`, `preload.py`, `scripts/preload-model`, `tests/test_laya_backend.py`, and `tests/test_worker.py`.

**Interfaces:** `DecisionBackend.predict(prompt) -> Prediction`; `LayaBackend(checkpoint, revision, local_files_only=True)`; `Worker.run_once() -> WorkerResult`; explicit preload is the only download path.

- [ ] Test exact two-question schema, full distributions, confidence, checkpoint metadata, malformed output, missing local checkpoint, and no implicit download using a fake backend.
- [ ] Test claim/complete, latency, three retries, five-failure breaker, 1/2/4/8/300 backoff, quarantine, and recovery.
- [ ] Run focused tests; expect failures.
- [ ] Implement import-isolated official Laya adapter and persistent worker; do not import model code in hook modules.
- [ ] Run fake-backend tests; commit `feat: add local laya inference worker`.

### Task 6: MCP review, reports, and calibration

**Files:** Create `mcp_server.py`, `reporting.py`, `calibration.py`, `skills/laya-triage-review/SKILL.md`, and their focused tests.

**Interfaces:** MCP tools `triage_health`, `triage_recent`, `triage_sample_for_review`, `triage_label`, `triage_report`, `triage_purge`; `fit_calibration(labels) -> CalibrationResult` with per-question temperature scaling and 80/20 held-out evaluation.

- [ ] Test tool schemas, hidden prompt by default, review access, stratified sample, label upsert, purge confirmation, and inability to mutate mode/model/effort.
- [ ] Test fewer than 50 labels, holdout below 10, 90% precision threshold, no passing threshold, confusion matrices, dangerous downgrade, percentiles, and routing opportunity.
- [ ] Run focused tests; expect failures.
- [ ] Implement MCP tools, review skill, calibration, and reports.
- [ ] Run tests; expect pass; commit `feat: expose triage review and reporting tools`.

### Task 7: Plugin hook and future routing adapters

**Files:** Create `hooks/hooks.json`, `adapters.py`, `app_server.py`, `tests/test_hooks.py`, `tests/test_adapters.py`, and `tests/test_app_server.py`.

**Interfaces:** Bundled `UserPromptSubmit` hook; `ShadowAdapter`, `AdvisoryAdapter`, `ActiveAdapter`; `build_turn_start(route, prompt, cwd) -> dict`. Active stays disabled without explicit mode plus promotion evidence.

- [ ] Test hook paths, shadow default, advisory accept/override/fallback audit, exact `turn/start` payload, timeout fallback, no in-progress mutation, canary percentages, kill switch, and rollback triggers.
- [ ] Run focused tests; expect failures.
- [ ] Implement hook and adapters with mocked App Server only.
- [ ] Run tests; expect pass; commit `feat: add codex hook and routing adapters`.

### Task 8: End-to-end verification and operations

**Files:** Create `tests/test_end_to_end.py`, `docs/operations.md`, `docs/privacy.md`; modify `README.md`.

**Interfaces:** Document setup, preload, hook trust, shadow enablement, review, mode gates, kill switch, purge, and uninstall.

- [ ] Test repository/projectless fixtures, fake inference, MCP labeling, report, retention purge, and absence of repository writes.
- [ ] Run `uv run pytest -v`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy src`.
- [ ] Validate manifests, inspect complete diff, and document advisory/active as disabled pending later approval.
- [ ] Commit `docs: add laya triage operations guide`.

### Task 9: Real-checkpoint bring-up

**Files:** Create `docs/bring-up.md` and `artifacts/.gitkeep`; modify `README.md`.

**Interfaces:** Consumes explicit download approval; records evidence without weights, raw prompts, secrets, or absolute paths.

- [ ] Preload the pinned multilingual checkpoint after approval and record integrity metadata.
- [ ] Measure at least 30 synthetic Thai, English, and mixed-language predictions.
- [ ] Record cold start, warm p50/p95, resident memory, errors, and 250 ms/4 GiB gate results.
- [ ] Verify fail-open behavior with stopped MCP and unavailable SQLite.
- [ ] Run full verification and inspect diff; commit `test: record local laya bring-up evidence`.
