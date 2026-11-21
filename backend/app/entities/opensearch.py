"""The search index on OpenSearch.

The engine finds candidates among millions of names; the ranking is the same function the database index uses
(`search._score`), so whatever the database index finds the engine finds with the same score, and it also finds
names a pattern match cannot (through accents and punctuation). Each client has its own
index. What the engine returns is only ids: the rows are read again through the caller's session, so the client
boundary and case visibility are enforced where they always are, not by the index.

The index is rebuilt whole after entity resolution (which is itself a whole rebuild) into a new index, and the
client's alias is switched to it in one step, so a search never sees a half-built index.
"""
from __future__ import annotations

import json
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..domain.models import Entity, Party
from ..tenancy import tenant_of
from . import names

try:
    from opensearchpy import OpenSearch, helpers
    from opensearchpy.exceptions import NotFoundError, OpenSearchException
except ImportError:  # pragma: no cover - only without the optional dependency
    OpenSearch = None

log = logging.getLogger("caseforge.search")

# Names are normalised here, by the same functions the ranking uses, and indexed as exact values. Nothing depends on
# the engine's own analysers, so every distribution behaves the same and "what matches" is decided in one place.
MAPPING = {
    "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
    "mappings": {"dynamic": "strict", "properties": {
        "kind": {"type": "keyword"}, "ref": {"type": "keyword"}, "ref_lower": {"type": "keyword"},
        "folded": {"type": "keyword"},  # each spelling, lower case with accents removed, whole: for "contains"
        "words": {"type": "keyword"},   # the words of every spelling: for near spellings
        "weight": {"type": "integer"}}},
}


def _doc(kind: str, ref: str, spellings: list[str], weight: int, extra: set[str] = frozenset()) -> dict:
    folded = sorted({names.fold(x) for x in spellings} | set(extra))
    return {"kind": kind, "ref": ref, "ref_lower": ref.lower(), "folded": folded, "weight": weight,
            "words": sorted({w for x in spellings for w in names.tokens(x)} | {w for f in folded for w in f.split()})}


class SearchError(Exception):
    """The search engine could not be used. The message is safe to show an operator."""


def alias(tenant_id: str) -> str:
    return f"{settings.event_topic_prefix}-{tenant_id}-names"


class OpenSearchIndex:
    """`customers` and `counterparties` as the search module expects them, answered by OpenSearch."""

    def __init__(self, url: str, options: dict | None = None) -> None:
        if OpenSearch is None:
            raise SearchError("The OpenSearch client is not installed. Install the 'search' dependency group (uv sync --group search).")
        hosts = [h for h in url.split("://", 1)[1].split(",") if h]
        if not hosts:
            raise SearchError("SEARCH_URL needs at least one host:port")
        scheme = "https" if url.startswith("opensearchs://") else "http"
        self.client = OpenSearch(hosts=[f"{scheme}://{h}" for h in hosts], timeout=10, max_retries=2, retry_on_timeout=True, **(options or {}))

    # ---- building

    def rebuild(self, s: Session) -> dict:
        """Index every customer and counterparty of this session's client, then switch the alias to the new index."""
        tenant = tenant_of(s)
        name = f"{alias(tenant)}-{int(time.time() * 1000)}"
        try:
            self.client.indices.create(index=name, body=MAPPING)
            n, errors = helpers.bulk(self.client, self._docs(s, name), chunk_size=2000, raise_on_error=False)
            if errors:
                raise SearchError(f"{len(errors)} names could not be indexed; the first: {json.dumps(errors[0])[:300]}")
            self.client.indices.refresh(index=name)
            old = self._behind(tenant)
            self.client.indices.update_aliases(body={"actions": [{"remove": {"index": o, "alias": alias(tenant)}} for o in old]
                                                     + [{"add": {"index": name, "alias": alias(tenant)}}]})
            for o in old:
                self.client.indices.delete(index=o, ignore_unavailable=True)
        except OpenSearchException as e:
            self.client.indices.delete(index=name, ignore_unavailable=True)
            raise SearchError(f"The search index could not be rebuilt: {e}") from None
        return {"index": name, "names": n}

    def _behind(self, tenant_id: str) -> list[str]:
        try:
            return sorted(self.client.indices.get_alias(name=alias(tenant_id)))
        except NotFoundError:
            return []

    @staticmethod
    def _docs(s: Session, index: str):
        for p in s.scalars(select(Party)):
            yield {"_index": index, "_id": f"customer:{p.id}"} | _doc("customer", p.id, [p.name], 0)
        for e in s.scalars(select(Entity).where(Entity.kind == "counterparty")):
            yield {"_index": index, "_id": f"counterparty:{e.id}"} | _doc("counterparty", e.id, [e.name] + [a["name"] for a in e.aliases], e.txns, {e.key})

    def exists(self, tenant_id: str) -> bool:
        return bool(self._behind(tenant_id))

    def count(self, tenant_id: str) -> int:
        try:
            return self.client.count(index=alias(tenant_id))["count"]
        except NotFoundError:
            return 0

    # ---- asking

    def _candidates(self, s: Session, kind: str, q: str, size: int) -> list[str]:
        tenant = tenant_of(s)
        if not self.exists(tenant):  # first use for this client, or its index was lost: build it now
            self.rebuild(s)
        folded = names.fold(q).strip()
        words = names.tokens(q)
        should: list[dict] = [
            {"term": {"ref_lower": {"value": folded, "boost": 10}}},
            {"prefix": {"ref_lower": {"value": folded, "boost": 5}}},
            {"wildcard": {"folded": {"value": f"*{_literal(folded)}*", "boost": 3}}},
        ]
        if words:
            # Near spellings, one typed word at a time, as the ranking judges them. Two edits is the engine's limit.
            should.append({"bool": {"must": [{"fuzzy": {"words": {"value": w, "fuzziness": 2, "prefix_length": 0, "max_expansions": 200}}} for w in words]}})
            should.append({"wildcard": {"folded": {"value": f"*{_literal(names.key(q))}*"}}})
        body = {"size": size, "_source": ["ref"], "query": {"bool": {"filter": [{"term": {"kind": kind}}], "should": should, "minimum_should_match": 1}},
                "sort": ["_score", {"weight": "desc"}, {"ref": "asc"}]}
        try:
            return [h["_source"]["ref"] for h in self.client.search(index=alias(tenant), body=body)["hits"]["hits"]]
        except OpenSearchException as e:
            raise SearchError(f"The search engine did not answer: {e}") from None

    def customers(self, s: Session, q: str, limit: int) -> list[tuple[float, Party]]:
        from .search import _score

        ids = self._candidates(s, "customer", q, limit * 10)
        if not ids:
            return []
        typed = q.strip().lower()
        scored = [(1.0 if p.id.lower() == typed else max(_score(q, p.name), 0.9 if p.id.lower().startswith(typed) else 0), p)
                  for p in s.scalars(select(Party).where(Party.id.in_(ids)))]  # through the session: this client's rows only
        return sorted([x for x in scored if x[0] > 0], key=lambda x: (-x[0], x[1].name))[:limit]

    def counterparties(self, s: Session, q: str, limit: int) -> list[tuple[float, Entity]]:
        from .search import _score

        if not names.key(q):
            return []
        ids = self._candidates(s, "counterparty", q, limit * 10)
        if not ids:
            return []
        pool = s.scalars(select(Entity).where(Entity.kind == "counterparty", Entity.id.in_(ids)))
        scored = [(max([_score(q, e.name)] + [_score(q, a["name"]) * 0.98 for a in e.aliases]), e) for e in pool]
        return sorted([x for x in scored if x[0] > 0], key=lambda x: (-x[0], -x[1].txns))[:limit]


def _literal(text: str) -> str:
    """A typed string as a literal inside a wildcard pattern: its own * and ? mean themselves."""
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")


def connect(url: str | None = None) -> OpenSearchIndex | None:
    """The search engine this deployment is configured for, or None when it searches the database."""
    url = settings.search_url if url is None else url
    if not url:
        return None
    if not url.startswith(("opensearch://", "opensearchs://")):
        raise SearchError(f"Unknown search engine {url.split('://')[0]!r}. Use opensearch://host:9200 (opensearchs:// for TLS).")
    try:
        options = json.loads(settings.search_config) if settings.search_config else {}
    except ValueError as e:
        raise SearchError(f"SEARCH_CONFIG is not valid JSON: {e}") from None
    return OpenSearchIndex(url, options)
