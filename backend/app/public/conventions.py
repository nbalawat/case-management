"""Shared conventions of the public API: problem documents, cursor pagination, ETags.

Errors follow RFC 9457 (application/problem+json) with a stable `code` and the request id, so an integrator can
branch on the code and quote the id to support. Lists are keyset-paginated with an opaque cursor, which stays
correct and fast however deep the list goes, unlike page numbers.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime
from typing import Generic, TypeVar

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM = "application/problem+json"
DOCS = "https://caseforge.dev/problems/"  # stable identifiers; the developer guide lists each one
T = TypeVar("T")
TITLES = {400: "Bad request", 401: "Not signed in", 403: "Forbidden", 404: "Not found", 405: "Method not allowed", 409: "Conflict",
          412: "Precondition failed", 413: "Request too large", 415: "Unsupported media type", 422: "Invalid request",
          428: "Precondition required", 429: "Too many requests", 500: "Server error", 503: "Unavailable"}
CODES = {400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed", 409: "conflict",
         412: "precondition_failed", 413: "payload_too_large", 415: "unsupported_media_type", 422: "invalid_request",
         428: "precondition_required", 429: "rate_limited", 500: "internal", 503: "unavailable"}


class ApiError(HTTPException):
    """An error with a stable machine-readable code."""

    def __init__(self, status: int, detail: str, code: str | None = None, headers: dict | None = None, **extra):
        super().__init__(status, detail, headers)
        self.code, self.extra = code or CODES.get(status, "error"), extra


class Problem(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    code: str
    request_id: str | None = None
    errors: list[dict] | None = None


def problem(request: Request, status: int, detail: str, code: str | None = None, headers: dict | None = None, **extra) -> JSONResponse:
    code = code or CODES.get(status, "error")
    body = {"type": DOCS + code, "title": TITLES.get(status, "Error"), "status": status, "detail": detail, "code": code,
            "request_id": getattr(request.state, "request_id", None)} | extra
    return JSONResponse(jsonable_encoder(body), status, headers=headers, media_type=PROBLEM)


def install(app) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, e: ApiError):
        return problem(request, e.status_code, str(e.detail), e.code, e.headers, **e.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        return problem(request, e.status_code, str(e.detail), headers=getattr(e, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, e: RequestValidationError):
        errors = [{"location": ".".join(str(x) for x in err.get("loc", ())), "message": err.get("msg", ""), "type": err.get("type", "")}
                  for err in e.errors()]
        return problem(request, 422, "The request does not match the schema", "invalid_request", errors=errors)

    @app.exception_handler(Exception)
    async def _crash(request: Request, e: Exception):  # never leak a stack trace or an internal message
        import logging

        logging.getLogger("caseforge.public").exception("Unhandled error in %s %s", request.method, request.url.path)
        return problem(request, 500, "Something went wrong on our side. Quote the request id to support.", "internal")


# --- pagination -------------------------------------------------------------------------------------------------------

class Page(BaseModel, Generic[T]):
    data: list[T]
    next_cursor: str | None = Field(None, description="Pass as `cursor` for the next page. Absent on the last page.")
    has_more: bool = False


def encode_cursor(*values) -> str:
    raw = json.dumps([v.isoformat() if isinstance(v, datetime) else v for v in values], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, kinds: tuple[type, ...]) -> tuple | None:
    """The values a cursor holds, checked against the kinds this list expects. A forged or stale cursor is a 400."""
    if not cursor:
        return None
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(values, list) or len(values) != len(kinds):
            raise ValueError
        return tuple(datetime.fromisoformat(v) if k is datetime else k(v) for v, k in zip(values, kinds))
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise ApiError(400, "The cursor is not one this list gave out", "invalid_cursor") from None


def limit_of(limit: int, ceiling: int = 200) -> int:
    if not (1 <= limit <= ceiling):
        raise ApiError(422, f"limit must be between 1 and {ceiling}", "invalid_request")
    return limit


def page(rows: list, limit: int, key) -> dict:
    """Rows fetched with limit + 1, cut to the page, with the cursor for the next one made from the last row's key."""
    more = len(rows) > limit
    rows = rows[:limit]
    return {"rows": rows, "has_more": more, "next_cursor": encode_cursor(*key(rows[-1])) if more and rows else None}


# --- concurrency ------------------------------------------------------------------------------------------------------

def etag(*parts) -> str:
    return '"' + hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:20] + '"'


def check_if_match(if_match: str | None, current: str) -> None:
    """Optimistic concurrency: a write that names the version it read is refused if the resource has moved on."""
    if if_match is None:
        return
    offered = {x.strip().removeprefix("W/") for x in if_match.split(",")}
    if "*" not in offered and current not in offered:
        raise ApiError(412, "The case changed since you read it. Read it again and retry.", "stale_version", headers={"ETag": current})
