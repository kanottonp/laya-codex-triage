#!/usr/bin/env python3
"""Re-label Laya triage captures."""
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DB = Path.home() / ".codex" / "plugins" / "data" / "knt-laya" / "triage.sqlite3"


def save_label(capture_id: str, tier: str, effort: str, note: str) -> None:
    conn = sqlite3.connect(DB, timeout=2)
    conn.execute("PRAGMA busy_timeout = 2000")
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO labels(capture_id, model_tier, reasoning_effort, note, reviewed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(capture_id) DO UPDATE SET
            model_tier = excluded.model_tier,
            reasoning_effort = excluded.reasoning_effort,
            note = excluded.note,
            reviewed_at = excluded.reviewed_at
        """,
        (capture_id, tier, effort, note, now),
    )
    conn.commit()
    conn.close()
    print(f"Saved: {capture_id[:8]}... -> {tier}/{effort}")


save_label(
    "d4a214a5-6b2b-41d7-aca1-1d089dd3f46a",
    "fast",
    "low",
    "Re-label: straightforward hook verification, no deep analysis needed",
)

save_label(
    "f853a427-c766-4240-aa9c-5d2944cfcd63",
    "balanced",
    "medium",
    "Re-label: diagnosing hook/worker lifecycle flow, moderate analysis needed",
)

