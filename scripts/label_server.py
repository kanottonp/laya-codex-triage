#!/usr/bin/env python3
"""Lightweight HTTP server for the Laya triage labeling dashboard."""

import argparse
import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Add src to path so we can import the project
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from laya_codex_triage import DEFAULT_PLUGIN_DATA
from laya_codex_triage.models import ModelTier, ReasoningEffort
from laya_codex_triage.storage import Storage

DASHBOARD_HTML = Path(__file__).resolve().parent / "label_dashboard.html"


def _json_response(handler: "LabelHandler", data: Any, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


class LabelHandler(BaseHTTPRequestHandler):
    storage: Storage

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[dashboard] {format % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path == "/":
            self._serve_dashboard()
        elif clean_path.startswith("/api/captures"):
            self._get_captures()
        elif clean_path == "/api/health":
            self._get_health()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/api/label":
            self._post_label()
        elif self.path == "/api/suggest":
            self._post_suggest()
        else:
            self.send_error(404)

    def _serve_dashboard(self) -> None:
        if not DASHBOARD_HTML.exists():
            self.send_error(500, "Dashboard HTML not found")
            return
        body = DASHBOARD_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_captures(self) -> None:
        rows = self.storage.recent_predictions(limit=200)
        items = []
        for row in rows:
            prediction = {}
            if row.get("prediction_json"):
                prediction = json.loads(str(row["prediction_json"]))

            items.append({
                "capture_id": row["capture_id"],
                "captured_at": row["captured_at"],
                "repository_name": row["repository_name"],
                "prompt": row["prompt"],
                "status": row["status"],
                "prediction": prediction,
                "inference_latency_ms": row.get("inference_latency_ms"),
                "queue_delay_ms": row.get("queue_delay_ms"),
                "label_model_tier": row.get("label_model_tier"),
                "label_reasoning_effort": row.get("label_reasoning_effort"),
            })
        _json_response(self, {"items": items})

    def _get_health(self) -> None:
        _json_response(self, {
            "captures": self.storage.count_records("captures"),
            "predictions": self.storage.count_records("predictions"),
            "labels": self.storage.count_records("labels"),
            "pending": self.storage.pending_count(),
        })

    def _post_label(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            capture_id = body["capture_id"]
            tier = ModelTier(body["model_tier"])
            effort = ReasoningEffort(body["reasoning_effort"])
            note = body.get("note")

            self.storage.save_label(capture_id, tier, effort, note=note)
            _json_response(self, {
                "ok": True,
                "capture_id": capture_id,
                "model_tier": tier.value,
                "reasoning_effort": effort.value,
            })
        except Exception as exc:
            _json_response(self, {"ok": False, "error": str(exc)}, status=400)

    def _post_suggest(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            prompt = body.get("prompt", "")
            model = body.get("model", "gpt-5.6-luna")
            api_base = body.get(
                "api_base", os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:10100/v1")
            )
            api_key = body.get("api_key", os.environ.get("OPENAI_API_KEY", "EMPTY"))

            system_prompt = (
                "You are an expert AI triage annotator for Codex prompts.\n"
                "Analyze the user's prompt and suggest the best Model Tier and Reasoning Effort.\n"
                "Model Tiers:\n"
                "- fast (e.g. gpt-5.6-luna): Direct, narrow, low-risk, mechanical work.\n"
                "- balanced (e.g. gpt-5.6-terra): Ordinary implementation, diagnosis, review.\n"
                "- deep (e.g. gpt-6-astra): Ambiguous, architecture, security, production.\n\n"
                "Reasoning Efforts:\n"
                "- low: Obvious steps and direct validation.\n"
                "- medium: Requires exploration, diagnosis, and planning.\n"
                "- high: Complex dependencies, trade-offs, or verification.\n"
                "- xhigh: Architecture, security, or heavy multi-system design.\n\n"
                "Output strictly a JSON object with keys:\n"
                '{"model_tier": "fast"|"balanced"|"deep", '
                '"reasoning_effort": "low"|"medium"|"high"|"xhigh", '
                '"reason": "<Concise explanation in Thai>"}'
            )

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Prompt to triage:\n\"\"\"\n{prompt}\n\"\"\""}
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"}
            }

            req = urllib.request.Request(
                f"{api_base.rstrip('/')}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                },
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                _json_response(self, {
                    "ok": True,
                    "model_tier": parsed.get("model_tier"),
                    "reasoning_effort": parsed.get("reasoning_effort"),
                    "reason": parsed.get("reason", ""),
                    "llm_model": model
                })
        except Exception as exc:
            _json_response(self, {"ok": False, "error": str(exc)}, status=500)

def main() -> None:
    parser = argparse.ArgumentParser(description="Laya triage labeling dashboard")
    parser.add_argument("--port", type=int, default=8477, help="HTTP port (default: 8477)")
    parser.add_argument("--plugin-data", type=Path, default=None)
    args = parser.parse_args()

    repo_data = Path(__file__).resolve().parent.parent / "data"
    default_data = repo_data if repo_data.exists() else DEFAULT_PLUGIN_DATA
    plugin_data = args.plugin_data or Path(os.environ.get("PLUGIN_DATA") or default_data)
    db_path = plugin_data / "triage.sqlite3"

    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    storage = Storage.open(db_path, role="mcp")
    LabelHandler.storage = storage

    server = HTTPServer(("127.0.0.1", args.port), LabelHandler)
    print("\n  Laya Triage Label Dashboard")
    print(f"  http://127.0.0.1:{args.port}")
    print(f"  Database: {db_path}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
        storage.close()


if __name__ == "__main__":
    main()
