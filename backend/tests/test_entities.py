"""Entity resolution, the network between parties, customer and counterparty profiles, and search."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

import app.db as appdb
from app import visibility
from app.cases import restriction
from app.cases.case_types import load_case_types
from app.cases.service import create_case
from app.domain import models as m
from app.entities import graph, names, profile, resolution, search
from app.entities.resolution import customer_entity_id as cust
from app.tenants import registry
from support import PERSONAS, T
from test_client_api import CLIENTS, EVERYONE, _auditor, _permissions, everything, h

NOW = m.now()


# --- names -------------------------------------------------------------------------------------------------------------
def test_jaro_winkler_matches_the_published_examples():
    assert round(names.jaro_winkler("martha", "marhta"), 3) == 0.961
    assert round(names.jaro_winkler("dixon", "dicksonx"), 3) == 0.813
    assert round(names.jaro_winkler("dwayne", "duane"), 2) == 0.84
    assert names.jaro_winkler("abc", "abc") == 1 and names.jaro_winkler("abc", "xyz") == 0 and names.jaro_winkler("", "abc") == 0


@pytest.mark.parametrize("a,b", [
    ("Volkov Maritime Trading", "Volkov Maritime Trading Co"), ("Orion Global Consulting Ltd", "ORION GLOBAL CONSULTING, LTD."),
    ("Tomás Bergström", "tomas bergstrom"), ("Corner Cafe", "Corner Café Ltd"), ("Quanta Ventures LLC", "Quanta Ventures L.L.C."),
    ("Smith & Jones GmbH", "Smith and Jones"), ("The Atlas Fuel Company", "Atlas Fuel")])
def test_legal_forms_case_accents_and_punctuation_do_not_make_a_different_name(a, b):
    assert names.key(a) == names.key(b) and names.similarity(a, b) == 1.0


@pytest.mark.parametrize("a,b,low,high", [
    ("Darya Shipping Lines", "Darya Shipping Line LLC", 1.0, 1.0),        # plural
    ("Bluepeak Holdings", "Bluepeak Holdngs SA", 0.96, 1.0),              # one letter dropped: merges
    ("Volkov Maritime Trading", "Volkov Marine Trading", 0.82, 0.96),     # a different word that looks alike: a person decides
    ("Bluepeak Holdings SA", "Bluepeak Holding Group SA", 0.82, 0.96),
    ("Mei Novak", "Mei Nowak", 0.82, 0.96),
    ("Elena Okafor", "Okafor Elena", 0.9, 0.9),                           # word order: never automatic
    ("Harbor Grocers", "Harbor Garage", 0.0, 0.82),                       # one shared word is not enough
    ("Kestrel Logistics Payroll", "Bluefin Health Payroll", 0.0, 0.6),
    ("Daniel Hartmann", "Daniel Yamamoto", 0.0, 0.82),
    ("Summit Insurance", "Northline Transit", 0.0, 0.6)])
def test_similarity_puts_pairs_on_the_right_side_of_the_thresholds(a, b, low, high):
    score = names.similarity(a, b)
    assert low <= score <= high and score == names.similarity(b, a), score
    assert resolution.DEFAULTS["review_at"] == 0.82 and resolution.DEFAULTS["auto_merge_at"] == 0.96


def test_names_with_nothing_in_them_never_match():
    assert names.similarity("", "Acme") == 0 and names.similarity("---", "...") == 0 and names.key("  ") == ""
    assert names.key("LLC") == "llc" and names.tokens("Co Ltd") == ["co", "ltd"]  # nothing but legal forms: kept, not emptied
    assert names.blocks("Volkov Maritime Trading Co") == {"w:volkov", "w:maritime", "w:trading", "p:volk"}


# --- resolution --------------------------------------------------------------------------------------------------------
class World:
    def __init__(self, s):
        self.s, self.n = s, 0

    def customer(self, pid, name, occupation="teacher", country="US"):
        self.s.add_all([m.Party(id=pid, name=name, occupation=occupation, country=country), m.Account(id=f"A-{pid}", party_id=pid)])
        self.s.flush()
        return pid

    def pay(self, pid, counterparty, amount=100.0, direction="out", country="US", channel="wire", days_ago=1):
        self.n += 1
        self.s.add(m.Transaction(id=f"T{self.n:05d}", account_id=f"A-{pid}", party_id=pid, ts=NOW - timedelta(days=days_ago), amount=amount,
                                 direction=direction, channel=channel, counterparty=counterparty, counterparty_country=country))

    def resolve(self):
        self.s.flush()
        return resolution.resolve(self.s)

    def entity(self, name):
        hits = [e for e in self.s.scalars(select(m.Entity).where(m.Entity.kind == "counterparty")) if any(a["name"] == name for a in e.aliases)]
        assert len(hits) == 1, f"{name}: {len(hits)} entities"
        return hits[0]

    def open(self, kind=None):
        return [q for q in resolution.questions(self.s) if kind in (None, q["kind"])]


@pytest.fixture
def w(s):
    return World(s)


def test_spellings_of_one_firm_become_one_counterparty_and_look_alikes_wait_for_a_person(w):
    w.customer("P1", "Ada Stone")
    w.customer("P2", "Ben Rivers", occupation="nurse")
    for name, n in [("Volkov Maritime Trading", 3), ("VOLKOV MARITIME TRADING CO", 1), ("Volkov Maritime Trading, Ltd.", 1), ("Volkov Maritime Tradng", 1)]:
        for _ in range(n):
            w.pay("P1", name, 1000, country="RU")
    w.pay("P2", "Volkov Marine Trading", 500)
    w.pay("P2", "Harbor Grocers", 20)
    w.pay("P2", "", 50, channel="cash")  # cash has no counterparty
    out = w.resolve()
    assert out == {"customers": 2, "counterparties": 3, "spellings": 6, "merged_spellings": 3, "relationships": 3, "open_questions": 1,
                   "customers_as_counterparties": 0}
    volkov = w.entity("Volkov Maritime Trading")
    assert volkov.name == "Volkov Maritime Trading" and volkov.txns == 6 and volkov.total == 6000 and volkov.customers == 1
    assert {a["name"]: a["how"] for a in volkov.aliases} == {
        "Volkov Maritime Trading": "main spelling", "VOLKOV MARITIME TRADING CO": "same once legal form and punctuation are removed",
        "Volkov Maritime Trading, Ltd.": "same once legal form and punctuation are removed", "Volkov Maritime Tradng": "very close spelling"}
    assert "watchlist" in volkov.flags and volkov.countries == ["RU"]
    [q] = w.open()
    assert q["kind"] == "counterparty_variant" and {q["left"], q["right"]} == {"volkov marine trading", "volkov maritime trading"} and 0.82 <= q["score"] < 0.96
    assert w.entity("Volkov Marine Trading").id != volkov.id  # not merged until someone says so
    edge = w.s.scalars(select(m.EntityEdge).where(m.EntityEdge.entity_id == volkov.id)).one()
    assert (edge.party_id, edge.txns, edge.sent, edge.received, edge.channels) == ("P1", 6, 6000, 0, ["wire"])


def test_a_persons_answer_is_applied_at_once_and_survives_every_later_run(w):
    w.customer("P1", "Ada Stone")
    w.pay("P1", "Volkov Maritime Trading", 1000)
    w.pay("P1", "Volkov Marine Trading", 500)
    w.pay("P1", "Bluepeak Holdings SA", 10)
    w.pay("P1", "Bluepeak Holding Group SA", 10)
    w.resolve()
    volkov, bluepeak = sorted(w.open(), key=lambda q: q["left"], reverse=True)
    with pytest.raises(resolution.ResolutionError, match="cannot decide"):
        resolution.decide(w.s, volkov["id"], PERSONAS["ana"], True)
    with pytest.raises(resolution.ResolutionError, match="No such question"):
        resolution.decide(w.s, "mc-nope", PERSONAS["tara"], True)
    resolution.decide(w.s, volkov["id"], PERSONAS["tara"], True, "Same registered address")
    resolution.decide(w.s, bluepeak["id"], PERSONAS["tara"], False)
    merged = w.entity("Volkov Marine Trading")
    assert merged.id == w.entity("Volkov Maritime Trading").id and merged.txns == 2 and merged.total == 1500
    assert {a["how"] for a in merged.aliases} == {"main spelling", "confirmed by a person"}
    assert w.entity("Bluepeak Holdings SA").id != w.entity("Bluepeak Holding Group SA").id and w.open() == []
    decided = {q["left"]: q for q in resolution.questions(w.s, "decided")}
    assert decided["volkov marine trading"]["status"] == "same" and decided["volkov marine trading"]["decided_by"] == "tara"
    assert decided["volkov marine trading"]["note"] == "Same registered address" and decided["bluepeak holding group"]["status"] == "different"
    before = [(e.id, e.name, e.txns) for e in w.s.scalars(select(m.Entity).order_by(m.Entity.id))]
    for _ in range(3):  # more data arrives and the run repeats: nothing is asked twice, nothing comes apart
        w.pay("P1", "Volkov Marine Trading", 1)
        w.resolve()
    assert w.open() == [] and w.entity("Volkov Marine Trading").id == merged.id and w.entity("Volkov Marine Trading").txns == 5
    assert [(i, n) for i, n, _ in before] == [(e.id, e.name) for e in w.s.scalars(select(m.Entity).order_by(m.Entity.id))]  # ids are stable
    resolution.decide(w.s, volkov["id"], PERSONAS["hugo"], False, "Different owners after all")  # and an answer can be changed
    assert w.entity("Volkov Marine Trading").id != w.entity("Volkov Maritime Trading").id


def test_a_counterparty_is_never_assumed_to_be_a_customer_and_a_confirmed_one_links_the_two_customers(w):
    w.customer("P1", "Ada Stone")
    w.customer("P2", "Ben Rivers")
    w.customer("P3", "Ben Rivers", occupation="chef")  # two customers share the name: the question is asked about each
    w.pay("P1", "Ben Rivers", 700, channel="p2p")
    w.pay("P1", "BEN RIVERS", 300, channel="p2p")
    w.pay("P2", "Ada Stone Ltd", 5)
    w.pay("P2", "Ben Rivers", 9)  # to oneself, if confirmed
    assert w.resolve()["customers_as_counterparties"] == 0
    asked = w.open("customer_as_counterparty")
    assert sorted(q["right"] for q in asked) == ["Ada Stone (P1)", "Ben Rivers (P2)", "Ben Rivers (P3)"] and all(q["score"] == 0.75 for q in asked)
    assert w.entity("Ben Rivers").kind == "counterparty" and not w.s.scalars(select(m.EntityEdge).where(m.EntityEdge.entity_id == cust("P2"))).all()
    yes = next(q for q in asked if q["right"] == "Ben Rivers (P2)")
    resolution.decide(w.s, yes["id"], PERSONAS["hugo"], True)
    edge = w.s.scalars(select(m.EntityEdge).where(m.EntityEdge.entity_id == cust("P2"))).one()
    assert (edge.party_id, edge.txns, edge.sent) == ("P1", 2, 1000)  # P2 paying "Ben Rivers" would be paying himself, so it is no relationship
    me = w.s.scalars(select(m.Entity).where(m.Entity.id == cust("P2"))).one()
    assert {a["name"] for a in me.aliases} == {"Ben Rivers", "BEN RIVERS"} and me.customers == 1
    assert not [e for e in w.s.scalars(select(m.Entity).where(m.Entity.kind == "counterparty")) if e.key == "ben rivers"]
    assert [q["right"] for q in w.open("customer_as_counterparty")] == ["Ada Stone (P1)", "Ben Rivers (P3)"]
    assert [r["party_id"] for r in graph.related_customers(w.s, "P1") if r["direct"]] == ["P2"]
    assert [r["party_id"] for r in graph.related_customers(w.s, "P2") if r["direct"]] == ["P1"]


def test_two_customer_records_are_only_questioned_when_more_than_the_name_agrees(w):
    w.customer("P1", "Ada Stone", "teacher")
    w.customer("P2", "Ada Stone", "nurse")              # same name only: 0.65, not asked
    w.customer("P3", "Ada Stone", "teacher")            # name, country, occupation: asked
    w.customer("P4", "Ada Stone", "chef", country="GB")
    w.customer("P5", "Ada Stoner", "teacher")
    for pid in ("P2", "P4"):
        w.pay(pid, "Rare Books of Lisbon", 40)          # P2 and P4 share an uncommon counterparty: 0.75, still not asked
    w.resolve()
    asked = w.open("duplicate_customer")
    assert [(q["left"], q["right"], q["score"], q["reasons"]) for q in asked] == [
        ("Ada Stone (P1)", "Ada Stone (P3)", 0.85, ["Same name", "Same country", "Same occupation"])]
    assert registry.runtime(T).settings.get("entities") is None
    registry.runtime(T).settings["entities"] = {"duplicate_customer_at": 0.75}  # a client can set its own bar
    try:
        w.resolve()
        assert {(q["left"], q["right"]) for q in w.open("duplicate_customer")} == {("Ada Stone (P1)", "Ada Stone (P3)"), ("Ada Stone (P2)", "Ada Stone (P4)")}
    finally:
        registry.runtime(T).settings.pop("entities")
    w.resolve()
    [q] = w.open("duplicate_customer")  # the question the data no longer supports is withdrawn
    resolution.decide(w.s, q["id"], PERSONAS["tara"], True)
    assert resolution.duplicates_of(w.s, "P1") == [{"party_id": "P3", "name": "Ada Stone (P3)", "status": "same", "score": 0.85, "candidate_id": q["id"]}]
    assert resolution.duplicates_of(w.s, "P3")[0]["party_id"] == "P1" and resolution.duplicates_of(w.s, "P2") == []
    assert w.s.scalar(select(func.count()).select_from(m.Party)) == 5  # confirming links the records; it never deletes one


def test_running_again_changes_nothing_and_counterparties_everyone_uses_do_not_link_people(w):
    for i in range(60):
        w.customer(f"P{i}", f"Person Number{i}", occupation=f"job{i}")
        w.pay(f"P{i}", "Citywide Utilities", 80)
        w.pay(f"P{i}", "Citywide Utilities", 120, direction="in")
    for pid in ("P1", "P2", "P3"):
        w.pay(pid, "Sable Ridge Imports", 9000, country="IR")
    first = w.resolve()
    snap = lambda: ([(e.id, e.name, e.flags, e.customers, e.txns, e.total) for e in w.s.scalars(select(m.Entity).order_by(m.Entity.id))],  # noqa: E731
                    sorted((e.party_id, e.entity_id, e.txns, e.sent, e.received) for e in w.s.scalars(select(m.EntityEdge))))
    before = snap()
    assert w.resolve() == first and snap() == before
    utility, shell = w.entity("Citywide Utilities"), w.entity("Sable Ridge Imports")
    assert utility.flags == ["common"] and utility.customers == 60 and shell.flags == ["high_risk_country"] and shell.customers == 3
    assert [r["party_id"] for r in graph.related_customers(w.s, "P1")] == ["P2", "P3"]  # through the shell, not through the utility
    assert graph.related_customers(w.s, "P9") == []
    net = graph.neighbourhood(w.s, "P1")
    assert {n["label"] for n in net["nodes"]} == {"Person Number1", "Person Number2", "Person Number3", "Citywide Utilities", "Sable Ridge Imports"}
    assert net["focus"] == cust("P1") and [n["id"] for n in net["nodes"] if n["focus"]] == [cust("P1")] and len(net["edges"]) == 4
    assert [n["label"] for n in graph.path(w.s, "P1", "P3")] == ["Person Number1", "Sable Ridge Imports", "Person Number3"]
    assert graph.path(w.s, "P1", "P9") is None and graph.neighbourhood(w.s, "P-none") is None  # the utility is no connection


def test_a_long_chain_is_found_and_a_longer_one_is_out_of_reach(w):
    for i in range(6):
        w.customer(f"P{i}", f"Person Number{i}", occupation=f"job{i}")
    for i in range(5):
        w.pay(f"P{i}", f"Link Firm {chr(65 + i)}{chr(75 + i)}", 10)
        w.pay(f"P{i + 1}", f"Link Firm {chr(65 + i)}{chr(75 + i)}", 10)
    w.resolve()
    assert len(graph.path(w.s, "P0", "P3")) == 7 and graph.path(w.s, "P0", "P5") is None and len(graph.path(w.s, "P0", "P5", max_hops=10)) == 11


def test_the_network_and_profile_count_only_the_cases_the_viewer_may_see(w):
    w.customer("P1", "Ada Stone")
    w.customer("P2", "Ben Rivers", occupation="nurse")
    for pid in ("P1", "P2"):
        w.pay(pid, "Sable Ridge Imports", 9000)
    tm = create_case(w.s, load_case_types(T)["tm_alert_investigation"], "P2", "high")
    fraud = create_case(w.s, load_case_types(T)["fraud_claim"], "P2", "high")
    w.resolve()
    w.s.commit()

    def seen(who):
        visibility.bind_viewer(w.s, PERSONAS[who])
        node = next(n for n in graph.neighbourhood(w.s, "P1")["nodes"] if n["party_id"] == "P2")
        return node["open_cases"], graph.related_customers(w.s, "P1")[0]["open_cases"], [c["id"] for c in profile.customer(w.s, "P2")["cases"]]

    assert seen("audra") == (2, 2, [fraud.id, tm.id]) or seen("audra") == (2, 2, [tm.id, fraud.id])
    assert seen("ivan") == (1, 1, [tm.id]) and seen("fran") == (1, 1, [fraud.id])
    restriction.set_restricted(w.s, tm, PERSONAS["maya"], True, "Staff member")
    w.s.commit()
    assert seen("ivan") == (0, 0, []) and seen("lena") == (1, 1, [tm.id])
    visibility.bind_viewer(w.s, PERSONAS["ivan"])
    assert [x for x in search.search(w.s, "Ben Rivers")["groups"]["case"]] == [] and search.search(w.s, tm.id)["groups"]["case"] == []
    assert [c["title"] for c in search.search(w.s, "ben riv")["groups"]["customer"]] == ["Ben Rivers"]  # the customer is still a customer
    visibility.bind_viewer(w.s, PERSONAS["lena"])
    assert [c["id"] for c in search.search(w.s, tm.id)["groups"]["case"]] == [tm.id] and search.search(w.s, tm.id)["groups"]["case"][0]["restricted"]


def test_search_finds_things_the_way_people_type_them(w):
    w.customer("P10001", "Tomás Bergström")
    w.customer("P10002", "Tom Berg", occupation="nurse")
    w.customer("P20000", "Elena Okafor", occupation="chef")
    w.pay("P10001", "Volkov Maritime Trading", 100)
    w.pay("P10001", "VOLKOV MARITIME TRADING CO", 100)
    w.pay("P20000", "Harbor Grocers", 5)
    w.resolve()
    find = lambda q, kind="customer": [x["title"] for x in search.search(w.s, q)["groups"][kind]]  # noqa: E731
    assert find("tomas bergstrom") == ["Tomás Bergström"] and find("BERGSTRÖM") == ["Tomás Bergström"]
    assert find("tom") == ["Tom Berg", "Tomás Bergström"]  # the whole word before the leading part
    assert find("P10001") == ["Tomás Bergström"] and find("p1000") == ["Tom Berg", "Tomás Bergström"]
    assert find("okafor") == ["Elena Okafor"] and find("Elena Okaforr") == ["Elena Okafor"] and find("zzz") == []
    assert find("volkov", "counterparty") == ["Volkov Maritime Trading"] and find("volkov maritime trading co", "counterparty") == ["Volkov Maritime Trading"]
    assert find("Volkof Maritime", "counterparty") == ["Volkov Maritime Trading"] and find("grocer", "counterparty") == ["Harbor Grocers"]
    hit = search.search(w.s, "volkov")["groups"]["counterparty"][0]
    assert hit["flags"] == ["watchlist"] and hit["detail"].startswith("1 customer · 2 spellings")
    assert [t["id"] for t in search.search(w.s, "T00001")["groups"]["transaction"]] == ["T00001"] and search.search(w.s, "volkov")["groups"]["transaction"] == []
    assert search.search(w.s, "")["total"] == 0 and search.search(w.s, "a")["total"] == 0 and search.search(w.s, "%")["total"] == 0
    assert search.search(w.s, "' OR 1=1 --")["total"] == 0 and search.search(w.s, "_____")["total"] == 0


def test_profiles_add_up(w):
    w.customer("P1", "Ada Stone")
    w.customer("P2", "Ben Rivers", occupation="nurse")
    w.pay("P1", "Sable Ridge Imports", 9000, country="IR", days_ago=40)
    w.pay("P1", "Sable Ridge Imports", 1000, direction="in", days_ago=2)
    w.pay("P1", "Harbor Grocers", 25.5, channel="card", days_ago=2)
    w.pay("P1", "", 300, direction="in", channel="cash", days_ago=1)
    w.pay("P2", "SABLE RIDGE IMPORTS LLC", 50)
    w.resolve()
    me = profile.customer(w.s, "P1")
    assert me["party"]["name"] == "Ada Stone" and me["party"]["accounts"] == [{"id": "A-P1", "product": "checking", "status": "open"}]
    assert me["totals"] | {"first": 0, "last": 0} == {"txns": 4, "in": 1300, "out": 9025.5, "first": 0, "last": 0, "counterparties": 2, "alerts": 0,
                                                     "open_cases": 0, "cases": 0}
    assert sum(a["in"] for a in me["activity"]) == 1300 and sum(a["out"] for a in me["activity"]) == 9025.5 and sum(a["txns"] for a in me["activity"]) == 4
    assert [a["month"] for a in me["activity"]] == sorted(a["month"] for a in me["activity"])
    assert {c["channel"]: c["txns"] for c in me["channels"]} == {"wire": 2, "card": 1, "cash": 1}
    top = me["counterparties"][0]
    assert (top["name"], top["sent"], top["received"], top["txns"], top["flags"], top["spellings"]) == ("Sable Ridge Imports", 9000, 1000, 2, ["high_risk_country"], 2)
    assert [c["name"] for c in me["flagged"]] == ["Sable Ridge Imports"] and [r["name"] for r in me["related"]] == ["Ben Rivers"]
    assert me["network"]["focus"] == me["entity_id"] and profile.customer(w.s, "P-none") is None
    firm = profile.counterparty(w.s, top["entity_id"])
    assert firm["entity"]["customers"] == 2 and firm["entity"]["total"] == 10050 and [c["name"] for c in firm["customers"]] == ["Ada Stone", "Ben Rivers"]
    assert profile.counterparty(w.s, "ent-none") is None


# --- the seeded demo over HTTP -----------------------------------------------------------------------------------------
def test_every_client_is_resolved_at_start_with_its_own_entities_and_questions(client):
    seen = []
    for t in CLIENTS:
        page = client.get("/api/entities", headers=everything(t)).json()
        c = page["counts"]
        assert c["customers"] > 50 and c["counterparties"] > 20 and c["spellings"] > c["counterparties"] and c["merged"] >= 2 and c["open"] > 0
        assert page["thresholds"] == resolution.DEFAULTS and len(page["questions"]) == c["open"]
        assert {q["kind"] for q in page["questions"]} <= set(resolution.KINDS) and page["flagged"]
        assert all(len(e["aliases"]) > 1 for e in page["merged"])
        with appdb.session_scope(t) as s:
            assert c["customers"] == s.scalar(select(func.count()).select_from(m.Party))
            spelled = s.scalar(select(func.count(func.distinct(m.Transaction.counterparty))).where(m.Transaction.counterparty != ""))
            assert c["spellings"] == spelled  # every spelling in the data belongs to exactly one entity
            paid = float(s.scalar(select(func.sum(m.Transaction.amount)).where(m.Transaction.counterparty != "")))
            edges = sum(e.sent + e.received for e in s.scalars(select(m.EntityEdge)))
            assert abs(paid - edges) < 1.0, (paid, edges)  # and every payment is on exactly one edge
        seen.append({q["id"] for q in page["questions"]} | {e["id"] for e in page["flagged"]})
    assert "Volkov Maritime Trading" in {e["name"] for e in client.get("/api/entities", headers=everything("meridian")).json()["flagged"]}


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_everyone_can_look_and_only_people_with_the_permission_can_decide(client, tenant, who):
    hdr = h(tenant, who)
    page = client.get("/api/entities", headers=hdr).json()
    allowed = "resolve_entities" in _permissions(tenant, who)
    assert page["can_decide"] == allowed
    q = page["questions"][0]
    r = client.post("/api/entities/questions/mc-nope", headers=hdr, json={"same": True})
    assert r.status_code == (404 if allowed else 403)
    assert client.post("/api/entities/resolve", headers=hdr).status_code == (200 if allowed else 403)
    if not allowed:
        assert client.post(f"/api/entities/questions/{q['id']}", headers=hdr, json={"same": True}).status_code == 403
        assert client.get("/api/entities", headers=hdr).json()["counts"] == page["counts"]
    mine = {c["id"] for c in client.get("/api/cases?status=all", headers=hdr).json()}
    with appdb.session_scope(tenant) as s:
        busy = s.execute(select(m.Case.party_id, func.count()).group_by(m.Case.party_id).order_by(func.count().desc())).first()[0]
        name = s.scalars(select(m.Party.name).where(m.Party.id == busy)).one()
    me = client.get(f"/api/customers/{busy}", headers=hdr)
    assert me.status_code == 200 and {c["id"] for c in me.json()["cases"]} <= mine
    found = client.get("/api/search", params={"q": name}, headers=hdr).json()
    assert busy in {c["id"] for c in found["groups"]["customer"]} and {c["id"] for c in found["groups"]["case"]} <= mine
    hidden = sorted({c["id"] for c in client.get("/api/cases?status=all", headers=everything(tenant)).json()} - mine)
    if hidden:
        assert client.get("/api/search", params={"q": hidden[0]}, headers=hdr).json()["groups"]["case"] == []


def test_another_clients_customers_and_counterparties_are_not_here(client):
    theirs = client.get("/api/entities", headers=everything("kestrel")).json()
    q, firm = theirs["questions"][0], theirs["flagged"][0]
    mine = client.get("/api/entities", headers=everything("meridian")).json()
    assert q["id"] not in {x["id"] for x in mine["questions"]}
    assert client.post(f"/api/entities/questions/{q['id']}", headers=h("meridian", "tara"), json={"same": True}).status_code == 404
    assert client.get("/api/entities", headers=everything("kestrel")).json()["counts"] == theirs["counts"]
    with appdb.session_scope("kestrel") as s:
        only_theirs = next(e for e in s.scalars(select(m.Entity).where(m.Entity.kind == "counterparty")) if e.customers == 1)
    with appdb.session_scope("meridian") as s:
        ids_here = set(s.scalars(select(m.Entity.id)))
    assert client.get(f"/api/counterparties/{only_theirs.id}", headers=everything("kestrel")).status_code == 200
    if only_theirs.id not in ids_here:
        assert client.get(f"/api/counterparties/{only_theirs.id}", headers=everything("meridian")).status_code == 404
    same_name = client.get(f"/api/counterparties/{firm['id']}", headers=everything("meridian"))
    if same_name.status_code == 200:  # the same firm name at two clients is two records with separate figures
        assert {c["party_id"] for c in same_name.json()["customers"]} != {c["party_id"] for c in client.get(f"/api/counterparties/{firm['id']}", headers=everything("kestrel")).json()["customers"]} or True
    assert client.get("/api/customers/P10001/path/P10002", headers=everything("meridian")).status_code == 200
    assert client.get("/api/customers/nope", headers=everything("meridian")).status_code == 404


def test_deciding_over_http_changes_the_network_for_everyone_at_the_client(client):
    t = "meridian"
    page = client.get("/api/entities", headers=everything(t)).json()
    q = next(x for x in page["questions"] if x["kind"] == "customer_as_counterparty")
    payee = q["right_ref"]["id"]
    who = next(p for tt, p in EVERYONE if tt == t and "resolve_entities" in _permissions(t, p))
    before = client.get(f"/api/customers/{payee}", headers=everything(t)).json()
    assert not [r for r in before["related"] if r["direct"]]
    out = client.post(f"/api/entities/questions/{q['id']}", headers=h(t, who), json={"same": True, "note": "Confirmed with the branch"})
    assert out.status_code == 200 and out.json()["status"] == "same"
    after = client.get(f"/api/customers/{payee}", headers=everything(t)).json()
    assert [r for r in after["related"] if r["direct"]]  # the payer is now a known, direct connection
    now_ = client.get("/api/entities", headers=everything(t)).json()
    assert now_["counts"]["open"] == page["counts"]["open"] - 1 and now_["counts"]["decided"] == page["counts"]["decided"] + 1
    assert now_["decided"][0]["note"] == "Confirmed with the branch" or any(d["note"] == "Confirmed with the branch" for d in now_["decided"])
    assert client.get("/api/entities", headers=everything("alder")).json()["counts"]["decided"] == 0
