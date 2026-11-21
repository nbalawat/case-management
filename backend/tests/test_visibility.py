"""Who can see which case.

Two halves. The first drives the rule itself with made-up policies and compares three things that must always agree:
what the database filter returns, what the in-memory rule says, and an oracle written here from the policy's meaning.
The second walks the seeded demo over HTTP as every person at every client and checks that a case a person may not
see does not exist for them anywhere: lists, detail, actions, feeds, numbers and answers.
"""
import random

import pytest
import yaml
from sqlalchemy import func, select

import app.db as appdb
from app import visibility
from app.alerts.hub import route_alerts
from app.cases import assignments, restriction
from app.cases.case_types import load_case_types
from app.cases.service import TransitionError, create_case, record
from app.domain import models as m
from app.domain.access import AccessPolicyError, parse_policy
from app.tenants import access_admin
from support import PERSONAS, T
from test_access_per_client import _draft
from test_client_api import CLIENTS, EVERYONE, OP, PEOPLE, _auditor, _permissions, everything, h

TYPES = ["tm_alert_investigation", "sanctions_hit", "fraud_claim", "kyc_refresh"]
QUEUES = ["aml-l1", "aml-l2", "fraud", "kyc"]


# --- the oracle: the rule, written from its meaning and nothing else ---------------------------------------------------
def may_see(*, roles_see, queues, holds_view_restricted, who, case, actors) -> bool:
    """roles_see: one entry per role held, None for a role that sees everything, else {"scope":..., "case_types":...}."""
    if case["restricted"] and not holds_view_restricted:
        return False
    if any(r is None for r in roles_see) or case["owner"] == who or who in actors:
        return True
    for r in roles_see:
        in_scope = r.get("scope", "all") == "all" or (r["scope"] == "queues" and case["queue"] in queues)
        if in_scope and (r.get("case_types") is None or case["case_type"] in r["case_types"]):
            return True
    return False


def _row(c: m.Case) -> dict:
    return {"id": c.id, "restricted": c.restricted, "owner": c.owner, "queue": c.queue, "case_type": c.case_type}


def _make_policy(r: random.Random):
    sees = [None, {"scope": "queues"}, {"scope": "own"}, {"scope": "all", "case_types": r.sample(TYPES, r.randint(1, 2))},
            {"scope": "queues", "case_types": r.sample(TYPES, r.randint(1, 3))}]
    roles = {}
    for i in range(6):
        v = r.choice(sees)
        perms = ["work"] + (["view_restricted"] if r.random() < 0.3 else [])
        roles[f"r{i}"] = {"title": f"Role {i}", "permissions": perms} | ({"sees": v} if v else {})
    people = [{"id": f"u{i}", "roles": r.sample(sorted(roles), r.randint(1, 3)), "queues": r.sample(QUEUES, r.randint(0, 3))} for i in range(10)]
    raw = {"permissions": {"work": "w", "view_restricted": "v"}, "queues": {q: q for q in QUEUES}, "roles": roles, "people": people}
    return raw, parse_policy(raw)


@pytest.mark.parametrize("seed", range(12))
def test_the_database_filter_the_in_memory_rule_and_the_oracle_agree(db, seed):
    r = random.Random(seed)
    raw, policy = _make_policy(r)
    db.onboard(T)
    types = load_case_types(T)
    with db.tenant(T) as s:
        s.add_all([m.Party(id="P1", name="Someone"), m.Account(id="A1", party_id="P1")])
        cases, actors = [], {}
        for _ in range(50):
            c = create_case(s, types[r.choice(TYPES)], "P1", "medium")
            c.queue, c.owner, c.restricted = r.choice(QUEUES + [None]), r.choice([None] + [f"u{i}" for i in range(10)]), r.random() < 0.2
            actors[c.id] = set()
            for who in r.sample([f"u{i}" for i in range(10)], r.randint(0, 2)):
                record(s, c.id, who, "human", "note")
                actors[c.id].add(who)
            record(s, c.id, r.choice([f"u{i}" for i in range(10)]), "agent", "agent.finished")  # an agent's event is not a person's
            cases.append(c)
        s.commit()
        rows = [_row(c) for c in cases]
        outcomes = set()
        for person in raw["people"]:
            p = policy.people[person["id"]]
            want = {c["id"] for c in rows if may_see(roles_see=[raw["roles"][x].get("sees") for x in person["roles"]], queues=person["queues"],
                                                     holds_view_restricted=any("view_restricted" in raw["roles"][x]["permissions"] for x in person["roles"]),
                                                     who=p.id, case=c, actors=actors[c["id"]])}
            v = visibility.viewer_of(p)
            assert {c.id for c in cases if visibility.can_see(v, c, p.id in actors[c.id])} == want
            with db.tenant(T) as mine:
                visibility.bind_viewer(mine, p)
                assert set(mine.scalars(select(m.Case.id))) == want, (p.id, person)
                assert mine.scalar(select(func.count()).select_from(m.Case)) == len(want)
                assert {e.case_id for e in mine.scalars(select(m.CaseEvent))} == want  # every case has events
                assert all((mine.get(m.Case, c["id"]) is not None) == (c["id"] in want) for c in rows)
            outcomes.add(len(want) == len(rows))
            outcomes.add(len(want) == 0 or 0 < len(want) < len(rows))
        assert rows and True in outcomes or False in outcomes


@pytest.fixture
def desk(s):
    """Meridian with one case of each kind in a known place."""
    types = load_case_types(T)
    s.add_all([m.Party(id="P1", name="Someone"), m.Account(id="A1", party_id="P1")])
    made = {k: create_case(s, types[k], "P1", "medium") for k in ("tm_alert_investigation", "sanctions_hit", "fraud_claim", "kyc_refresh")}
    s.commit()
    return made


def _as(s, who):
    return visibility.bind_viewer(s, PERSONAS[who])


def _ids(s):
    return set(s.scalars(select(m.Case.id)))


def test_each_role_at_the_reference_client_sees_what_its_policy_says(s, desk):
    tm, sanc, fraud, kyc = (desk[k].id for k in ("tm_alert_investigation", "sanctions_hit", "fraud_claim", "kyc_refresh"))
    expect = {"ana": {tm, sanc},  # her queues: first-line monitoring and sanctions
              "ivan": {tm}, "lena": {tm}, "sofia": {sanc}, "kai": {kyc}, "fran": {fraud},
              "quinn": {tm, sanc, fraud, kyc}, "tara": {tm, sanc, fraud, kyc}, "maya": {tm, sanc, fraud, kyc}, "hugo": {tm, sanc, fraud, kyc},
              "omar": {tm, sanc, fraud, kyc}, "audra": {tm, sanc, fraud, kyc}}
    assert set(expect) == set(PERSONAS)
    for who, want in expect.items():
        assert _ids(_as(s, who)) == want, who
    desk["tm_alert_investigation"].queue = "aml-l2"  # escalated out of the analyst's queue
    s.commit()
    assert _ids(_as(s, "ana")) == {sanc}


def test_people_keep_sight_of_cases_they_own_or_have_acted_on(s, desk):
    tm = desk["tm_alert_investigation"]
    assignments.claim(s, tm, PERSONAS["ana"])
    tm.queue, tm.owner = "aml-l2", None  # escalated: no longer hers, no longer in her queue
    s.commit()
    assert tm.id in _ids(_as(s, "ana"))  # she claimed it, so its history stays open to her
    assert tm.id not in _ids(_as(s, "fran"))
    desk["fraud_claim"].owner = "ivan"  # an odd assignment still shows the owner their own case
    s.commit()
    assert desk["fraud_claim"].id in _ids(_as(s, "ivan"))
    record(s, desk["kyc_refresh"].id, "ivan", "agent", "agent.finished")  # only a person's own action counts
    s.commit()
    assert desk["kyc_refresh"].id not in _ids(_as(s, "ivan"))


def test_a_restricted_case_needs_the_permission_whatever_else_is_true(s, desk):
    tm = desk["tm_alert_investigation"]
    assignments.claim(s, tm, PERSONAS["ana"])
    child = create_case(s, load_case_types(T)["qa_review"], "P1", "medium", parent_id=tm.id)
    s.commit()
    with pytest.raises(TransitionError, match="cannot restrict"):
        restriction.set_restricted(s, tm, PERSONAS["ivan"], True, "because")
    with pytest.raises(TransitionError, match="Say why"):
        restriction.set_restricted(s, tm, PERSONAS["maya"], True, "  ")
    changed = restriction.set_restricted(s, tm, PERSONAS["maya"], True, "Subject is a member of staff")
    s.commit()
    assert {c.id for c in changed} == {tm.id, child.id} and tm.owner is None  # the owner could no longer see it, so it is released
    kinds = [e.kind for e in s.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == tm.id).order_by(m.CaseEvent.id))]
    assert kinds[-2:] == ["case.restricted", "case.released"]
    cleared = {p for p, x in PERSONAS.items() if x.can("view_restricted")}
    assert cleared == {"lena", "maya", "hugo", "audra"}
    for who in PERSONAS:
        seen = _ids(_as(s, who))
        assert (tm.id in seen) == (who in cleared), who  # owner, actor, lead, admin, QA: none of it is enough
        assert (child.id in seen) == (who in cleared - {"lena"}), who  # being cleared adds nothing to what her role shows: no QA reviews
    later = create_case(visibility.bind_viewer(s, PERSONAS["maya"]), load_case_types(T)["qa_review"], "P1", "medium", parent_id=tm.id)
    assert later.restricted and later.restricted_reason  # a case opened from a restricted case starts restricted
    restriction.set_restricted(s, tm, PERSONAS["hugo"], False, "Cleared by HR")
    s.commit()
    assert not tm.restricted and not child.restricted and tm.id in _ids(_as(s, "ivan"))


def test_everything_that_hangs_off_a_case_is_hidden_with_it(s, desk):
    tm, fraud = desk["tm_alert_investigation"], desk["fraud_claim"]
    for c in (tm, fraud):
        s.add_all([m.Alert(id=f"al-{c.id}", party_id="P1", scenario="structuring", scenario_version="1", score=0.9, explanation="x", txn_ids=[],
                           dedupe_key=f"k-{c.id}", case_id=c.id),
                   m.AgentRun(id=f"run-{c.id}", case_id=c.id, provider="rules", steps=[]),
                   m.InfoRequest(id=f"rfi-{c.id}", case_id=c.id, recipient="branch", question="?", raised_by="ivan", due=m.now()),
                   m.Filing(id=f"fil-{c.id}", case_id=c.id, kind="SAR", filed_by="maya", payload={}, sha256="0" * 64),
                   m.AgentStep(run_id=f"run-{c.id}", case_id=c.id, agent="x", agent_version="1", level="L1", policy_version=1, hash=c.id)])
    s.add(m.Alert(id="al-loose", party_id="P1", scenario="structuring", scenario_version="1", score=0.2, explanation="x", txn_ids=[], dedupe_key="k-loose"))
    s.commit()
    _as(s, "fran")  # sees fraud only
    for model in (m.CaseEvent, m.AgentRun, m.InfoRequest, m.Filing, m.AgentStep):
        assert {x.case_id for x in s.scalars(select(model))} == {fraud.id}, model.__name__
        assert s.scalar(select(func.count()).select_from(model).where(model.case_id == tm.id)) == 0
    assert {a.id for a in s.scalars(select(m.Alert))} == {f"al-{fraud.id}", "al-loose"}  # an alert in no case hides nothing
    joined = s.execute(select(m.Alert.id, m.Case.id).join(m.Case, m.Case.id == m.Alert.case_id)).all()
    assert joined == [(f"al-{fraud.id}", fraud.id)]
    assert s.scalars(select(m.Party)).all() and s.scalars(select(m.Account)).all()  # customers are not case-scoped


def test_platform_work_sees_everything_whoever_asked(s, desk):
    """Routing must find the customer's existing case even when the person who triggered it cannot see that case."""
    tm = desk["tm_alert_investigation"]
    _as(s, "fran")
    assert tm.id not in _ids(s)
    a = m.Alert(id="al-new", party_id="P1", scenario="structuring", scenario_version="1", score=0.9, explanation="x", txn_ids=[], dedupe_key="k-new")
    s.add(a)
    s.flush()
    before = s.scalar(select(func.count()).select_from(m.Case).execution_options(populate_existing=True)) or 0
    with visibility.system(s):
        total = s.scalar(select(func.count()).select_from(m.Case))
        with visibility.system(s):
            pass
        assert s.scalar(select(func.count()).select_from(m.Case)) == total == 4  # leaving an inner block does not end the outer one
    assert before == 1 and s.scalar(select(func.count()).select_from(m.Case)) == 1  # and the person's view is back afterwards
    assert [c.id for c in route_alerts(s, [a], screened=True)] == [tm.id] and a.case_id == tm.id  # joined the open case: no duplicate
    with visibility.system(s):
        assert s.scalar(select(func.count()).select_from(m.Case)) == 4
    with pytest.raises(RuntimeError):
        with visibility.system(s):
            raise RuntimeError
    assert s.scalar(select(func.count()).select_from(m.Case)) == 1  # an error inside platform work still restores the view


def test_find_case_asks_the_database_even_when_the_session_already_holds_the_case(s, desk):
    tm = desk["tm_alert_investigation"]
    _as(s, "fran")
    with visibility.system(s):
        assert s.get(m.Case, tm.id) is tm  # platform work loads it into the session
    assert visibility.find_case(s, tm.id) is None and visibility.find_case(s, desk["fraud_claim"].id) is desk["fraud_claim"]
    assert visibility.find_case(s, None) is None and visibility.find_case(s, "no-such-case") is None


# --- the policy language -----------------------------------------------------------------------------------------------
def _raw(**role):
    return {"permissions": {"work": "w", "view_restricted": "v", "restrict_case": "r"}, "queues": {"q": "Q"},
            "roles": {"a": {"title": "A", "permissions": ["work"]} | role}, "people": [{"id": "u", "role": "a", "queues": ["q"]}]}


@pytest.mark.parametrize("sees,message", [
    ({"scope": "everything"}, "unknown visibility scope"), ("queues", "takes a scope"), ({"scope": "all", "types": ["x"]}, "takes a scope"),
    ({"scope": "all", "case_types": []}, "non-empty list"), ({"scope": "all", "case_types": "tm"}, "non-empty list"),
    ({"scope": "all", "case_types": [1]}, "non-empty list")])
def test_a_malformed_visibility_rule_is_refused(sees, message):
    with pytest.raises(AccessPolicyError, match=message):
        parse_policy(_raw(sees=sees))


def test_visibility_rules_parse_and_combine_across_roles():
    assert parse_policy(_raw()).people["u"].views is None
    assert parse_policy(_raw(sees="all")).people["u"].views is None
    assert parse_policy(_raw(sees={"scope": "all"})).people["u"].views is None  # no limit at all is not a limit
    one = parse_policy(_raw(sees={"scope": "queues", "case_types": ["x"]})).people["u"].views
    assert [(v.scope, v.case_types) for v in one] == [("queues", frozenset({"x"}))] and one[0].describe({"x": "Fraud claim"}) == "Fraud claim cases in their own queues"
    raw = _raw(sees={"scope": "own"})
    raw["roles"]["b"] = {"title": "B", "permissions": ["work"], "sees": {"scope": "queues"}}
    raw["roles"]["c"] = {"title": "C", "permissions": ["work"]}
    raw["people"] = [{"id": "two", "roles": ["a", "b"], "queues": ["q"]}, {"id": "three", "roles": ["a", "b", "c"], "queues": ["q"]},
                     {"id": "same", "roles": ["b", "b"], "queues": ["q"]}]
    people = parse_policy(raw).people
    assert [v.scope for v in people["two"].views] == ["own", "queues"] and people["three"].views is None and len(people["same"].views) == 1
    with pytest.raises(AccessPolicyError, match="could not then see them"):
        parse_policy(_raw(permissions=["work", "restrict_case"]))
    assert parse_policy(_raw(permissions=["work", "restrict_case", "view_restricted"]))


def test_a_policy_that_blinds_people_to_their_own_queue_is_refused_and_changes_are_described(s):
    def blind(r):
        r["roles"]["kyc_analyst"]["sees"] = {"scope": "all", "case_types": ["fraud_claim"]}

    with pytest.raises(AccessPolicyError, match="Kai Nakamura is in the queue 'KYC and onboarding' but could not see the .* cases that arrive there"):
        access_admin.check(s, _draft(edit=blind))
    with pytest.raises(AccessPolicyError, match="could not see"):
        access_admin.check(s, _draft(edit=lambda r: r["roles"]["analyst"].update(sees={"scope": "own"})))

    def narrow(r):
        r["roles"]["qa_reviewer"]["sees"] = {"scope": "queues"}
        r["roles"]["investigator"].pop("sees")
        r["roles"]["fraud_analyst"]["sees"] = {"scope": "all", "case_types": ["fraud_claim", "chargeback"]}

    out = access_admin.check(s, _draft(edit=narrow))
    assert "'QA reviewer' now sees: Every case in their own queues (was: Every case)" in out["changes"]
    assert "'L2 investigator' now sees: Every case (was: tm_alert_investigation cases)" in out["changes"]
    assert any("does not run: chargeback" in w for w in out["warnings"]) and any("restricted case waits" in w for w in out["warnings"])
    assert not [w for w in access_admin.check(s, _draft(edit=lambda r: None))["warnings"] if "does not run" in w]


# --- the seeded demo over HTTP, as everyone ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world(client):
    """Every case at every client, read straight from the database with nobody's view applied."""
    out = {}
    for t in CLIENTS:
        with appdb.session_scope(t) as s:
            cases = {c.id: _row(c) | {"open": c.closed is None, "parent": c.parent_id, "party": c.party_id} for c in s.scalars(select(m.Case))}
            actors = {cid: set() for cid in cases}
            for e in s.scalars(select(m.CaseEvent).where(m.CaseEvent.actor_kind == "human")):
                actors[e.case_id].add(e.actor)
        out[t] = {"cases": cases, "actors": actors}
    return out


def _expected(world, tenant, who) -> set[str]:
    raw = PEOPLE[tenant]
    person = next(p for p in raw["people"] if p["id"] == who)
    roles = person.get("roles") or [person["role"]]
    sees = [None if raw["roles"][r].get("sees") in (None, "all") else raw["roles"][r]["sees"] for r in roles]
    return {cid for cid, c in world[tenant]["cases"].items() if may_see(
        roles_see=sees, queues=person.get("queues", []), holds_view_restricted="view_restricted" in _permissions(tenant, who),
        who=who, case=c, actors=world[tenant]["actors"][cid])}


def _listed(client, tenant, who, status="all"):
    r = client.get(f"/api/cases?status={status}", headers=h(tenant, who))
    assert r.status_code == 200, r.text
    return {c["id"] for c in r.json()}


def test_the_demo_exercises_the_rule(world):
    """If every person saw everything, the tests below would prove nothing."""
    shapes = {t: {who: len(_expected(world, t, who)) for tt, who in EVERYONE if tt == t} for t in CLIENTS}
    for t, counts in shapes.items():
        total = len(world[t]["cases"])
        assert max(counts.values()) == total and counts[_auditor(t)] == total
        assert sum(1 for n in counts.values() if 0 < n < total) >= 3, counts  # several people see a real, partial slice
        restricted = [c for c in world[t]["cases"].values() if c["restricted"]]
        roots = [c for c in restricted if c["parent"] not in {x["id"] for x in restricted}]
        assert len(roots) == 1 and roots[0]["open"] and roots[0]["owner"]  # one restricted matter, plus any case opened from it
        cleared = {who for tt, who in EVERYONE if tt == t and "view_restricted" in _permissions(t, who)}
        assert roots[0]["owner"] in cleared and 0 < len(cleared) < len(counts)
        for c in world[t]["cases"].values():  # nobody is blind to work waiting in their own queue
            if c["open"] and not c["restricted"]:
                members = [p["id"] for p in PEOPLE[t]["people"] if c["queue"] in p.get("queues", [])]
                assert members and all(c["id"] in _expected(world, t, who) for who in members), c


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_every_person_is_listed_exactly_the_cases_they_may_see(client, world, tenant, who):
    want = _expected(world, tenant, who)
    cases = world[tenant]["cases"]
    assert _listed(client, tenant, who) == want
    assert _listed(client, tenant, who, "open") == {i for i in want if cases[i]["open"]}
    assert _listed(client, tenant, who, "closed") == {i for i in want if not cases[i]["open"]}


def _one_of_each(cases: dict, ids: set[str]) -> list[str]:
    picked = {}
    for cid in sorted(ids):
        c = cases[cid]
        picked.setdefault((c["case_type"], c["open"], c["restricted"]), cid)
    return list(picked.values())


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_a_case_a_person_may_not_see_does_not_exist_for_them(client, world, tenant, who):
    cases, want = world[tenant]["cases"], _expected(world, tenant, who)
    hidden = _one_of_each(cases, set(cases) - want)
    hdr = h(tenant, who)
    for cid in hidden:
        r = client.get(f"/api/cases/{cid}", headers=hdr)
        assert r.status_code == 404 and cid not in r.text and cases[cid]["party"] not in r.text
        before = client.get(f"/api/cases/{cid}", headers=everything(tenant)).json()
        for method, url, body in [
                ("post", f"/api/cases/{cid}/transition", {"to": "closed_no_action", "note": "x"}), ("post", f"/api/cases/{cid}/claim", None),
                ("post", f"/api/cases/{cid}/release", None), ("post", f"/api/cases/{cid}/reassign", {"owner": who, "reason": "x"}),
                ("post", f"/api/cases/{cid}/narrative", {"text": "x"}), ("post", f"/api/cases/{cid}/agents/run", None),
                ("post", f"/api/cases/{cid}/tasks/anything", {"done": True}), ("post", f"/api/cases/{cid}/requests", {"recipient": "branch", "question": "?"}),
                ("post", f"/api/cases/{cid}/copilot", {"question": "Summarise this case"}),
                ("post", f"/api/cases/{cid}/restrict", {"restricted": False, "reason": "x"}),
                ("get", f"/api/agents/ledger?case_id={cid}", None)]:
            out = getattr(client, method)(url, headers=hdr, **({"json": body} if body is not None else {}))
            if method == "get":
                assert out.status_code == 403 or out.json()["entries"] == [], url
            else:
                assert out.status_code in (403, 404), f"{who} got {out.status_code} from {url}"  # refused for the role, or simply not there
        assert client.get(f"/api/cases/{cid}", headers=everything(tenant)).json() == before  # and nothing about it changed
        for rfi in before["requests"]:
            if rfi["status"] == "open":
                assert client.post(f"/api/requests/{rfi['id']}/answer", headers=hdr, json={"response": "x"}).status_code in (403, 404)
    for cid in _one_of_each(cases, want):
        detail = client.get(f"/api/cases/{cid}", headers=hdr)
        assert detail.status_code == 200, f"{who} could not open {cid}"
        d = detail.json()
        linked = {x["id"] for x in d["children"] + d["other_cases"]} | ({d["parent"]["id"]} if d["parent"] else set())
        assert linked <= want  # nor do links from a case they can see lead to one they cannot
        everyone_sees = {x["id"] for x in (lambda e: e["children"] + e["other_cases"])(client.get(f"/api/cases/{cid}", headers=everything(tenant)).json())}
        assert linked >= {i for i in everyone_sees if i in want}


@pytest.mark.parametrize("tenant,who", EVERYONE, ids=[f"{t}:{p}" for t, p in EVERYONE])
def test_feeds_numbers_and_answers_are_cut_to_the_same_view(client, world, tenant, who):
    cases, want = world[tenant]["cases"], _expected(world, tenant, who)
    hdr = h(tenant, who)
    open_now = {c["id"] for c in client.get("/api/cases?status=open", headers=hdr).json()}  # timers may have moved things since `world`
    assert open_now <= want

    events = client.get("/api/events?limit=500", headers=hdr).json()
    assert {e["case_id"] for e in events} <= want and (events or not want)

    work = client.get("/api/work", headers=hdr).json()
    shown = {c["id"] for c in work["mine"]} | {c["id"] for q in work["queues"] for c in q["cases"]}
    assert shown <= want and all(c["owner"] == who for c in work["mine"])
    assert work["persona"]["sees"]["everything"] == (want == set(cases) or work["persona"]["sees"]["views"] == ["Every case"])
    assert work["persona"]["sees"]["restricted"] == ("view_restricted" in _permissions(tenant, who))

    board = client.get("/api/dashboard", headers=hdr).json()
    assert board["kpis"]["open_cases"] == len(open_now) == sum(f["count"] for f in board["funnel"])
    assert sum(d["opened"] for d in board["trend"]) <= len(want)

    team = client.get("/api/team", headers=hdr)
    assert (team.status_code == 200) == ("view_team" in _permissions(tenant, who))
    if team.status_code == 200:
        assert {c["id"] for c in team.json()["attention"]} <= want and sum(q["open"] for q in team.json()["queues"]) <= len(open_now)

    ledger = client.get("/api/agents/ledger?limit=500", headers=hdr)
    if ledger.status_code == 200:
        assert {e["case_id"] for e in ledger.json()["entries"] if e.get("case_id")} <= want and ledger.json()["chain"]["intact"]

    total = client.post("/api/ask", headers=hdr, json={"question": "how many cases are there in total"}).json()
    assert total["steps"][0]["result"]["matching_cases"] == len(want)
    hidden = sorted(set(cases) - want)
    if hidden:
        body = client.post("/api/ask", headers=hdr, json={"question": f"tell me about {hidden[0]}"}).json()
        assert body["cited"] == [] and "No case" in body["answer"] and cases[hidden[0]]["party"] not in str(body)


def test_restricting_a_case_over_http_and_lifting_it_again(client, world):
    t = "meridian"
    cases = world[t]["cases"]
    target = next(c for c in sorted(cases.values(), key=lambda c: c["id"])
                  if c["open"] and c["case_type"] == "tm_alert_investigation" and c["owner"] == "ivan" and not c["restricted"])
    cid, url = target["id"], f"/api/cases/{target['id']}/restrict"
    assert client.post(url, headers=h(t, "ivan"), json={"reason": "x"}).status_code == 403  # the owner cannot restrict it
    assert client.post(url, headers=h(t, "audra"), json={"reason": "x"}).status_code == 403  # nor a read-only auditor
    assert client.post(url, headers=h(t, "maya"), json={"reason": ""}).status_code == 422
    assert client.post(url, headers=h("kestrel", "joy"), json={"reason": "x"}).status_code == 404  # another client's officer
    try:
        out = client.post(url, headers=h(t, "maya"), json={"reason": "Subject is a relative of a board member"})
        assert out.status_code == 200 and cid in out.json()["cases"]
        sees = {who for tt, who in EVERYONE if tt == t and cid in _listed(client, t, who)}
        assert sees == {"lena", "maya", "hugo", "audra"}
        assert client.get(f"/api/cases/{cid}", headers=h(t, "ivan")).status_code == 404  # he owned it and worked it; it is gone for him
        detail = client.get(f"/api/cases/{cid}", headers=h(t, "lena")).json()
        assert detail["case"]["restricted"] and detail["case"]["owner"] is None and detail["access"]["restricted_reason"].startswith("Subject")
        assert detail["access"]["can_restrict"] is False and [e["kind"] for e in detail["events"]][-2:] == ["case.restricted", "case.released"]
        assert client.post(f"/api/cases/{cid}/claim", headers=h(t, "lena")).status_code == 200  # a cleared investigator carries on
        assert client.get(f"/api/cases/{cid}", headers=h(t, "lena")).json()["case"]["owner"] == "lena"
        assert cid not in str(client.get("/api/events?limit=500", headers=h(t, "ivan")).json())
        assert cid not in str(client.get("/api/work", headers=h(t, "tara")).json()) and cid not in str(client.get("/api/team", headers=h(t, "tara")).json())
        ops = client.get("/api/platform/overview", headers=OP).json()
        assert next(r for r in ops["tenants"] if r["id"] == t)["cases"] == len(cases) and cid not in str(ops)  # the operator still only counts
    finally:
        assert client.post(url, headers=h(t, "hugo"), json={"restricted": False, "reason": "Relationship disproved"}).status_code == 200
    assert cid in _listed(client, t, "ivan") and not client.get(f"/api/cases/{cid}", headers=h(t, "ivan")).json()["case"]["restricted"]


def test_an_alert_from_outside_joins_a_case_the_default_person_could_not_see(client, world):
    """A monitoring engine has no person's view: a new alert for a customer with a restricted case joins that case."""
    t = "meridian"
    case = next(c for c in world[t]["cases"].values() if c["restricted"] and c["open"])
    body = {"source": "actimize", "external_id": "VIS-1", "scenario": "structuring", "party_id": case["party"], "score": 0.91, "explanation": "x"}
    before = len(world[t]["cases"])
    out = client.post("/api/alerts", headers=h(t), json=body)
    assert out.status_code == 201 and out.json()["case_id"] == case["id"] and out.json()["duplicate"] is False
    assert client.post("/api/alerts", headers=h(t), json=body).json()["duplicate"] is True  # the replay is recognised too
    assert len(_listed(client, t, _auditor(t))) == before


def test_the_access_page_says_in_words_what_each_role_sees(client):
    page = client.get("/api/access", headers=h("meridian", "omar")).json()
    roles = {r["id"]: r for r in page["roles"]}
    assert roles["analyst"]["sees"] == "Every case in their own queues" and roles["analyst"]["limited"]
    assert roles["investigator"]["sees"] == "Transaction monitoring investigation cases"
    assert roles["mlro"]["sees"] == "Every case" and roles["mlro"]["restricted"] and not roles["admin"]["restricted"]
    people = {p["id"]: p for p in page["people"]}
    assert people["lena"]["restricted"] and len(people["lena"]["sees"]) == 2 and people["audra"]["sees"] == ["Every case"]
    assert {p["id"] for p in page["permissions"]} >= {"view_restricted", "restrict_case"}
    assert yaml.safe_load(page["yaml"])["roles"]["analyst"]["sees"] == {"scope": "queues"}
