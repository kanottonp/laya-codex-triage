---
name: laya-triage-review
description: Review sampled Laya prompt-triage predictions, label the intended model tier and reasoning effort, and summarize pilot health or reports.
---

Use the bundled triage MCP tools only. Prompt text appears only when a review explicitly requests it.

1. Call triage_sample_for_review for 10–20 predictions, or triage_recent when the user names a specific recent item.
2. For each sampled prediction, judge only the original task complexity. Do not treat the active Codex model as ground truth.
3. Assign one model_tier value (fast, balanced, or deep) and one reasoning_effort value (low, medium, high, or xhigh). Add a short optional note only when it clarifies the label.
4. Call triage_label once per reviewed prediction. Reuse the capture ID to correct a label.
5. For summaries or weekly review, call triage_report and triage_health; explain calibration availability, dangerous downgrades, latency, and sample size without exposing prompts or secrets.

Never change operating mode, model, reasoning effort, hook policy, or repository files through this skill.
