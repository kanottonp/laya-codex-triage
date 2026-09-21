import json
import re
import subprocess
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "scripts" / "label_dashboard.html"


def _format_timings(item: dict[str, object]) -> list[str]:
    source = DASHBOARD.read_text()
    match = re.search(
        r"function formatMilliseconds\(value, status\) \{.*?\n\}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, "dashboard must provide a reusable millisecond formatter"
    latency = json.dumps(item.get("inference_latency_ms"))
    queue = json.dumps(item.get("queue_delay_ms"))
    status = json.dumps(item.get("status"))
    javascript = (
        f"{match.group(0)}\nconsole.log(JSON.stringify(["
        f"formatMilliseconds({latency}, {status}),"
        f"formatMilliseconds({queue}, {status})]));"
    )
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            javascript,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_dashboard_formats_timing_values_in_milliseconds() -> None:
    assert _format_timings(
        {
            "prediction": {"model_tier": "fast"},
            "status": "completed",
            "inference_latency_ms": 2093.61,
            "queue_delay_ms": 123955.765,
        }
    ) == ["2094 ms", "123956 ms"]


def test_dashboard_marks_unprocessed_timings_as_pending() -> None:
    assert _format_timings(
        {
            "prediction": {},
            "status": "pending",
            "inference_latency_ms": None,
            "queue_delay_ms": None,
        }
    ) == ["pending", "pending"]
