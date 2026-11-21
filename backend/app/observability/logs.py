"""Structured logs. LOG_FORMAT=json writes one JSON object per line with the request id, client and trace id of the
request that wrote it, which is what log shippers (Fluent Bit, Vector, a cloud's agent) index. LOG_FORMAT=text, the
default on a laptop, stays readable in a terminal."""
from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timezone

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant: ContextVar[str | None] = ContextVar("tenant", default=None)

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def _trace_id() -> str | None:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


class ContextFilter(logging.Filter):
    """Stamps every record with the request it belongs to, whichever format writes it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        record.tenant = tenant.get()
        record.trace_id = _trace_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"), "level": record.levelname.lower(),
               "logger": record.name, "message": record.getMessage()}
        for k in ("request_id", "tenant", "trace_id"):
            if getattr(record, k, None):
                doc[k] = getattr(record, k)
        for k, v in record.__dict__.items():  # anything passed with extra={...}
            if k not in _RESERVED and k not in doc and k not in ("request_id", "tenant", "trace_id") and not k.startswith("_"):
                doc[k] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
        if record.exc_info:
            doc["error"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        rid = getattr(record, "request_id", None)
        return f"{line} [{record.tenant or '-'} {rid}]" if rid else line


def configure(fmt: str | None = None, level: str | None = None) -> None:
    """Once per process. Safe to call again: the handler is replaced, not added twice."""
    fmt = (fmt or os.getenv("LOG_FORMAT", "text")).lower()
    root = logging.getLogger()
    root.setLevel((level or os.getenv("LOG_LEVEL", "INFO")).upper())
    for h in list(root.handlers):
        if getattr(h, "_caseforge", False):
            root.removeHandler(h)
    handler = logging.StreamHandler()
    handler._caseforge = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(ContextFilter())
    root.addHandler(handler)
    if fmt == "json":  # uvicorn's own access log would repeat ours in another shape
        logging.getLogger("uvicorn.access").disabled = True
