"""Customer reach, tipping-off and break-glass access.

Three rules join the case rules of test_visibility.py:
  * a role reaches customers by attribute (booking jurisdiction, segment, a relationship manager's own book), and a
    person's own jurisdictions and segments narrow that further. A customer out of reach does not exist for the
    person, nor do its accounts, transactions, cases and reports;
  * customer-facing people never see the case types where suspicion is investigated, nor any report;
  * a live break-glass grant opens one case or one customer, never a restricted case unless the client allows it, and
    never past tipping-off.

As in test_visibility.py, the rules are checked three ways that must agree: the SQL filter the session applies, the
in-memory `can_see` / `can_see_party`, and an oracle written here from the policy's raw YAML and nothing else.
"""
import random
from dataclasses import replace
from datetime import timedelta

import pytest
import yaml
from sqlalchemy import func, select

from app import break_glass, visibility
from app.break_glass import BreakGlassError
from app.cases.case_types import load_case_types
from app.cases.service import create_case, record
from app.domain import models as m
from app.domain.access import AccessPolicyError, parse_policy
from app.domain.models import now
from app.tenants import access_admin, registry
from support import T
from test_access_per_client import _draft

TYPES = ["tm_alert_investigation", "sanctions_hit", "fraud_claim", "kyc_refresh"]
QUEUES = ["aml-l1", "fraud", "kyc"]
PLACES = ["US", "GB", "CA", "DE"]
SEGMENTS = ["retail", "business", "private"]
PEOPLE = [f"u{i}" for i in range(10)]


# --- the oracle ---------------------------------------------------------------------------------------------------------
def _meet(a, b):
    return b if a is None else a if b is None else set(a) & set(b)


def oracle_reaches(raw, who, party) -> bool:
    person = next(p for p in raw["people"] if p["id"] == who)
    where = party["booking"] or party["country"]
    for rid in person["roles"]:
        c = raw["roles"][rid].get("customers") or {}
        places, segments = _meet(c.get("jurisdictions"), person.get("jurisdictions")), _meet(c.get("segments"), person.get("segments"))
        if ((places is None or where in places) and (segments is None or party["segment"] in segments)
                and (c.get("book", "any") == "any" or party["rm"] == who)):
            return True
    return False


def oracle(raw, who, cases, parties, filings, grants, actors):
    """What `who` sees: case ids, customer ids and report ids."""
    person = next(p for p in raw["people"] if p["id"] == who)
    roles = [raw["roles"][r] for r in person["roles"]]
    facing = any(r.get("customer_facing") for r in roles)
    hidden = set(raw["tipping_off"]["case_types"]) if facing else set()
    no_reports = facing and raw["tipping_off"]["filings"] == "hidden"
    sees_restricted = any("view_restricted" in r["permissions"] for r in roles)
    glass_restricted = raw["break_glass"]["restricted"]
    live = {(g["kind"], g["target"]) for g in grants if g["person"] == who and g["live"]}

    def opened(c):
        return ("case", c["id"]) in live and (not c["restricted"] or sees_restricted or glass_restricted) and c["case_type"] not in hidden

    def by_roles(c):
        if c["owner"] == who or who in actors[c["id"]]:
            return True
        for r in roles:
            v = r.get("sees")
            if v is None:
                return True
            in_scope = v.get("scope", "all") == "all" or (v["scope"] == "queues" and c["queue"] in person.get("queues", []))
            if in_scope and (v.get("case_types") is None or c["case_type"] in v["case_types"]):
                return True
        return False

    seen = set()
    for c in cases.values():
        if c["case_type"] in hidden:
            continue
        if opened(c) or ((not c["restricted"] or sees_restricted) and oracle_reaches(raw, who, parties[c["party"]]) and by_roles(c)):
            seen.add(c["id"])
    customers = {pid for pid, p in parties.items() if oracle_reaches(raw, who, p) or ("customer", pid) in live
                 or any(opened(c) and c["party"] == pid for c in cases.values())}
    reports = set() if no_reports else {f["id"] for f in filings if (f["case"] is None or f["case"] in seen)
                                       and (not f["party"] or f["party"] in customers)}
    return seen, customers, reports


# --- random policies, customers, cases and grants -------------------------------------------------------------------------
def _reach(r: random.Random):
    return r.choice([None, {"jurisdictions": r.sample(PLACES, r.randint(1, 2))}, {"segments": r.sample(SEGMENTS, r.randint(1, 2))},
                     {"book": "own"}, {"jurisdictions": r.sample(PLACES, 2), "segments": r.sample(SEGMENTS, 2)},
                     {"book": "own", "segments": ["private", "business"]}])


def _make_policy(r: random.Random):
    sees = [None, {"scope": "queues"}, {"scope": "own"}, {"scope": "all", "case_types": r.sample(TYPES, r.randint(1, 3))}]
    roles = {}
    for i in range(7):
        facing = i >= 5  # two customer-facing roles, five inside ones
        perms = ["work"] + (["view_restricted"] if not facing and r.random() < 0.3 else []) + (["break_glass"] if not facing and r.random() < 0.5 else [])
        role = {"title": f"Role {i}", "permissions": perms}
        if (v := r.choice(sees)) is not None:
            role["sees"] = v
        if (c := _reach(r)) is not None:
            role["customers"] = c
        if facing:
            role["customer_facing"] = True
        roles[f"r{i}"] = role
    inside, outside = [k for k, v in roles.items() if not v.get("customer_facing")], [k for k, v in roles.items() if v.get("customer_facing")]
    people = []
    for who in PEOPLE:
        held = r.sample(outside, r.randint(1, 2)) if r.random() < 0.3 else r.sample(inside, r.randint(1, 3))
        person = {"id": who, "roles": held, "queues": r.sample(QUEUES, r.randint(0, 2))}
        if r.random() < 0.3:
            person["jurisdictions"] = r.sample(PLACES, r.randint(1, 2))
        if r.random() < 0.2:
            person["segments"] = r.sample(SEGMENTS, r.randint(1, 2))
        people.append(person)
    raw = {"permissions": {"work": "w", "view_restricted": "v", "break_glass": "b"}, "queues": {q: q for q in QUEUES}, "roles": roles,
           "people": people, "tipping_off": {"case_types": r.sample(TYPES[:2], r.randint(1, 2)), "filings": r.choice(["hidden", "visible"])},
           "break_glass": {"hours": 4, "restricted": r.random() < 0.5}}
    return raw, parse_policy(raw)


@pytest.mark.parametrize("seed", range(16))
def test_the_database_filter_the_in_memory_rule_and_the_oracle_agree(db, seed):
    r = random.Random(seed)
    raw, policy = _make_policy(r)
    db.onboard(T)
    types = load_case_types(T)
    at = now()
    with db.tenant(T) as s:
        parties = {}
        for i in range(14):
            p = {"booking": r.choice([None] + PLACES), "country": r.choice(PLACES), "segment": r.choice(SEGMENTS),
                 "rm": r.choice([None] + PEOPLE)}
            parties[f"P{i}"] = p
            s.add(m.Party(id=f"P{i}", name=f"Customer {i}", country=p["country"], booking_jurisdiction=p["booking"], segment=p["segment"],
                          relationship_manager=p["rm"]))
            s.add(m.Account(id=f"A{i}", party_id=f"P{i}"))
            s.add(m.Transaction(id=f"T{i}", account_id=f"A{i}", party_id=f"P{i}", ts=at, amount=10, currency="USD", direction="out",
                                channel="wire", counterparty="x", counterparty_country="US", feed_id="f"))
        s.flush()
        cases, actors = {}, {}
        for _ in range(45):
            c = create_case(s, types[r.choice(TYPES)], r.choice(sorted(parties)), "medium")
            c.queue, c.owner, c.restricted = r.choice(QUEUES + [None]), r.choice([None] + PEOPLE), r.random() < 0.2
            actors[c.id] = set()
            for who in r.sample(PEOPLE, r.randint(0, 2)):
                record(s, c.id, who, "human", "note")
                actors[c.id].add(who)
            record(s, c.id, "break-glass", "system", "access.break_glass")  # the system's own event is nobody's
            cases[c.id] = {"id": c.id, "case_type": c.case_type, "party": c.party_id, "queue": c.queue, "owner": c.owner,
                           "restricted": c.restricted}
        filings = []
        for i in range(12):
            case = r.choice([None] + sorted(cases))
            party = cases[case]["party"] if case else r.choice(["", *sorted(parties)])
            filings.append({"id": f"F{i}", "case": case, "party": party})
            s.add(m.Filing(id=f"F{i}", case_id=case, party_id=party, kind="SAR", filed_by="x", sha256="0" * 64))
        grants = []
        for i in range(30):
            g = {"person": r.choice(PEOPLE), "kind": r.choice(["case", "customer"]), "state": r.choice(["live", "live", "expired", "closed"])}
            g["target"] = r.choice(sorted(cases)) if g["kind"] == "case" else r.choice(sorted(parties))
            g["live"] = g["state"] == "live"
            grants.append(g)
            opened = at - timedelta(hours=6 if g["state"] == "expired" else 1)
            s.add(m.AccessGrant(id=f"g{i}", person=g["person"], kind=g["kind"], target=g["target"], reason="r" * 20, opened=opened,
                                ends=opened + timedelta(hours=4), closed=at if g["state"] == "closed" else None))
        s.commit()

        for who in PEOPLE:
            want_cases, want_parties, want_reports = oracle(raw, who, cases, parties, filings, grants, actors)
            v = visibility.viewer_of(policy.people[who], policy)
            s.info[visibility.VIEWER_KEY] = v
            s.expunge_all()
            got = set(s.scalars(select(m.Case.id)))
            assert got == want_cases, (seed, who)
            assert s.scalar(select(func.count()).select_from(m.Case)) == len(want_cases)
            assert set(s.scalars(select(m.Party.id))) == want_parties, (seed, who)
            assert set(s.scalars(select(m.Account.party_id))) == want_parties
            assert set(s.scalars(select(m.Transaction.party_id))) == want_parties
            assert set(s.scalars(select(m.Filing.id))) == want_reports, (seed, who)
            assert {e for e in s.scalars(select(m.CaseEvent.case_id))} == want_cases
            # the in-memory rule, case by case and customer by customer
            live = {(g["kind"], g["target"]) for g in grants if g["person"] == who and g["live"]}
            granted = frozenset(t for k, t in live if k == "case")
            with visibility.system(s):
                every = list(s.scalars(select(m.Case)))
                people = {p.id: p for p in s.scalars(select(m.Party))}
            for c in every:
                assert visibility.can_see(v, c, who in actors[c.id], granted, party=people[c.party_id]) == (c.id in want_cases), (seed, who, c.id)
            opened = [c for c in every if visibility.opens(v, c, granted)]
            for pid, p in people.items():
                mine = frozenset(t for k, t in live if k == "customer")
                assert visibility.can_see_party(v, p, mine, opened) == (pid in want_parties), (seed, who, pid)
            s.info.pop(visibility.VIEWER_KEY)


# --- parsing ------------------------------------------------------------------------------------------------------------
def _with(edit):
    raw = yaml.safe_load(_draft())
    edit(raw)
    return raw


def _role(raw, rid, **kw):
    raw["roles"][rid].update(kw)


@pytest.mark.parametrize("edit,message", [
    (lambda r: _role(r, "analyst", customers={"places": ["US"]}), "'customers' takes jurisdictions, segments and book"),
    (lambda r: _role(r, "analyst", customers={"book": "mine"}), "book is 'own'"),
    (lambda r: _role(r, "analyst", customers={"jurisdictions": "US"}), "must be a list of codes"),
    (lambda r: _role(r, "analyst", customer_facing="yes"), "customer_facing is true or false"),
    (lambda r: _role(r, "relationship_manager", permissions=["view_restricted"], read_only=False), "cannot hold view_restricted"),
    (lambda r: _role(r, "analyst", custmers={"book": "own"}), "unknown setting(s): custmers"),
    (lambda r: next(p for p in r["people"] if p["id"] == "rosa").update(role=None, roles=["relationship_manager", "analyst"]),
     "combines"),
    (lambda r: next(p for p in r["people"] if p["id"] == "ana").update(jurisdictions="US"), "must be a list of codes"),
    (lambda r: r.pop("break_glass"), "hold 'break_glass' but the policy has no break_glass section"),
    (lambda r: r.update(break_glass={"hours": 0}), "whole number from 1 to 72"),
    (lambda r: r.update(break_glass={"hours": 4, "restricted": "no"}), "restricted is true or false"),
    (lambda r: r.update(break_glass={"minutes": 30}), "break_glass takes hours and restricted"),
    (lambda r: r.update(tipping_off={"filings": "shown"}), "filings is 'hidden' or 'visible'"),
    (lambda r: r.update(tipping_off={"types": []}), "tipping_off takes case_types and filings"),
    (lambda r: _role(r, "relationship_manager", permissions=["view_team", "break_glass"], read_only=False), "cannot break glass"),
])
def test_a_policy_that_breaks_the_customer_rules_is_refused_with_a_reason(edit, message):
    with pytest.raises(AccessPolicyError, match=None) as e:
        parse_policy(_with(edit))
    assert message in str(e.value)


def test_a_person_narrows_their_roles_reach_and_the_page_says_so():
    p = parse_policy(_with(lambda r: next(x for x in r["people"] if x["id"] == "ana").update(jurisdictions=["CA"], segments=["retail"])))
    assert [c.describe() for c in p.people["ana"].customers] == ["Customers in retail segment booked in CA"]
    assert p.people["rosa"].customers[0].describe() == "Their own book" and p.people["rosa"].customer_facing
    assert p.people["omar"].customers is None  # nothing said: every customer


def test_the_check_refuses_a_customer_facing_person_in_a_queue_where_suspicion_arrives(s):
    def edit(raw):
        raw["roles"]["relationship_manager"].update(read_only=False, permissions=["work"])
        raw["roles"]["relationship_manager"].pop("sees")
        next(p for p in raw["people"] if p["id"] == "rosa")["queues"] = ["aml-l1"]
    with pytest.raises(AccessPolicyError, match="Rosa Bianchi is customer-facing but is in the queue 'AML alert triage"):
        access_admin.check(s, _draft(edit=edit))
    # the reference client runs every library case type, so name one it does not run
    out = access_admin.check(s, _draft(edit=lambda r: r.update(tipping_off={"case_types": ["trade_surveillance"]})))
    assert any("does not run: trade_surveillance" in w for w in out["warnings"])


# --- tipping-off and customer reach in the reference client -------------------------------------------------------------
@pytest.fixture
def book(s):
    """Three customers: one in Rosa's book booked in the US, one in nobody's booked in GB, one US retail customer."""
    types = load_case_types(T)
    s.add_all([m.Party(id="ROSA1", name="Book customer", country="US", segment="private", relationship_manager="rosa"),
               m.Party(id="GB1", name="London customer", country="US", booking_jurisdiction="GB", segment="business"),
               m.Party(id="US1", name="Retail customer", country="US"),
               m.Account(id="ACC-R", party_id="ROSA1"), m.Account(id="ACC-G", party_id="GB1")])
    s.flush()
    made = {}
    for party in ("ROSA1", "GB1", "US1"):
        for kind in ("tm_alert_investigation", "fraud_claim", "kyc_refresh"):
            made[(party, kind)] = create_case(s, types[kind], party, "medium").id
    s.add(m.Filing(id="SAR-R", case_id=made[("ROSA1", "tm_alert_investigation")], party_id="ROSA1", kind="SAR", filed_by="maya", sha256="0" * 64))
    s.commit()
    return made


def _as(s, who):
    visibility.bind_viewer(s, registry.runtime(T).policy.people[who])
    s.expunge_all()
    return s


def test_a_relationship_manager_sees_their_own_book_but_never_the_suspicion(s, book):
    _as(s, "rosa")
    assert set(s.scalars(select(m.Party.id))) == {"ROSA1"}
    assert set(s.scalars(select(m.Account.id))) == {"ACC-R"}
    assert set(s.scalars(select(m.Case.id))) == {book[("ROSA1", "fraud_claim")], book[("ROSA1", "kyc_refresh")]}
    assert not list(s.scalars(select(m.Filing)))


def test_an_investigator_limited_to_us_bookings_does_not_see_a_london_booked_customer(s, book):
    _as(s, "noah")
    assert "GB1" not in set(s.scalars(select(m.Party.id))) and {"ROSA1", "US1"} <= set(s.scalars(select(m.Party.id)))
    assert set(s.scalars(select(m.Case.id))) == {book[("ROSA1", "tm_alert_investigation")], book[("US1", "tm_alert_investigation")]}
    assert not s.scalars(select(m.Account).where(m.Account.id == "ACC-G")).first()


def test_owning_a_case_does_not_lift_the_customer_line(s, book):
    with visibility.system(s):
        s.scalars(select(m.Case).where(m.Case.id == book[("GB1", "tm_alert_investigation")])).one().owner = "noah"
    s.commit()
    _as(s, "noah")
    assert book[("GB1", "tm_alert_investigation")] not in set(s.scalars(select(m.Case.id)))


# --- break glass ------------------------------------------------------------------------------------------------------------
REASON = "Customer called the fraud line about a wire I am tracing"


def _person(who):
    return registry.runtime(T).policy.people[who]


def _restrict(s, case_id):
    s.scalars(select(m.Case).where(m.Case.id == case_id)).one().restricted = True


def test_breaking_glass_opens_one_case_and_its_customer_until_it_is_closed(s, book):
    target = book[("GB1", "tm_alert_investigation")]
    noah = _person("noah")
    g = break_glass.open_grant(s, noah, "case", target, REASON)
    s.commit()
    assert g.ends - g.opened == timedelta(hours=4)
    _as(s, "noah")
    assert target in set(s.scalars(select(m.Case.id))) and "GB1" in set(s.scalars(select(m.Party.id)))
    assert book[("GB1", "fraud_claim")] not in set(s.scalars(select(m.Case.id)))  # only that one case
    with visibility.system(s):
        trail = list(s.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == target, m.CaseEvent.kind == "access.break_glass")))
        log = list(s.scalars(select(m.AccessEvent).where(m.AccessEvent.kind == "break_glass.opened")))
    assert trail[0].actor_kind == "system" and trail[0].payload["person"] == "noah" and log[0].detail["reason"] == REASON
    assert break_glass.open_grant(s, noah, "case", target, REASON).id == g.id  # opening again keeps the one grant
    break_glass.close(s, g.id, noah)
    s.commit()
    _as(s, "noah")
    assert target not in set(s.scalars(select(m.Case.id))) and "GB1" not in set(s.scalars(select(m.Party.id)))
    with pytest.raises(BreakGlassError, match="already ended"):
        break_glass.close(s, g.id, noah)


def test_a_grant_ends_by_itself_when_its_hours_are_up(s, book):
    target = book[("GB1", "tm_alert_investigation")]
    break_glass.open_grant(s, _person("noah"), "case", target, REASON, at=now() - timedelta(hours=5))
    s.commit()
    _as(s, "noah")
    assert target not in set(s.scalars(select(m.Case.id)))
    assert not break_glass.listing(s, _person("noah"))["mine"][0]["live"]


def test_a_customer_grant_opens_the_customer_and_their_accounts_but_not_their_cases(s, book):
    break_glass.open_grant(s, _person("noah"), "customer", "GB1", REASON)
    s.commit()
    _as(s, "noah")
    assert "GB1" in set(s.scalars(select(m.Party.id))) and "ACC-G" in set(s.scalars(select(m.Account.id)))
    assert book[("GB1", "tm_alert_investigation")] not in set(s.scalars(select(m.Case.id)))


@pytest.mark.parametrize("who,kind,target,reason,message", [
    ("rosa", "case", ("GB1", "fraud_claim"), REASON, "cannot break glass"),  # customer-facing, and no permission
    ("ana", "case", ("GB1", "fraud_claim"), REASON, "cannot break glass"),  # no permission
    ("noah", "case", ("GB1", "fraud_claim"), "need it", "at least 20 characters"),
    ("noah", "case", "NOPE", REASON, "No case with that id"),
    ("noah", "customer", "NOPE", REASON, "No customer with that id"),
    ("noah", "account", "ACC-G", REASON, "opens a case or a customer"),
    ("noah", "case", ("US1", "tm_alert_investigation"), REASON, "already see this case"),
    ("noah", "customer", "US1", REASON, "already see this customer"),
])
def test_breaking_glass_is_refused_with_a_reason(s, book, who, kind, target, reason, message):
    with pytest.raises(BreakGlassError, match=message):
        break_glass.open_grant(s, _person(who), kind, book[target] if isinstance(target, tuple) else target, reason)


def test_a_restricted_case_stays_closed_to_breaking_glass_unless_the_person_is_cleared(s, book):
    target = book[("GB1", "fraud_claim")]
    with visibility.system(s):
        _restrict(s, target)
    s.commit()
    with pytest.raises(BreakGlassError, match="Restricted cases cannot be opened"):
        break_glass.open_grant(s, _person("noah"), "case", target, REASON)
    g = break_glass.open_grant(s, _person("lena"), "case", target, REASON)  # cleared for restricted matters
    s.commit()
    _as(s, "lena")
    assert target in set(s.scalars(select(m.Case.id)))
    assert g.person == "lena"


def test_a_grant_on_a_case_that_is_restricted_later_stops_opening_it_and_its_customer(s, book):
    target = book[("GB1", "fraud_claim")]
    break_glass.open_grant(s, _person("noah"), "case", target, REASON)
    with visibility.system(s):
        _restrict(s, target)
    s.commit()
    _as(s, "noah")
    assert target not in set(s.scalars(select(m.Case.id))) and "GB1" not in set(s.scalars(select(m.Party.id)))


def test_someone_else_reviews_each_opening_and_an_unjustified_one_ends_at_once(s, book):
    g = break_glass.open_grant(s, _person("noah"), "case", book[("GB1", "tm_alert_investigation")], REASON)
    s.commit()
    with pytest.raises(BreakGlassError, match="certify access"):
        break_glass.review(s, g.id, _person("ivan"), "justified")
    with pytest.raises(BreakGlassError, match="Say what was wrong"):
        break_glass.review(s, g.id, _person("maya"), "not_justified")
    with pytest.raises(BreakGlassError, match="Outcome is one of"):
        break_glass.review(s, g.id, _person("maya"), "fine")
    assert break_glass.listing(s, _person("maya"))["to_review"][0]["id"] == g.id
    assert break_glass.listing(s, _person("ivan"))["to_review"] == []
    done = break_glass.review(s, g.id, _person("maya"), "not_justified", "Not his case and no customer contact on record")
    s.commit()
    assert done.closed is not None and done.reviewed_by == "maya"
    with pytest.raises(BreakGlassError, match="already been reviewed"):
        break_glass.review(s, g.id, _person("hugo"), "justified")
    _as(s, "noah")
    assert book[("GB1", "tm_alert_investigation")] not in set(s.scalars(select(m.Case.id)))
    mine = break_glass.listing(s, _person("noah"))["mine"][0]
    assert mine["review_label"] == "Not justified" and not mine["live"]


def test_nobody_reviews_their_own_opening(s, book):
    target = book[("ROSA1", "fraud_claim")]
    both = replace(_person("hugo"), permissions=_person("hugo").permissions | {"break_glass"}, views=_person("noah").views)
    g = break_glass.open_grant(s, both, "case", target, REASON)
    with pytest.raises(BreakGlassError, match="Someone else"):
        break_glass.review(s, g.id, both, "justified")
