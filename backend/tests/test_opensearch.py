"""Name search on a real OpenSearch, held to the database index's answers.

Runs when TEST_OPENSEARCH_URL names a disposable node, for example TEST_OPENSEARCH_URL=opensearch://127.0.0.1:9200.
Each test uses its own index prefix and removes its indices afterwards.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.domain import models as m
from app.entities import opensearch, resolution, search
from app.entities.opensearch import OpenSearchIndex, SearchError
from support import T

URL = os.getenv("TEST_OPENSEARCH_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("opensearch"), reason="set TEST_OPENSEARCH_URL to a disposable OpenSearch node to run")
OTHER = "kestrel"
NOW = m.now()


@pytest.fixture
def engine():
    old = settings.event_topic_prefix, settings.search_url
    object.__setattr__(settings, "event_topic_prefix", f"cft{uuid.uuid4().hex[:10]}")
    object.__setattr__(settings, "search_url", URL)
    idx = opensearch.connect()
    yield idx
    idx.client.indices.delete(index=f"{settings.event_topic_prefix}-*", ignore_unavailable=True)
    object.__setattr__(settings, "event_topic_prefix", old[0])
    object.__setattr__(settings, "search_url", old[1])
    search._engine = search._engine_for = None


def people(s, rows):
    for pid, name in rows:
        s.add(m.Party(id=pid, name=name, risk_rating="medium"))
        s.add(m.Account(id=f"A{pid}", party_id=pid))
    s.flush()


def pay(s, pid, to, n=1):
    for _ in range(n):
        s.add(m.Transaction(id=m.new_id("T"), account_id=f"A{pid}", party_id=pid, ts=NOW, amount=100, direction="out", channel="wire", counterparty=to))
    s.flush()


NAMES = [("P10001", "Tomás Bergström"), ("P10002", "Tom Berg"), ("P20000", "Elena Okafor"), ("P20001", "Elena Okonkwo"), ("P30000", "Wei Chen"),
         ("P30001", "Chen Wei"), ("P40000", "Jean-Luc O'Brien"), ("P40001", "ACME Holdings (Cayman) Ltd"), ("P50000", "Müller & Söhne GmbH"),
         ("P50001", "Mohammed Al-Rashid"), ("P50002", "Muhammad Al Rashid"), ("P60000", "Ana"), ("P60001", "Anastasia Petrova"), ("P70000", "100% Pure Trading")]
FIRMS = [("P10001", "Volkov Maritime Trading", 3), ("P10001", "VOLKOV MARITIME TRADING CO", 1), ("P10002", "Volkov Marine Trading", 1),
         ("P20000", "Harbor Grocers", 2), ("P20001", "Harbour Grocers Ltd", 1), ("P30000", "Société Générale de Transport", 1),
         ("P30001", "Northwind Logistics LLC", 4), ("P40000", "North Wind Cafe", 1)]
QUERIES = ["tomas bergstrom", "BERGSTRÖM", "tom", "P10001", "p1000", "okafor", "Elena Okaforr", "elena", "zzz", "chen", "wei chen", "obrien", "o'brien",
           "jean-luc", "acme", "cayman", "muller", "müller", "sohne", "al-rashid", "mohamed al rashid", "ana", "anast", "petrov", "petrova", "100%", "pure trad",
           "volkov", "volkov maritime trading co", "Volkof Maritime", "grocer", "harbor", "harbour", "societe generale", "generale", "northwind", "north wind",
           "logistics", "wind", "x*", "?", "a*b", "%", "_____", "' OR 1=1 --", "tradng", "maritme", "berg", "P2", "p", "rashid muhammad"]


@pytest.fixture
def filled(s, engine):
    people(s, NAMES)
    for pid, firm, n in FIRMS:
        pay(s, pid, firm, n)
    resolution.resolve(s)  # rebuilds the engine's index as its last step
    return s


@pytest.mark.parametrize("q", QUERIES)
def test_the_engine_finds_everything_the_database_does_ranked_the_same(filled, engine, q):
    for kind in ("customers", "counterparties"):
        want = [(round(sc, 4), x.id) for sc, x in getattr(search.index, kind)(filled, q, 8)]
        got = [(round(sc, 4), x.id) for sc, x in getattr(engine, kind)(filled, q, 8)]
        assert [x for x in got if x in want] == want, (kind, q)  # every database answer, same score, same order
        assert all(sc > 0 for sc, _ in got)


MORE = {"obrien": ["Jean-Luc O'Brien"], "muller": ["Müller & Söhne GmbH"], "sohne": ["Müller & Söhne GmbH"], "al-rashid": ["Mohammed Al-Rashid", "Muhammad Al Rashid"]}


@pytest.mark.parametrize("q", MORE)
def test_the_engine_also_finds_what_a_like_query_cannot(filled, engine, q):
    """Accents and punctuation the database's pattern match cannot see through, and near spellings its shortlist misses."""
    assert [p.name for _, p in engine.customers(filled, q, 8)] == MORE[q]
    assert len(search.index.customers(filled, q, 8)) < len(MORE[q])


def test_the_search_box_is_answered_by_the_engine(filled, engine, monkeypatch):
    monkeypatch.setattr(search.index, "customers", lambda *a: pytest.fail("the database index was asked"))
    monkeypatch.setattr(search.index, "counterparties", lambda *a: pytest.fail("the database index was asked"))
    out = search.search(filled, "volkov")
    assert [x["title"] for x in out["groups"]["counterparty"]] == ["Volkov Maritime Trading", "Volkov Marine Trading"]
    assert [x["title"] for x in search.search(filled, "bergstrom")["groups"]["customer"]] == ["Tomás Bergström"]


def test_every_name_and_spelling_is_indexed(filled, engine):
    firms = list(filled.scalars(select(m.Entity).where(m.Entity.kind == "counterparty")))
    assert engine.count(T) == len(NAMES) + len(firms)
    one = lambda ref: engine.client.search(index=opensearch.alias(T), body={"query": {"term": {"ref": ref}}})["hits"]["hits"][0]["_source"]  # noqa: E731
    assert one("P50000") == {"kind": "customer", "ref": "P50000", "ref_lower": "p50000", "folded": ["muller & sohne gmbh"], "weight": 0,
                             "words": ["&", "gmbh", "muller", "sohne"]}
    volkov = next(e for e in firms if e.name == "Volkov Maritime Trading")
    assert one(volkov.id)["folded"] == ["volkov maritime trading", "volkov maritime trading co"] and one(volkov.id)["weight"] == 4


def test_a_rebuild_swaps_the_index_in_one_step_and_drops_the_old_one(filled, engine):
    before = engine._behind(T)
    people(filled, [("P90000", "Zebedee Quince")])
    assert engine.customers(filled, "quince", 8) == []  # not indexed yet
    out = engine.rebuild(filled)
    after = engine._behind(T)
    assert len(before) == len(after) == 1 and before != after and out["index"] == after[0] and out["names"] == engine.count(T)
    assert [p.id for _, p in engine.customers(filled, "quince", 8)] == ["P90000"]
    assert not engine.client.indices.exists(index=before[0])


def test_each_client_has_its_own_index(db, engine):
    for tenant, rows in ((T, [("P1", "Ada Stone")]), (OTHER, [("P1", "Ada Stonehouse"), ("P2", "Ben Stone")])):
        db.onboard(tenant)
        with db.tenant(tenant) as s:
            people(s, rows)
            resolution.resolve(s)
            s.commit()
    assert engine.count(T) == 1 and engine.count(OTHER) == 2 and opensearch.alias(T) != opensearch.alias(OTHER)
    with db.tenant(T) as s:
        assert [p.name for _, p in engine.customers(s, "stone", 8)] == ["Ada Stone"]
    with db.tenant(OTHER) as s:
        assert sorted(p.name for _, p in engine.customers(s, "stone", 8)) == ["Ada Stonehouse", "Ben Stone"]


def test_ids_from_a_stale_index_cannot_show_another_clients_rows(db, engine):
    """Even if an index held the wrong ids, rows are read through the caller's session."""
    db.onboard(T)
    db.onboard(OTHER)
    with db.tenant(OTHER) as s:
        people(s, [("P7", "Only At Kestrel")])
        s.commit()
        engine.rebuild(s)
    engine.client.indices.put_alias(index=engine._behind(OTHER)[0], name=opensearch.alias(T))  # point the wrong client at it
    with db.tenant(T) as s:
        assert engine.customers(s, "kestrel", 8) == []


def test_a_client_with_no_index_gets_one_on_first_search(s, engine):
    people(s, [("P1", "Ada Stone")])
    assert not engine.exists(T)
    assert [p.id for _, p in engine.customers(s, "ada", 8)] == ["P1"] and engine.exists(T)


def test_when_the_engine_is_down_the_search_box_answers_from_the_database(s, engine):
    people(s, [("P1", "Ada Stone")])
    object.__setattr__(settings, "search_url", "opensearch://127.0.0.1:1")
    search._engine = None
    assert [x["title"] for x in search.search(s, "ada")["groups"]["customer"]] == ["Ada Stone"]
    resolution.resolve(s)  # and a load that cannot reach the engine still completes


def test_a_bad_address_is_explained():
    with pytest.raises(SearchError, match="Unknown search engine"):
        opensearch.connect("elastic://x:9200")
    with pytest.raises(SearchError, match="host:port"):
        OpenSearchIndex("opensearch://")


def test_the_status_view_names_the_engine_and_counts_this_clients_names(filled, engine):
    from app.backbone import status

    got = status.search_engine(T)
    assert got["engine"] == "opensearch" and got["names"] == engine.count(T) > len(NAMES)
    assert status.search_engine(OTHER)["names"] == 0
    object.__setattr__(settings, "search_url", "")
    assert status.search_engine(T) == {"engine": "database", "names": None, "note": "Names are searched in the operational database."}


def test_dropping_one_clients_index_leaves_the_other(db, engine):
    """Offboarding drops every index behind a client's alias; the other client still searches."""
    for tenant, rows in ((T, [("P1", "Ada Stone")]), (OTHER, [("P1", "Ben Stone")])):
        db.onboard(tenant)
        with db.tenant(tenant) as s:
            people(s, rows)
            resolution.resolve(s)
            s.commit()
    assert engine.drop(T) == 1 and not engine.exists(T) and engine.count(T) == 0
    assert engine.drop(T) == 0  # again: nothing left, no error
    with db.tenant(OTHER) as s:
        assert [p.name for _, p in engine.customers(s, "stone", 8)] == ["Ben Stone"]
