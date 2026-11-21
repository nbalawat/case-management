"""The payment network on a real Apache AGE, held to the database's answers.

Runs when TEST_AGE_URL names a disposable Postgres with the AGE extension available, for example
TEST_AGE_URL=age://caseforge@127.0.0.1:5433/caseforge. The graphs of the two test clients are dropped afterwards.
"""
from __future__ import annotations

import os
import random

import pytest

from app.backbone import status
from app.config import settings
from app.entities import age, graph, graphstore
from app.entities.graphstore import GraphError
from support import T
from test_entities import World, cust
from test_graphstore import trio

URL = os.getenv("TEST_AGE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("age://"), reason="set TEST_AGE_URL to a disposable Postgres with Apache AGE to run")
OTHER = "kestrel"


@pytest.fixture
def engine():
    object.__setattr__(settings, "graph_url", URL)
    graphstore._engine = graphstore._engine_for = None
    g = graphstore.current()
    for t in (T, OTHER):
        g.drop(t)
    yield g
    for t in (T, OTHER):
        g.drop(t)
    object.__setattr__(settings, "graph_url", "")
    graphstore._engine = graphstore._engine_for = None


@pytest.fixture
def w(s, engine):
    return World(s)


def labels(route):
    return route and [n["label"] for n in route]


def test_resolution_loads_the_clients_graph(w, engine):
    assert engine.size(T) is None
    shell = trio(w)
    assert engine.size(T) == (62, 3)  # sixty customers, the utility and the shell; only the shell's three relationships
    assert engine.route(T, cust("P1"), cust("P3"), 6) == [cust("P1"), shell, cust("P3")]
    assert status.graph_engine(T) | {"note": ""} == {"engine": "age", "entities": 62, "links": 3, "note": ""}


def test_a_route_is_found_by_the_engine_and_a_common_counterparty_is_no_connection(w, engine):
    trio(w)
    assert labels(graph.path(w.s, "P1", "P3")) == ["Person Number1", "Sable Ridge Imports", "Person Number3"]
    assert graph.path(w.s, "P1", "P9") is None and graph.path(w.s, "P8", "P9") is None


def test_a_long_chain_is_found_and_a_longer_one_is_out_of_reach(w, engine):
    for i in range(6):
        w.customer(f"P{i}", f"Person Number{i}", occupation=f"job{i}")
    for i in range(5):
        w.pay(f"P{i}", f"Link Firm {chr(65 + i)}{chr(75 + i)}", 10)
        w.pay(f"P{i + 1}", f"Link Firm {chr(65 + i)}{chr(75 + i)}", 10)
    w.resolve()
    assert len(graph.path(w.s, "P0", "P3")) == 7 and graph.path(w.s, "P0", "P5") is None and len(graph.path(w.s, "P0", "P5", max_hops=10)) == 11


def test_on_a_tangled_network_the_engine_and_the_database_agree_on_every_distance(w, engine):
    rng = random.Random(7)
    firms = [f"{a} {b}" for a in ("Amber", "Birch", "Cobalt", "Dune", "Ember", "Flint", "Garnet", "Heron") for b in ("Quay", "Ridge", "Vale", "Works", "Yard")]
    for i in range(120):  # few enough customers per firm that none counts as common
        w.customer(f"P{i}", f"Person Number{i}", occupation=f"job{i}")
        for firm in rng.sample(firms, rng.choice((1, 2, 2))):
            w.pay(f"P{i}", firm, 500)
    w.resolve()
    pairs = [(f"P{a}", f"P{b}") for a, b in (rng.sample(range(120), 2) for _ in range(60))]
    found = 0
    for a, b in pairs:
        ours = graph._route_in_database(w.s, cust(a), cust(b), 6)
        theirs = engine.route(T, cust(a), cust(b), 6)
        assert (ours is None) == (theirs is None), (a, b)
        if ours:
            found += 1
            assert len(theirs) == len(ours) and theirs[0] == cust(a) and theirs[-1] == cust(b), (a, b)
            real = {frozenset(p) for p in graphstore.links(w.s)[1]}
            assert all(frozenset(step) in real for step in zip(theirs, theirs[1:])), (a, b)  # every step is a relationship that exists
    assert 5 < found < 60  # the sample has both kinds


def test_a_rebuild_replaces_the_graph(w, engine):
    trio(w)
    assert engine.size(T) == (62, 3)
    w.pay("P9", "Sable Ridge Imports", 50, country="IR")
    w.resolve()
    assert engine.size(T) == (62, 4) and labels(graph.path(w.s, "P1", "P9")) == ["Person Number1", "Sable Ridge Imports", "Person Number9"]


def test_one_clients_graph_holds_nothing_of_anothers(db, engine):
    db.onboard(T), db.onboard(OTHER)
    with db.tenant(T) as s:
        trio(World(s))
    with db.tenant(OTHER) as s:
        w = World(s)
        for pid in ("P1", "P3"):
            w.customer(pid, f"Kestrel {pid}")
        w.resolve()
        assert engine.size(OTHER) == (2, 0) and engine.size(T) == (62, 3)
        assert engine.route(OTHER, cust("P1"), cust("P3"), 6) is None and graph.path(s, "P1", "P3") is None
    assert engine.route(T, cust("P1"), cust("P3"), 6) is not None


def test_before_a_clients_graph_is_built_routes_come_from_the_database(w, engine):
    trio(w)
    engine.drop(T)
    with pytest.raises(GraphError, match="not been built"):
        engine.route(T, cust("P1"), cust("P3"), 6)
    assert labels(graph.path(w.s, "P1", "P3")) == ["Person Number1", "Sable Ridge Imports", "Person Number3"]
    assert "next entity resolution" in status.graph_engine(T)["note"]


def test_a_search_that_runs_out_of_time_is_answered_from_the_database(w, engine, caplog):
    trio(w)
    engine.timeout_ms = 1  # one millisecond: nothing finishes
    rng = random.Random(3)
    for i in range(60, 200):
        w.customer(f"P{i}", f"Person Number{i}", occupation=f"job{i}")
    firms = [f"Dense {chr(65 + a)}{chr(65 + b)} Holdings" for a in range(6) for b in range(6)]
    for i in range(60, 200):
        for firm in rng.sample(firms, 4):
            w.pay(f"P{i}", firm, 10)
    w.resolve()
    want = graph._route_in_database(w.s, cust("P61"), cust("P1"), 6)
    with pytest.raises(GraphError, match="QueryCanceled"):
        engine.route(T, cust("P61"), cust("P1"), 6)
    with caplog.at_level("WARNING", logger="caseforge.graph"):
        got = graph.path(w.s, "P61", "P1")
    assert (got is None) == (want is None) and "finding the route in the database" in caplog.text


def test_an_engine_that_is_unreachable_costs_nothing_but_speed(w):
    trio(w)
    object.__setattr__(settings, "graph_url", "age://nobody@127.0.0.1:9/none")
    graphstore._engine = graphstore._engine_for = None
    try:
        assert labels(graph.path(w.s, "P1", "P3")) == ["Person Number1", "Sable Ridge Imports", "Person Number3"]
        assert w.resolve()["customers"] == 60
        assert status.graph_engine(T)["engine"] == "database"
    finally:
        object.__setattr__(settings, "graph_url", "")
        graphstore._engine = graphstore._engine_for = None


def test_ids_with_quotes_and_dollars_are_data_not_cypher(engine, s):
    name = age.graph_name(T)
    nasty = ["ent-a'b", 'ent-"c"', "ent-$$ RETURN 1 $$", "ent-{id: 1}", "ent-é"]
    with engine._cursor() as cur:
        cur.execute("SELECT create_graph(%s)", (name,))
        cur.execute("SELECT create_vlabel(%s, 'Entity')", (name,))
        cur.execute("SELECT create_elabel(%s, 'LINK')", (name,))
        import json

        cur.execute(age._NODES.format(g=name), (json.dumps({"rows": nasty}),), prepare=True)
        cur.execute(age._LINKS.format(g=name), (json.dumps({"rows": [[nasty[i], nasty[i + 1]] for i in range(4)]}),), prepare=True)
    assert engine.size(T) == (5, 4)
    assert engine.route(T, nasty[0], nasty[4], 6) == nasty


# ---- the picture around a customer, two steps out

def tangle(w, n=120, seed=7):
    rng = random.Random(seed)
    firms = [f"{a} {b}" for a in ("Amber", "Birch", "Cobalt", "Dune", "Ember", "Flint", "Garnet", "Heron") for b in ("Quay", "Ridge", "Vale", "Works", "Yard")]
    for i in range(n):
        w.customer(f"P{i}", f"Person Number{i}", occupation=f"job{i}")
        w.pay(f"P{i}", "Citywide Utilities", 80)  # common: shown beside each customer, never a connection
        for firm in rng.sample(firms, rng.choice((1, 2, 2))):
            w.pay(f"P{i}", firm, rng.choice((500, 900, 4000)))
    w.resolve()


def test_on_a_tangled_network_the_picture_drawn_through_the_engine_is_the_databases_for_every_customer(w, engine):
    tangle(w)
    drawn = {}
    for i in range(120):
        drawn[i] = (graph.related_customers(w.s, f"P{i}"), graph.neighbourhood(w.s, f"P{i}"))
    object.__setattr__(settings, "graph_url", "")
    for i in range(120):
        assert (graph.related_customers(w.s, f"P{i}"), graph.neighbourhood(w.s, f"P{i}")) == drawn[i], i
    assert sum(bool(r) for r, _ in drawn.values()) > 100 and any(p["hidden"] for _, p in drawn.values())


def test_two_steps_out_is_what_the_relationships_say_and_never_passes_through_a_common_counterparty(w, engine):
    from test_graphstore import two_steps

    tangle(w, n=80)
    utility = w.entity("Citywide Utilities").id
    for i in (0, 7, 33, 79):
        got = engine.shared(T, cust(f"P{i}"))
        assert got == two_steps(w.s, cust(f"P{i}")) and got and all(utility not in pair and cust(f"P{i}") != pair[1] for pair in got)
    assert engine.shared(T, "E-nobody") == []


def test_one_clients_picture_never_reaches_into_anothers_graph(db, engine):
    for tenant, people in ((T, ("P1", "P2")), (OTHER, ("P1", "P2", "P3"))):
        db.onboard(tenant)
        with db.tenant(tenant) as s:
            w = World(s)
            for i in range(60):
                w.customer(f"Q{i}", f"Other Person{i}", occupation=f"job{i}")
                w.pay(f"Q{i}", "Citywide Utilities", 80)
            for pid in people:
                w.customer(pid, f"Person {pid} of {tenant}")
                w.pay(pid, "Sable Ridge Imports", 9000)
            w.resolve()
            s.commit()
    with db.tenant(T) as s:
        assert [r["party_id"] for r in graph.related_customers(s, "P1")] == ["P2"]
    with db.tenant(OTHER) as s:
        assert [r["party_id"] for r in graph.related_customers(s, "P1")] == ["P2", "P3"]


def test_before_a_clients_graph_is_built_the_picture_comes_from_the_database(w, engine, caplog):
    trio(w)
    engine.drop(T)
    assert [r["party_id"] for r in graph.related_customers(w.s, "P1")] == ["P2", "P3"]
    assert "reading the network from the database" in caplog.text
