"""Upload diagnostics accept only application-generated fields."""

import json
import logging
from contextvars import ContextVar

correlation_id = ContextVar("upload_correlation_id", default=None)
_logger = logging.getLogger("uvicorn.error")


def upload_event(stage: str, outcome: str, status: int | None = None) -> None:
    record = {
        "event": "document_upload",
        "correlation_id": correlation_id.get(),
        "stage": stage,
        "outcome": outcome,
    }
    if status is not None:
        record["status"] = status
    level = logging.WARNING if outcome in {"failed", "rejected"} else logging.INFO
    _logger.log(level, json.dumps(record, separators=(",", ":")))
