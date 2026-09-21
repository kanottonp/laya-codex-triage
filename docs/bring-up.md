# Laya Real-Checkpoint Bring-Up

Status: **Completed and verified on local checkpoint**

The pinned multilingual checkpoint has been preloaded and verified locally with `local_files_only=True`.

## Identity

- Checkpoint: `convaiinnovations/laya-multilingual`
- Revision: `052592a15d198d9ad47da779604259b10b47b7aa`
- Runtime: `laya==0.3.4`
- Python: `3.12.11`

## Snapshot Integrity & Weights

- Files present: `model.safetensors` (644 MB), `rl_agent_config.json`, `encoder/config.json`, `tokenizer/tokenizer.json`, `tokenizer/tokenizer_config.json`
- Target revision verified: `052592a15d198d9ad47da779604259b10b47b7aa`
- Weights: 322M parameters mmBERT-base backbone with decision head

## Bring-Up Measurements (30 Synthetic Prompts)

Evaluated 30 synthetic prompts across 3 language categories (10 Thai, 10 English, 10 mixed Thai/English):

- **Cold start time:** 48.67 s
- **Peak resident memory (RSS):** 3,851.1 MB (~3.76 GiB)
- **Warm inference latency:**
  - p50: 179.99 ms
  - p95: 200.99 ms
  - min: 168.17 ms
  - max: 210.08 ms
- **Inference errors:** 0
- **Abstentions:** 29 / 30 (reflecting uncalibrated base checkpoint with tight provisional thresholds)

## Acceptance Gates

| Gate | Threshold | Measured | Status |
|---|---|---|---|
| Warm inference latency p95 | <= 250 ms | 200.99 ms | **PASS** |
| Resident memory (RSS) | <= 4,096 MB | 3,851.1 MB | **PASS** |
| Inference errors | 0 | 0 | **PASS** |

## Fail-Open Verification

1. **Stopped MCP Server:** When the MCP server is not running, the prompt capture hook exits with code `0`, emitting no stdout/stderr and without blocking Codex execution.
2. **Unavailable / Locked SQLite:** When the underlying SQLite database file is read-only or temporarily unavailable, capture fails open cleanly, returning exit code `0` and no errors to Codex.
