"""Metrics exposition, alerting and report formatting."""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .util import iso

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_]")

# Counters the runner is known to own. `Counter` has no keys until they are
# incremented, so a counter that never fired is simply **absent** from /metrics -
# and absence is indistinguishable from "the metric was renamed or deleted".
# The 2-hourly inspection hit exactly that on 2026-10-12: it looked for
# `ta_orders_submitted_total` / `ta_fills_total` / `ta_venue_degraded_total`,
# found none of them, and could not tell "0 since this process started" from
# "monitoring is broken". Declaring them here makes 0 visible.
EXPECTED_COUNTERS: tuple[str, ...] = (
    "ta_orders_submitted_total",
    "ta_fills_total",
    "ta_venue_degraded_total",
)


class Metrics:
    """Tiny Prometheus-text-format registry (no third-party dependency)."""

    def __init__(self) -> None:
        self.counters: Counter[str] = Counter()
        self.gauges: dict[str, float] = {}

    @staticmethod
    def _clean(name: str) -> str:
        return _NAME_SAFE.sub("_", name)

    def inc(self, name: str, value: float = 1.0) -> None:
        self.counters[self._clean(name)] += value

    def set(self, name: str, value: float) -> None:  # noqa: A003 - deliberate API
        self.gauges[self._clean(name)] = float(value)

    def render(self) -> str:
        lines: list[str] = []
        declared = {self._clean(name) for name in EXPECTED_COUNTERS}
        for name in sorted(set(self.counters) | declared):
            value = self.counters.get(name, 0.0)
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")
        for name, value in sorted(self.gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"


class _Handler(BaseHTTPRequestHandler):
    metrics: Metrics | None = None

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.rstrip("/") not in ("", "/metrics"):
            self.send_response(404)
            self.end_headers()
            return
        body = (self.metrics.render() if self.metrics else "").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:  # silence per-request logging
        return None


class MetricsServer:
    def __init__(self, metrics: Metrics, host: str = "127.0.0.1", port: int = 9101) -> None:
        self.metrics = metrics
        handler = type("BoundHandler", (_Handler,), {"metrics": metrics})
        self._server = HTTPServer((host, port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.port = port

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/metrics"


class Alerts:
    """JSONL alert log + optional webhook (network failures never break trading)."""

    def __init__(self, path: str | Path, webhook_url: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.webhook_url = webhook_url
        self.sent = 0

    def send(self, level: str, title: str, **fields: Any) -> dict[str, Any]:
        payload = {"ts": iso(), "level": level, "title": title, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        self.sent += 1
        if self.webhook_url:
            try:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                request = urllib.request.Request(
                    self.webhook_url, data=body, headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(request, timeout=5)  # noqa: S310
            except Exception:
                pass
        return payload


def format_header(venue: str, dry_run: bool) -> str:
    mode = "DRY-RUN (no orders leave the process)" if dry_run else "LIVE ORDER SUBMISSION"
    return f"venue={venue}  mode={mode}"


def format_report(report: dict[str, Any]) -> str:
    lines = ["─" * 60]
    for key, value in report.items():
        if isinstance(value, float):
            rendered = f"{value:,.4f}"
        else:
            rendered = str(value)
        lines.append(f"  {key:<18} {rendered}")
    lines.append("─" * 60)
    return "\n".join(lines)
