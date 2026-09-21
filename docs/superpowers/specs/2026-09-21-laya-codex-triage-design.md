# Laya Codex Triage Plugin — Design Specification

Date: 2026-09-21  
Status: Approved design; implementation not started

## 1. Purpose

Build a personal Codex plugin that evaluates repository-backed Codex prompts with Laya. The first pilot runs in shadow mode and measures whether Laya can recommend an appropriate model tier and reasoning effort without changing the active model, blocking prompts, steering the agent, or modifying repository files. The architecture also provides explicit advisory and active-routing extension points for later, separately approved rollout.

The experiment is successful only if it produces enough locally reviewed evidence to decide whether a later limited-routing experiment is justified.

## 2. Scope

### Included

- Codex tasks whose current working directory resolves to a Git repository.
- Prompt-time triage using the `UserPromptSubmit` lifecycle event.
- Two recommendations:
  - model tier: `fast`, `balanced`, or `deep`
  - reasoning effort: `low`, `medium`, `high`, or `xhigh`
- Local inference and local persistence.
- Secret redaction and prompt truncation before inference and persistence.
- Passive collection, sampled human review, calibration, and reporting.
- Plugin-bundled lifecycle hook, MCP server, and review skill.
- A versioned policy interface and routing-adapter contract that can support future advisory and active modes without replacing the shadow data path.

### Excluded from the pilot

- Projectless Codex tasks and ordinary chats.
- Automatic model or reasoning-effort changes.
- Enabling advisory or active routing during the initial pilot.
- Prompt blocking, tool blocking, permission decisions, or agent steering.
- Action-level supervision and completion judging.
- Uploading prompts, labels, or metrics to an external service.
- Reading repository files, transcripts, remote URLs, or environment variables for classification.
- Fine-tuning before the zero-shot and calibrated pilot results are known.

## 3. Design Principles

1. **Shadow means non-interfering.** Laya output is never injected into the active turn during the pilot.
2. **Fail open.** Plugin, model, queue, or database failure must not stop or alter Codex work.
3. **Local by default.** Prompt processing, model inference, persistence, review, and reports remain on the user's machine.
4. **Typed output, deterministic policy.** Laya supplies probability distributions; ordinary code handles abstention, retention, and reporting.
5. **Version everything.** Each prediction records the checkpoint revision, decision-schema version, redaction version, and calibration version.
6. **Evidence before routing.** No active-routing proposal is considered until the pilot gates in Section 12 pass.
7. **Mode changes require authority.** The plugin cannot promote itself from shadow to advisory or active mode; each promotion requires explicit user approval and a versioned configuration change.

## 4. Architecture

```text
Codex UserPromptSubmit
        |
        v
Plugin command hook: capture_prompt
  - verify cwd is in a Git repository
  - redact secrets
  - truncate normalized prompt
  - enqueue record and exit quickly
        |
        v
Local SQLite queue in plugin data directory
        |
        v
Persistent MCP server / worker
  - keep the selected Laya checkpoint loaded
  - consume queued prompts
  - write predictions, probabilities, and latency
        |
        +--> triage_health
        +--> triage_recent
        +--> triage_sample_for_review
        +--> triage_label
        +--> triage_report
        +--> triage_purge

Future pre-turn client (disabled during pilot)
        |
        +--> advisory adapter: display recommendation, require user choice
        |
        +--> active adapter: Policy Engine --> Codex App Server/SDK
                                             turn/start(model, effort)
```

The lifecycle hook is a short command hook rather than a direct MCP tool hook. Direct MCP hook execution is synchronous; queueing allows the active Codex prompt to continue without waiting for model inference. The MCP server provides a persistent process for warm inference and interactive review.

The future pre-turn client is a separate adapter. A `UserPromptSubmit` hook observes a prompt after the current Codex turn already has an active model, so it is not the control point for changing that turn's model. Active routing must classify before `turn/start`, then pass the selected `model` and `effort` through the Codex App Server or SDK. Ordinary Codex Desktop composer turns remain shadow or advisory unless the host later exposes an equivalent pre-start routing interface.

### 4.1 Plugin components

```text
laya-codex-triage/
├── .codex-plugin/plugin.json
├── hooks/hooks.json
├── skills/laya-triage-review/SKILL.md
├── mcp/
│   ├── server
│   ├── inference
│   ├── storage
│   ├── calibration
│   └── redaction
├── scripts/
│   ├── capture-prompt
│   ├── preload-model
│   └── purge-expired
└── tests/
```

The final language-specific filenames and package layout may follow the plugin creator's generated conventions, but the component boundaries and behavior in this specification are mandatory.

## 5. Prompt Capture Flow

For every `UserPromptSubmit` event:

1. Read only the stable hook fields needed by the pilot: `cwd`, `session_id`, `turn_id`, `model`, `permission_mode`, and `prompt`.
2. Resolve the Git root with a local, non-network Git command.
3. If no Git root exists, exit successfully without creating a record.
4. Normalize the prompt without expanding files, links, or references.
5. Redact recognized secrets and sensitive credential forms.
6. Truncate the redacted prompt to 4,000 Unicode characters.
7. Enqueue the record in a short SQLite transaction.
8. Exit successfully without emitting model-visible text.

The hook must not inspect repository content, invoke Laya directly, download dependencies, or start a long-running process.

## 6. Redaction and Privacy

Redaction occurs before both inference and persistence, so the reviewed text is exactly the text Laya classified.

The initial redactor covers:

- common API key and access-token prefixes;
- bearer and basic authorization values;
- private key blocks;
- credential-like `NAME=value` forms for names containing `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `API_KEY`, or `PRIVATE_KEY`;
- URLs containing embedded user information;
- configurable user-supplied patterns.

Redaction is intentionally conservative. Each replacement preserves only a type marker such as `[REDACTED_API_KEY]`.

Storage rules:

- Plugin data directory is user-only.
- Database file is user-readable and user-writable only.
- Remote repository URLs, environment variables, repository file contents, and full transcripts are never stored.
- Repository identity consists of the repository basename plus a salted fingerprint of the canonical root path.
- Session and turn identifiers are stored as salted hashes.
- Redacted prompt text is removed after 30 days.
- Predictions, human labels, and aggregate metrics without prompt text may remain until explicitly purged.

## 7. Laya Decision Contract

Laya receives the redacted prompt as state and answers two small-cardinality `choice` questions.

### 7.1 Model tier

| Choice | Criterion |
|---|---|
| `fast` | Direct, narrow, low-risk work with obvious verification and little ambiguity. |
| `balanced` | Ordinary implementation, diagnosis, review, or research requiring exploration and some judgment. |
| `deep` | Ambiguous, cross-system, architecture-sensitive, security-sensitive, production-sensitive, financial, destructive, migration, or data-integrity work. |

### 7.2 Reasoning effort

| Choice | Criterion |
|---|---|
| `low` | Steps and validation are obvious. |
| `medium` | Some exploration and judgment are required. |
| `high` | Dependencies, trade-offs, or verification are complex. |
| `xhigh` | Architecture, security, production, financial, migration, destructive, or data-sensitive decisions dominate. |

Laya returns the complete probability distribution for each question. The plugin records the distributions and does not treat the selected label as truth.

### 7.3 Abstention

Abstention is implemented by deterministic policy, not as another Laya label. A prediction is marked `abstain` when inference fails, the calibrated top probability is below the configured threshold, or the calibrated top-two margin is below the configured threshold.

Before calibration, reporting uses provisional thresholds of `top_probability >= 0.80` and `top_1_minus_top_2 >= 0.20`; these values cannot authorize active routing. After at least 50 reviewed labels, fit one temperature per decision question on an 80% training split and evaluate on a stratified 20% holdout containing at least 10 examples. Select the highest-coverage threshold that reaches at least 90% held-out precision and produces no dangerous downgrade. If the available labels cannot support the split or no threshold passes, calibrated high-confidence coverage is reported as unavailable. The policy must prefer abstention over a low-confidence downgrade.

## 8. Model Runtime

The pilot uses the official Laya package and pins the exact checkpoint revision.

Because the user's prompts commonly mix Thai and English, bring-up starts with the multilingual checkpoint as the single loaded model. The benchmark also measures the upstream language Router. Dual-checkpoint routing may replace multilingual-only inference only when all of the following hold:

- English exact-match accuracy improves by at least five percentage points on the reviewed pilot sample;
- Thai and mixed-language exact-match accuracy does not fall by more than two percentage points;
- persistent worker resident memory stays at or below 4 GiB;
- checkpoint routing and revisions are recorded per prediction;
- warm service reliability remains within the pilot gates.

Model acquisition occurs only during explicit setup or preload. A Codex turn must never trigger an unannounced model download.

## 9. Persistence Model

SQLite runs in WAL mode. Capture writes use a 25 ms busy timeout and one short transaction; MCP operations use a 2 second busy timeout. A busy capture fails open and increments the next available local dropped-capture counter. The logical records are:

### `captures`

- capture ID
- timestamps
- hashed session and turn IDs
- repository basename and fingerprint
- active Codex model
- permission mode
- redacted, truncated prompt
- prompt language metadata
- redaction and schema versions
- queue status and failure count

### `predictions`

- capture ID
- checkpoint identity and revision
- runtime identity
- model-tier distribution and selected label
- reasoning-effort distribution and selected label
- raw and calibrated confidence values
- abstention state and reason
- queue delay and inference latency
- inference error code, if any

### `labels`

- capture ID
- human model-tier label
- human reasoning-effort label
- optional short review note
- review timestamp

### `calibrations`

- calibration version
- source label window
- method and parameters
- evaluation metrics
- activation timestamp

### `route_decisions` (created before advisory rollout, unused in shadow-only pilot)

- capture or preflight ID
- operating mode
- suggested tier and effort
- policy-adjusted tier and effort
- mapped model slug and supported effort
- policy rule or safety floor applied
- user override, when present
- final applied model and effort
- adapter identity and version
- decision timestamp and launch outcome

Schema migrations must be additive during the pilot. Destructive migration requires an explicit backup and separate approval.

## 10. MCP Tools and Review Skill

### MCP tools

- `triage_health`: checkpoint, worker, queue, database, retention, latency, and error summary.
- `triage_recent`: recent predictions with filters; prompt text is omitted unless review access is explicitly requested.
- `triage_sample_for_review`: stratified sample of unlabeled predictions, prioritizing low margin, disagreements, and underrepresented repositories/languages.
- `triage_label`: write or update the two human labels and optional note.
- `triage_report`: accuracy, calibration, confusion matrices, coverage, latency, errors, and estimated routing opportunity.
- `triage_purge`: apply retention immediately or delete specified local experiment data after confirmation.

No pilot tool changes the active Codex model, reasoning effort, hook policy, or repository.

### Review skill

The review skill supports requests such as:

- "Review 15 Laya predictions."
- "Summarize the Laya pilot this week."
- "Show high-confidence disagreements."

It asks the reviewer for only the two target labels and an optional note. Default review size is 10–20 predictions per week. It never treats the active model as ground truth.

## 11. Future Operating Modes

The implementation uses one decision engine and three explicit operating modes. Mode is configuration, not a model output.

### 11.1 Shadow

- Capture and classify repository-backed prompts asynchronously.
- Persist predictions and metrics.
- Never display, inject, or apply routing decisions to the active turn.
- This is the only mode enabled by the initial implementation plan.

### 11.2 Advisory

- Run pre-turn classification synchronously through a dedicated preflight client.
- Display the suggested model tier, reasoning effort, calibrated confidence, and any policy floor.
- Let the user accept, override, or use the configured default.
- Record the suggestion and final user choice in `route_decisions`.
- If inference or policy evaluation fails, use the configured default and continue.

For the ordinary Codex Desktop composer, advisory may display a recommendation but cannot retroactively change the model already assigned to the submitted turn. Full accept-and-launch advisory behavior therefore uses the same App Server/SDK pre-turn client as active mode.

### 11.3 Active

- Classify before creating the Codex turn.
- Apply deterministic policy and safety floors.
- Map generic tier and effort labels to the current, versioned model catalog.
- Call Codex App Server/SDK `turn/start` with the resulting `model` and `effort`.
- Preserve a user override and global kill switch.
- Fail to the configured default; never fail closed.

Active mode cannot change the model of a turn already in progress. A later escalation applies to the next turn or starts a new routed continuation. Automatic mid-task changes may only escalate; they may never reduce tier or effort.

### 11.4 Policy Engine

The Policy Engine is deterministic and independent from Laya. Its ordered inputs are:

1. explicit user override;
2. repository-specific minimums;
3. hard safety floors;
4. calibrated Laya recommendation;
5. configured default fallback.

Hard safety floors prevent automatic downgrade below `deep/high` for authentication or authorization, secrets, money, tenant isolation, data integrity, migrations, destructive operations, production mutations, security-sensitive changes, and other configured protected categories. A low-confidence result abstains to the default. No policy path may map a human- or rule-classified `deep` task directly to `fast`.

The model catalog maps `fast`, `balanced`, and `deep` to model slugs and records which reasoning-effort values each model supports. Unsupported effort values round upward to the nearest supported value; they never round downward silently. Catalog updates are versioned and do not require retraining Laya.

### 11.5 Promotion and rollback

Allowed promotions are `shadow -> advisory -> active`; either advisory or active can return immediately to shadow. Promotion never occurs automatically.

Advisory becomes eligible only after the shadow gates in Section 12 pass. Active canary becomes eligible only after all of the following are true:

- advisory has at least 200 route decisions and 100 human-reviewed labels;
- eligible high-confidence model-tier precision is at least 90% on held-out data;
- there are zero dangerous `deep -> fast` recommendations in the reviewed set;
- policy, override, fallback, and kill-switch tests pass;
- the user explicitly approves active canary rollout.

Active rollout proceeds at 5%, 20%, 50%, and then 100% of eligible repository-backed preflight turns. Each stage requires a reviewed report. Automatic rollback to shadow occurs on any dangerous downgrade, routing error rate above 1%, or user-override rate above 10% for the stage. Rollback changes only routing mode; it never deletes evidence.

## 12. Pilot Phases and Acceptance Gates

### Phase 1: Bring-up

- Install and pin runtime dependencies.
- Explicitly download and verify checkpoint assets.
- Validate redaction before model or storage use.
- Measure cold start, warm inference, resident memory, and queue overhead.
- Prove fail-open behavior with the MCP server stopped, the model unavailable, a locked database, malformed hook input, and a non-repository working directory.

### Phase 2: Shadow collection

- Enable the plugin for repository-backed tasks.
- Collect at least 100 predictions.
- Review 10–20 sampled predictions per week.
- Do not inject prediction output into active turns.

### Phase 3: Calibration and decision report

- Begin calibration after at least 50 human-labeled predictions.
- Evaluate calibration on held-out labels rather than the same labels used to fit it.
- Publish a local report recommending one of: stop, collect more data, calibrate/fine-tune, or design a separately approved limited-routing experiment.

### Acceptance gates

| Measure | Required result |
|---|---|
| Workflow safety | No prompt blocked or altered by the pilot. |
| Hook reliability | Internal failures exit successfully and are counted locally. |
| Capture overhead | p95 at or below 50 ms. |
| Warm inference latency | p95 target at or below 250 ms on the user's machine. |
| Sample size | At least 100 predictions and 50 human labels. |
| Model-tier exact match | At least 80%. |
| Reasoning-effort exact match | At least 70%. |
| Reasoning-effort within one level | At least 95%. |
| High-confidence precision | At least 90% after calibration. |
| Dangerous downgrade | Zero high-confidence `fast` predictions when the human label is `deep`. |

Failure to meet the gates does not trigger looser thresholds automatically. The report must identify whether the likely remedy is more labels, revised criteria, checkpoint routing, domain fine-tuning, or abandoning the routing use case.

## 13. Failure Handling

- Hook failures are fail-open and produce no model-visible output.
- Queue records retry at most three times before entering an explicit terminal error state.
- Five consecutive inference failures open a circuit breaker. Consumption retries after 1, 2, 4, 8, and then 300 seconds until a health check succeeds.
- Queue capture continues while inference is paused up to 10,000 pending records. At the limit, new captures are dropped and counted rather than deleting existing evidence.
- Corrupt records are quarantined rather than retried indefinitely.
- Health and report tools expose failure counts without exposing raw secrets.
- Cleanup failure never deletes newer records and is retried later.
- Database corruption handling creates a diagnostic copy before attempting repair; no destructive repair occurs without explicit approval.

## 14. Verification Strategy

### Unit tests

- Git-repository inclusion and projectless exclusion.
- Redaction patterns, Unicode handling, and 4,000-character truncation.
- Stable salted hashing without retaining source identifiers.
- Decision request construction and response parsing.
- Abstention and calibration policy.
- Operating-mode state transitions and prevention of self-promotion.
- Policy ordering, safety floors, effort rounding, model-catalog versioning, and kill switch.
- Retention and purge behavior.
- Circuit breaker and retry transitions.

### Integration tests

- Hook input to queued capture.
- Worker consumption to stored prediction.
- MCP review sample to persisted human label.
- Report generation from a deterministic fixture dataset.
- Advisory accept, override, and default-fallback records.
- Active preflight mapping to a mocked App Server `turn/start` request.
- Concurrent hook writes and MCP reads under SQLite WAL.
- Plugin data permissions and absence of repository writes.

### Failure tests

- MCP server absent.
- Checkpoint absent after setup.
- Database busy or read-only.
- Invalid Laya output.
- Process interruption during a transaction.
- Prompt containing representative credential formats.
- Hook invocation outside a Git repository.
- Router timeout and App Server launch failure falling back to the configured default.
- Canary rollback triggers.

### Manual acceptance

- Review the plugin hook trust prompt before enabling it.
- Confirm no model-visible Laya output appears during normal work.
- Confirm a repository prompt is captured and a projectless prompt is skipped.
- Stop the MCP server and verify Codex continues normally.
- Inspect one stored record to confirm redaction and truncation.
- Run a sample review and generate the first local report.
- In a later advisory test environment, confirm accept and override are recorded without changing an already-started turn.
- In a later active test environment, confirm model and effort are selected before `turn/start` and the kill switch returns routing to shadow.

## 15. Rollout and Removal

The plugin starts disabled until setup, checkpoint preload, and bring-up verification pass. Enabling requires explicit hook trust in Codex. Removal must be reversible:

1. Disable the plugin and hook.
2. Stop the MCP server.
3. Optionally export aggregate metrics.
4. Purge plugin data only after explicit confirmation.
5. Uninstall the plugin without changing any repository.

## 16. Authoritative Sources

- OpenAI Codex Hooks: <https://learn.chatgpt.com/docs/hooks>
- OpenAI Plugin Architecture: <https://developers.openai.com/plugins/concepts/plugins>
- OpenAI Codex App Server: <https://developers.openai.com/docs/app-server>
- OpenAI Codex SDK: <https://developers.openai.com/docs/codex-sdk>
- Laya model card: <https://huggingface.co/convaiinnovations/laya>
- Laya repository: <https://github.com/NandhaKishorM/laya>

## 17. Next Gate

Implementation planning begins only after the user reviews and approves this written specification. The implementation plan must use the plugin-creation workflow, identify the target personal marketplace or plugin workspace, pin concrete dependencies and checkpoint revisions, and separate setup/download approval from normal local execution.
