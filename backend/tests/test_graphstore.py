"""The graph seam: what is handed to an engine, and what happens when it answers, fails or is behind.

The engine here is a stand-in. tests/test_age.py runs the same questions on a real Apache AGE.
"""
from __future__ import annotations

import pytest

from app.backbone import status
from app.config import settings
from app.entities import age, graph, graphstore
from app.entities.graphstore import GraphError
from support import T
from test_entities import World, cust


class Fake:
    """An engine that answers what it is told to, and remembers what it was asked."""

    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.asked, self.rebuilt = answer, fail, [], 0

    def route(self, tenant_id, start, goal, max_hops):
        self.asked.append((tenant_id, start, goal, max_hops))
        if self.fail:
            raise GraphError("down")
        return self.answer

    def rebuild(self, s):
        self.rebuilt += 1
        if self.fail:
            raise GraphError("down")
        return {}

    def size(self, tenant_id):
        if self.fail:
            raise GraphError("down")
        return self.answer


@pytest.fixture
def w(s):
    return World(s)


@pytest.fixture
def engine():
    def use(fake):
        object.__setattr__(settings, "graph_url", "age://stand-in")
        graphstore._engine, graphstore._engine_for = fake, "age://stand-in"
        return fake

    yield use
    object.__setattr__(settings, "graph_url", "")
    graphstore._engine = graphstore._engine_for = None


def trio(w, resolve=True):
    """Three customers paying one uncommon firm, sixty paying a utility."""
    for i in range(60):
        w.customer(f"P{i}", f"Person Number{i}", occupation=f"job{i}")
        w.pay(f"P{i}", "Citywide Utilities", 80)
    for pid in ("P1", "P2", "P3"):
        w.pay(pid, "Sable Ridge Imports", 9000, country="IR")
    if resolve:
        w.resolve()
    return w.entity("Sable Ridge Imports").id if resolve else None


def test_with_no_engine_configured_none_is_used():
    assert settings.graph_url == "" and graphstore.current() is None


def test_an_engine_is_handed_every_entity_and_only_the_relationships_that_may_connect_people(w):
    shell = trio(w)
    nodes, pairs = graphstore.links(w.s)
    utility = w.entity("Citywide Utilities").id
    assert pairs == sorted((cust(p), shell) for p in ("P1", "P2", "P3"))
    assert utility in nodes and len([n for n in nodes if n.startswith("ent-P")]) == 60 and not any(utility in p for p in pairs)


def test_the_engines_route_is_used_and_its_entities_are_read_through_the_session(w, engine):
    shell = trio(w)
    fake = engine(Fake([cust("P1"), shell, cust("P3")]))
    assert [n["label"] for n in graph.path(w.s, "P1", "P3")] == ["Person Number1", "Sable Ridge Imports", "Person Number3"]
    assert fake.asked == [(T, cust("P1"), cust("P3"), 6)]


def test_when_the_engine_says_there_is_no_route_there_is_none(w, engine):
    trio(w)
    fake = engine(Fake(None))
    assert graph.path(w.s, "P1", "P3") is None and len(fake.asked) == 1  # believed: it is not second-guessed in the database


def test_an_engine_that_fails_costs_nothing_but_speed(w, engine):
    trio(w)
    fake = engine(Fake(fail=True))
    assert [n["label"] for n in graph.path(w.s, "P1", "P3")] == ["Person Number1", "Sable Ridge Imports", "Person Number3"]
    assert graph.path(w.s, "P1", "P9") is None and len(fake.asked) == 2


def test_a_route_through_an_entity_that_no_longer_exists_is_found_again_in_the_database(w, engine):
    trio(w)
    engine(Fake([cust("P1"), "ent-gone-since-the-last-rebuild", cust("P3")]))
    assert [n["label"] for n in graph.path(w.s, "P1", "P3")] == ["Person Number1", "Sable Ridge Imports", "Person Number3"]


def test_a_customer_to_themselves_never_troubles_the_engine(w, engine):
    trio(w)
    fake = engine(Fake(None))
    assert [n["label"] for n in graph.path(w.s, "P1", "P1")] == ["Person Number1"] and fake.asked == []


def test_resolution_rebuilds_the_engine_and_survives_its_failure(w, engine):
    fake = engine(Fake())
    trio(w)
    assert fake.rebuilt == 1
    bad = engine(Fake(fail=True))
    assert w.resolve()["customers"] == 60 and bad.rebuilt == 1


@pytest.mark.parametrize("tenant, name", [("meridian", "cf_meridian"), ("acme-bank", "cf_acme_bank"), ("a-b-c", "cf_a_b_c")])
def test_each_client_has_its_own_graph_name(tenant, name):
    assert age.graph_name(tenant) == name


@pytest.mark.parametrize("tenant", ["", "ab", "Acme", "acme_bank", "acme bank", 'acme"; drop schema public cascade; --', "acme\n", "a" * 40, None])
def test_a_client_id_that_could_change_a_statement_is_refused(tenant):
    with pytest.raises(GraphError):
        age.graph_name(tenant)


def test_a_path_as_age_prints_it_is_read_into_entity_ids():
    printed = ('[{"id": 844424930131979, "label": "Entity", "properties": {"id": "ent-P1"}}::vertex, '
               '{"id": 1125899906842635, "label": "LINK", "end_id": 844424930131980, "start_id": 844424930131979, "properties": {}}::edge, '
               '{"id": 844424930131980, "label": "Entity", "properties": {"id": "ent-x::vertex"}}::vertex]::path')
    assert age._ids(printed.replace("ent-x::vertex", "ent-x")) == ["ent-P1", "ent-x"]


@pytest.mark.parametrize("url", ["", "postgresql://host/db", "neo4j://host", "age"])
def test_only_an_age_address_is_accepted(url):
    with pytest.raises(GraphError, match="GRAPH_URL"):
        age.connect(url)


def test_the_platform_view_says_where_routes_are_found(s, engine):
    assert status.graph_engine(T) == {"engine": "database", "entities": None, "links": None, "note": "Routes between customers are found in the operational database."}
    engine(Fake((63, 3)))
    assert status.graph_engine(T) | {"note": ""} == {"engine": "age", "entities": 63, "links": 3, "note": ""}
    engine(Fake(None))
    v = status.graph_engine(T)
    assert v["engine"] == "age" and v["entities"] is None and "next entity resolution" in v["note"]
    engine(Fake(fail=True))
    v = status.graph_engine(T)
    assert v["engine"] == "database" and "did not answer" in v["note"]
    assert "stand-in" not in str(v)
    assert status.view(s)["graph"]["engine"] == "database"
