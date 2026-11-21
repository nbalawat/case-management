"""The managed-service model: the provider's own staff working a client's cases, only on the client's terms.

The rules under test, in the order a client would meet them:
  * a client's policy decides whether the provider's staff may work there at all, and in which roles;
  * the provider proposes each engagement; the client's own access administrator approves or turns it down;
  * while it lasts, the member of staff is an ordinary person at the client, under every rule the client's people are;
  * it ends by either side or by the calendar, and their cases go back to the queue;
  * where the client says so, provider work needs a client checker, and only the client sends reports to the regulator;
  * nothing about it can be used to cross into another client, or to grant access control to the provider.
"""
from datetime import timedelta

import pytest
import yaml
from sqlalchemy import select

from app.cases.service import cross_signoff
from app.domain.access import PROVIDER, AccessPolicyError, parse_policy
from app.domain.models import AccessEvent, Case, CaseEvent, Engagement, Filing, now
from app.filing import service as filing
from app.tenants import access_admin, provider
from app.tenants.provider import EngagementError
from app.tenants.registry import runtime
from app.tenants.templates import templates

FINTECH = "fintech_payments"
SOURCE = templates()[FINTECH].access_source


def raw(**managed):
    r = yaml.safe_load(SOURCE)
    if managed.get("_drop"):
        r.pop("managed_service", None)
    else:
        r["managed_service"] = r["managed_service"] | managed
    return r


@pytest.fixture
def two(db):
    """A payments fintech that allows the managed service, a bank that does not, and the provider's staff."""
    db.onboard("kite", FINTECH)
    db.onboard("bank")  # the full-service template: no managed_service section
    with db.platform() as p:
        for sid, name in (("priya", "Priya Raman"), ("marcus", "Marcus Webb"), ("gone", "Former Colleague")):
            provider.add_staff(p, id=sid, name=name, title="Managed-service analyst")
        provider.staff(p, "gone").active = False
        p.commit()
    return db


def propose(db, tenant="kite", staff="priya", roles=("analyst",), queues=("aml-l1",), days=30, at=None):
    with db.platform() as p:
        e = provider.propose(p, tenant, staff, list(roles), list(queues), (at or now()) + timedelta(days=days), "olivia", "cover", at=at)
        p.commit()
        return e.id


def admin(tenant="kite"):
    return next(p for p in runtime(tenant).policy.own_people.values() if p.can("manage_access"))


def approve(db, eid, tenant="kite", ok=True, at=None):
    with db.tenant(tenant) as s:
        provider.decide(s, eid, ok, admin(tenant), "agreed", at=at)
        s.commit()


# --- the client's policy decides -----------------------------------------------------------------------------------
@pytest.mark.parametrize("change, error", [
    ({"roles": ["wizard"]}, "unknown role"),
    ({"roles": ["admin"]}, "stays with the client's own people"),  # admin holds manage_access
    ({"roles": ["mlro"]}, "stays with the client's own people"),  # certify_access
    ({"roles": []}, "must list the roles"),
    ({"approval": "maybe"}, "approval must be"),
    ({"max_days": 0}, "max_days"),
    ({"max_days": 400}, "max_days"),
    ({"max_days": True}, "max_days"),
    ({"client_signoff": ["everything"]}, "client_signoff takes"),
    ({"surprise": 1}, "managed_service takes"),
])
def test_a_policy_cannot_offer_the_provider_what_it_should_not(change, error):
    with pytest.raises(AccessPolicyError, match=error):
        parse_policy(raw(**change), "draft")


def test_ids_kept_for_provider_staff_cannot_be_used_by_a_client():
    r = raw()
    r["people"][0]["id"] = "ms.priya"
    with pytest.raises(AccessPolicyError, match="kept for the provider"):
        parse_policy(r, "draft")


def test_reports_cannot_need_client_signoff_if_nobody_at_the_client_can_send_them():
    r = raw()
    for role in r["roles"].values():
        role["permissions"] = [p for p in role["permissions"] if p != "submit_filings"]
    with pytest.raises(AccessPolicyError, match="none of them can send"):
        parse_policy(r, "draft")


def test_the_shipped_terms_are_read_as_written():
    ms = parse_policy(raw(), "x").managed_service
    assert ms.roles == {"analyst", "fraud_analyst", "investigator", "sanctions_officer"}
    assert ms.approval == "client" and ms.max_days == 180 and ms.client_signoff == {"four_eyes", "submit_filings"}
    assert parse_policy(raw(_drop=True), "x").managed_service is None


def test_a_client_whose_policy_says_nothing_cannot_be_given_provider_staff(two):
    with pytest.raises(EngagementError, match="does not allow"):
        propose(two, "bank")


@pytest.mark.parametrize("kw, error", [
    ({"roles": ("admin",)}, "only these roles"),
    ({"roles": ()}, "at least one role"),
    ({"queues": ("nowhere",)}, "unknown queue"),
    ({"roles": ("analyst",), "queues": ("mlro",)}, None),  # a queue the role cannot work in is the client's call; it is still a real queue
    ({"days": 181}, "at most 180 days"),
    ({"days": -1}, "in the future"),
    ({"staff": "gone"}, "no longer active"),
    ({"staff": "nobody"}, "No member of staff"),
])
def test_an_engagement_must_fit_the_client_terms(two, kw, error):
    if error is None:
        propose(two, **kw)
        return
    with pytest.raises(EngagementError, match=error):
        propose(two, **kw)


def test_a_queue_needs_a_role_that_works_cases(db):
    r = raw()
    r["roles"]["viewer"] = {"title": "Viewer", "permissions": ["view_team"]}
    r["managed_service"]["roles"] = ["viewer", "analyst"]
    db.onboard("kite", FINTECH, access_source=yaml.safe_dump(r))
    with db.platform() as p:
        provider.add_staff(p, id="priya", name="Priya")
        with pytest.raises(EngagementError, match="holds no role that can work cases"):
            provider.propose(p, "kite", "priya", ["viewer"], ["aml-l1"], now() + timedelta(days=5), "olivia")
        assert provider.propose(p, "kite", "priya", ["viewer"], [], now() + timedelta(days=5), "olivia")


# --- two-sided consent ---------------------------------------------------------------------------------------------
def test_a_proposed_engagement_gives_nothing_until_the_client_approves(two):
    eid = propose(two)
    assert "ms.priya" not in runtime("kite").policy.people
    approve(two, eid)
    p = runtime("kite").policy.people["ms.priya"]
    assert p.org == PROVIDER and p.external and p.engagement == eid
    assert p.roles == ("analyst",) and p.queues == ("aml-l1",) and p.permissions == {"work"}
    assert "CaseForge Managed Services" in p.title and "Priya Raman" == p.name
    assert p in runtime("kite").policy.queue_members("aml-l1")
    assert "ms.priya" not in runtime("bank").policy.people


def test_only_the_clients_own_access_administrator_can_approve(two):
    eid = propose(two)
    approve(two, propose(two, staff="marcus", roles=("investigator",), queues=("aml-l2",)))
    analyst = next(p for p in runtime("kite").policy.own_people.values() if not p.can("manage_access"))
    with two.tenant("kite") as s:
        for who in (analyst, runtime("kite").policy.people["ms.marcus"]):
            with pytest.raises(EngagementError, match="manage access"):
                provider.decide(s, eid, True, who)
    assert "ms.priya" not in runtime("kite").policy.people


def test_a_turned_down_engagement_never_starts_and_cannot_be_approved_later(two):
    eid = propose(two)
    approve(two, eid, ok=False)
    assert "ms.priya" not in runtime("kite").policy.people
    with pytest.raises(EngagementError, match="already rejected"):
        approve(two, eid)


def test_a_client_can_waive_approval(db):
    db.onboard("kite", FINTECH, access_source=yaml.safe_dump(raw(approval="none")))
    with db.platform() as p:
        provider.add_staff(p, id="priya", name="Priya")
        p.commit()
    propose(db)
    assert "ms.priya" in runtime("kite").policy.people
    with db.tenant("kite") as s:
        kinds = [e.kind for e in s.scalars(select(AccessEvent).order_by(AccessEvent.id))]
    assert kinds == ["engagement.proposed", "engagement.started"]


def test_one_open_engagement_per_person_per_client(two):
    eid = propose(two)
    with pytest.raises(EngagementError, match="already has an engagement"):
        propose(two)
    approve(two, eid)
    with pytest.raises(EngagementError, match="already has an engagement"):
        propose(two)
    propose(two, "kite", "marcus")  # someone else is fine


# --- it ends -------------------------------------------------------------------------------------------------------
def _claim_one(db, tenant="kite", pid="ms.priya"):
    from app.cases import assignments
    from app.cases.case_types import load_case_types
    from app.cases.service import create_case
    from app.domain.models import Party

    with db.tenant(tenant) as s:
        s.add(Party(id="P1", name="Customer"))
        s.flush()
        c = create_case(s, load_case_types(tenant)["tm_alert_investigation"], "P1", "medium")
        assignments.claim(s, c, runtime(tenant).policy.people[pid])
        s.commit()
        return c.id


def test_ending_an_engagement_hands_its_cases_back_to_the_queue(two):
    approve(two, propose(two))
    cid = _claim_one(two)
    with two.tenant("kite") as s:
        with pytest.raises(EngagementError, match="Say why"):
            provider.end(s, provider_engagement(s), admin().id, "client", " ")
        provider.end(s, provider_engagement(s), admin().id, "client", "Statement of work finished")
        s.commit()
    assert "ms.priya" not in runtime("kite").policy.people
    with two.tenant("kite") as s:
        c = s.get(Case, cid)
        assert c.owner is None
        handed = s.scalars(select(CaseEvent).where(CaseEvent.case_id == cid, CaseEvent.kind == "case.handed_off")).one()
        assert handed.payload["from_owner"] == "ms.priya" and "Statement of work finished" in handed.payload["reason"]
        assert s.scalars(select(Engagement)).one().status == "ended"
        with pytest.raises(EngagementError, match="already ended"):
            provider.end(s, provider_engagement(s), admin().id, "client", "again")


def provider_engagement(s):
    return s.scalars(select(Engagement)).first().id


def test_an_engagement_ends_by_itself_on_its_end_date(two):
    start = now() - timedelta(days=40)
    approve(two, propose(two, days=30, at=start), at=start)  # started 40 days ago, for 30 days
    with two.platform() as p:
        assert "ms.priya" not in provider.refresh(p, runtime("kite")).policy.people  # the runtime never keeps someone past their date
        assert provider.expire(p, "kite") == 1
        p.commit()
    with two.tenant("kite") as s:
        e = s.scalars(select(Engagement)).one()
        assert e.status == "ended" and e.ended_by == "calendar" and "end date" in e.end_reason


def test_a_person_still_holding_a_seat_is_rechecked_on_every_request(two):
    approve(two, propose(two))
    p = runtime("kite").policy.people["ms.priya"]
    with two.platform() as s:
        assert provider.still_live(s, p).ok
        s.get(provider.ProviderStaff, "priya").active = False
        s.flush()
        assert provider.still_live(s, p).reason == "Your provider account is no longer active"
        s.get(provider.ProviderStaff, "priya").active = True
        s.scalars(select(Engagement)).one().ends = now() - timedelta(minutes=1)
        s.flush()
        assert provider.still_live(s, p).reason == "Your engagement at this client has ended"


# --- the client's policy changes under an engagement ----------------------------------------------------------------
def _publish(db, r, tenant="kite"):
    r["version"] = "1.1.0"
    with db.tenant(tenant) as s:
        out = access_admin.check(s, yaml.safe_dump(r))
        access_admin.publish(s, yaml.safe_dump(r), admin(tenant), "change")
        s.commit()
    return out


def test_withdrawing_a_role_from_the_terms_ends_engagements_that_hold_it(two):
    approve(two, propose(two))
    approve(two, propose(two, staff="marcus", roles=("investigator",), queues=("aml-l2",)))
    r = raw(roles=["investigator"])
    out = _publish(two, r)
    assert any("Ends the engagement of Priya Raman" in w for w in out["warnings"])
    assert any("Managed service:" in c for c in out["changes"])
    people = runtime("kite").policy.people
    assert "ms.priya" not in people and "ms.marcus" in people


def test_removing_the_managed_service_from_a_policy_ends_every_engagement(two):
    approve(two, propose(two))
    _publish(two, raw(_drop=True))
    assert not [p for p in runtime("kite").policy.people.values() if p.external]
    with two.tenant("kite") as s:
        assert {e.status for e in s.scalars(select(Engagement))} == {"ended"}


def test_a_shorter_limit_applies_to_new_engagements_not_agreed_ones(two):
    approve(two, propose(two, days=150))
    _publish(two, raw(max_days=30))
    assert "ms.priya" in runtime("kite").policy.people


def test_publishing_keeps_provider_staff_and_their_cases(two):
    approve(two, propose(two))
    cid = _claim_one(two)
    _publish(two, raw())
    assert "ms.priya" in runtime("kite").policy.people
    with two.tenant("kite") as s:
        assert s.get(Case, cid).owner == "ms.priya"


def test_provider_staff_can_never_change_the_policy(two, monkeypatch):
    approve(two, propose(two))
    staff = runtime("kite").policy.people["ms.priya"]
    monkeypatch.setattr(type(staff), "can", lambda self, perm: True)  # even if a role somehow granted it
    with two.tenant("kite") as s, pytest.raises(AccessPolicyError, match="manage access"):
        access_admin.publish(s, SOURCE.replace("version: 1.0.0", "version: 1.2.0"), staff)


# --- client sign-off -----------------------------------------------------------------------------------------------
class _C:
    def __init__(self, tenant, drafted_by=None, last=None):
        self.tenant_id, self.drafted_by, self.data = tenant, drafted_by, {"last_actor": last}


def test_provider_work_needs_a_client_checker(two):
    approve(two, propose(two, staff="marcus", roles=("investigator",), queues=("aml-l2",)))
    approve(two, propose(two, staff="priya", roles=("investigator",), queues=("aml-l2",)))
    people = runtime("kite").policy.people
    priya, client = people["ms.priya"], next(p for p in people.values() if not p.external and "investigator" in p.roles)
    assert "Client sign-off" in cross_signoff(_C("kite", drafted_by="ms.marcus"), priya, "four_eyes")
    assert "Client sign-off" in cross_signoff(_C("kite", last="ms.marcus"), priya, "four_eyes")
    assert cross_signoff(_C("kite", drafted_by="ms.marcus"), client, "four_eyes") is None  # the client checks the provider: fine
    assert cross_signoff(_C("kite", drafted_by=client.id), priya, "four_eyes") is None  # the provider checks the client: allowed


def test_without_signoff_terms_provider_staff_check_each_other(db):
    db.onboard("kite", FINTECH, access_source=yaml.safe_dump(raw(client_signoff=[])))
    with db.platform() as p:
        provider.add_staff(p, id="priya", name="Priya")
        p.commit()
    approve(db, propose(db))
    assert cross_signoff(_C("kite", drafted_by="ms.marcus"), runtime("kite").policy.people["ms.priya"], "four_eyes") is None


def test_only_the_clients_own_officers_send_reports_to_the_regulator(two, monkeypatch):
    r = raw(roles=["analyst", "investigator", "sanctions_officer", "fraud_analyst", "filer"])
    r["roles"]["filer"] = {"title": "Report preparer", "permissions": ["work", "submit_filings"]}
    _publish(two, r)
    approve(two, propose(two, roles=("filer",), queues=()))
    staff = runtime("kite").policy.people["ms.priya"]
    with two.tenant("kite") as s:
        s.add(Filing(id="sar-1", kind="SAR", filed_by="x", sha256="0" * 64, status="prepared"))
        s.flush()
        with pytest.raises(filing.FilingError, match="client's own officers"):
            filing.submit(s, "sar-1", staff)


# --- the record ----------------------------------------------------------------------------------------------------
def test_every_change_to_an_engagement_is_on_the_clients_record(two):
    eid = propose(two)
    approve(two, eid)
    with two.tenant("kite") as s:
        provider.end(s, eid, "olivia", "provider", "rotation")
        s.commit()
        rows = [(e.kind, e.org) for e in s.scalars(select(AccessEvent).order_by(AccessEvent.id))]
    assert rows == [("engagement.proposed", "provider"), ("engagement.approved", "client"), ("engagement.ended", "provider")]
    with two.tenant("bank") as s:
        assert s.scalars(select(AccessEvent)).first() is None  # another client's record holds none of it
