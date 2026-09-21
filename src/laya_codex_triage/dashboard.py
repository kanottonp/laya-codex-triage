"""Loopback HTTP dashboard served by the Laya MCP process."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from threading import Thread
from typing import Any, cast

from .models import ModelTier, ReasoningEffort
from .storage import Storage

_DASHBOARD_HTML = files("laya_codex_triage.resources").joinpath("label_dashboard.html")


class DashboardController:
    """Serve the local review UI over a single loopback listener."""

    def __init__(self, storage: Storage, *, host: str = "127.0.0.1", port: int = 11020) -> None:
        self._storage = storage
        self._host = host
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def start(self) -> bool:
        if self._server is not None:
            return False
        server = HTTPServer((self._host, self._port), self._handler_type())
        self._port = int(server.server_address[1])
        self._server = server
        self._thread = Thread(target=server.serve_forever, daemon=True, name="laya-dashboard")
        self._thread.start()
        return True

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._server = None
        self._thread = None

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        storage = self._storage

        class DashboardHandler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/":
                    self._html()
                elif path == "/api/captures":
                    self._captures()
                elif path == "/api/health":
                    self._json(
                        {
                            "captures": storage.count_records("captures"),
                            "predictions": storage.count_records("predictions"),
                            "labels": storage.count_records("labels"),
                            "pending": storage.pending_count(),
                        }
                    )
                else:
                    self.send_error(404)

            def do_POST(self) -> None:
                if self.path != "/api/label":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = cast(dict[str, object], json.loads(self.rfile.read(length)))
                    capture_id = str(body["capture_id"])
                    tier = ModelTier(str(body["model_tier"]))
                    effort = ReasoningEffort(str(body["reasoning_effort"]))
                    note = body.get("note")
                    storage.save_label(
                        capture_id, tier, effort, note=note if isinstance(note, str) else None
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    self._json({"ok": False, "error": str(error)}, status=400)
                    return
                self._json(
                    {
                        "ok": True,
                        "capture_id": capture_id,
                        "model_tier": tier.value,
                        "reasoning_effort": effort.value,
                    }
                )

            def _html(self) -> None:
                body = _DASHBOARD_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _captures(self) -> None:
                items: list[dict[str, object]] = []
                for row in storage.recent_predictions(limit=200):
                    prediction = (
                        json.loads(str(row["prediction_json"]))
                        if row["prediction_json"]
                        else {}
                    )
                    items.append(
                        {
                            "capture_id": row["capture_id"],
                            "captured_at": row["captured_at"],
                            "repository_name": row["repository_name"],
                            "prompt": row["prompt"],
                            "status": row["status"],
                            "prediction": prediction,
                            "inference_latency_ms": row["inference_latency_ms"],
                            "queue_delay_ms": row["queue_delay_ms"],
                            "label_model_tier": row["label_model_tier"],
                            "label_reasoning_effort": row["label_reasoning_effort"],
                        }
                    )
                self._json({"items": items})

            def _json(self, value: Any, *, status: int = 200) -> None:
                body = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return DashboardHandler
