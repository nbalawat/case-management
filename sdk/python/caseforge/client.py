"""A small, dependency-light client: it gets and renews tokens, follows cursors, retries what is safe to retry and
turns problem documents into exceptions with the server's code and request id."""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

RETRY_STATUSES = {429, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, request_id: str | None, body: dict | None = None):
        super().__init__(f"{status} {code}: {detail} (request {request_id})")
        self.status, self.code, self.detail, self.request_id, self.body = status, code, detail, request_id, body or {}


class Client:
    """One application's connection to one CaseForge deployment.

    Pass `token` instead of a client id and secret to call as a person, with the token from your identity provider.
    """

    def __init__(self, base_url: str, client_id: str | None = None, client_secret: str | None = None, *, scopes: list[str] | None = None,
                 token: str | None = None, http: httpx.Client | None = None, max_retries: int = 3, timeout: float = 30.0, prefix: str = "/api/v1"):
        if not token and not (client_id and client_secret):
            raise ValueError("Give a client id and secret, or a person's token")
        self._base = base_url.rstrip("/") + prefix
        self._id, self._secret, self._scopes = client_id, client_secret, scopes
        self._token, self._expires = token, float("inf") if token else 0.0
        self._lock = threading.Lock()
        self._http = http or httpx.Client(timeout=timeout)
        self._retries = max_retries

    # --- tokens ----------------------------------------------------------------------------------------------------------
    def _bearer(self) -> str:
        with self._lock:
            if self._token is None or time.time() > self._expires - 30:
                form = {"grant_type": "client_credentials"} | ({"scope": " ".join(self._scopes)} if self._scopes else {})
                r = self._http.post(f"{self._base}/oauth/token", data=form, auth=(self._id, self._secret))
                if r.status_code != 200:
                    self._raise(r)
                body = r.json()
                self._token, self._expires = body["access_token"], time.time() + body["expires_in"]
            return self._token

    # --- requests --------------------------------------------------------------------------------------------------------
    @staticmethod
    def _raise(r: httpx.Response) -> None:
        try:
            body = r.json()
        except ValueError:
            body = {}
        raise ApiError(r.status_code, body.get("code") or body.get("error") or "error", body.get("detail") or r.text[:200],
                       body.get("request_id") or r.headers.get("x-request-id"), body)

    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None, headers: dict | None = None,
                idempotency_key: str | None = None) -> httpx.Response:
        """Writes carry an Idempotency-Key (one is made if you give none), so retrying them after a timeout is safe."""
        hdrs = dict(headers or {})
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            hdrs.setdefault("Idempotency-Key", idempotency_key or str(uuid.uuid4()))
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self._retries + 1):
            hdrs["Authorization"] = f"Bearer {self._bearer()}"
            try:
                r = self._http.request(method, f"{self._base}{path}", params=params, json=json, headers=hdrs)
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
                time.sleep(min(2 ** attempt, 10))
                continue
            if r.status_code == 401 and self._id and attempt == 0:
                self._token = None  # revoked or expired early: get a new one once
                continue
            if r.status_code in RETRY_STATUSES and attempt < self._retries:
                time.sleep(min(float(r.headers.get("retry-after", 2 ** attempt)), 30))
                continue
            if r.status_code >= 400:
                self._raise(r)
            return r
        self._raise(r)
        raise AssertionError("unreachable")

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, params=params).json()

    def post(self, path: str, body: Any = None, **kw) -> Any:
        r = self.request("POST", path, json=body if body is not None else {}, **kw)
        return r.json() if r.content else None

    def put(self, path: str, body: Any, **kw) -> Any:
        return self.request("PUT", path, json=body, **kw).json()

    def patch(self, path: str, body: Any, **kw) -> Any:
        return self.request("PATCH", path, json=body, **kw).json()

    def delete(self, path: str, **kw) -> None:
        self.request("DELETE", path, **kw)

    # --- lists and events ------------------------------------------------------------------------------------------------
    def paginate(self, path: str, **params) -> Iterator[dict]:
        """Every item of a cursor-paginated list, page by page."""
        cursor = None
        while True:
            page = self.get(path, **params, cursor=cursor)
            yield from page["data"]
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def events(self, after: int = 0, types: list[str] | None = None, limit: int = 200) -> tuple[list[dict], int]:
        """One page of the event feed and the sequence to continue from. Store the sequence to resume after a restart."""
        page = self.get("/events", after=after, types=",".join(types) if types else None, limit=limit)
        return page["data"], page["next_after"]

    def follow(self, after: int = 0, types: list[str] | None = None, poll: float = 5.0) -> Iterator[dict]:
        """Every event from `after` on, waiting for new ones. For a long-running consumer without webhooks."""
        while True:
            batch, after = self.events(after, types)
            yield from batch
            if not batch:
                time.sleep(poll)
