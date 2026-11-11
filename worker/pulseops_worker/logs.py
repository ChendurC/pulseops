"""Logging setup: readable text on the console, one JSON object per line in a log file.

JSON logs can be searched and filtered by tools (grep, jq, AWS CloudWatch Logs Insights)
using fields such as correlation_id, job_id and tenant_id.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

CONTEXT_FIELDS = (
    "job_id",
    "tenant_id",
    "correlation_id",
    "worker_id",
    "attempt",
    "status",
    "total",
    "invalid",
    "duration_ms",
    "error_category",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "@timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "log.level": record.levelname,
            "service.name": "pulseops-worker",
            "log.logger": record.name,
            "message": record.getMessage(),
        }
        for key in CONTEXT_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["error.stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-friendly console output that still shows the context fields."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = " ".join(f"{k}={getattr(record, k)}" for k in CONTEXT_FIELDS if hasattr(record, k))
        return f"{base} {extras}".rstrip()


def configure_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "worker.log"

    console = logging.StreamHandler()
    console.setFormatter(TextFormatter("%(asctime)s %(levelname)s %(message)s"))
    to_file = logging.FileHandler(log_file, encoding="utf-8")
    to_file.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers[:] = [console, to_file]
    root.setLevel(logging.INFO)
    return log_file
