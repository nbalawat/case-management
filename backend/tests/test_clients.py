"""Onboarding clients and keeping each client's configuration its own."""
import pytest
import yaml
from sqlalchemy import func, select

from app.cases import publishing
from app.cases.case_types import CaseTypeError, library, load_case_types, versions
from app.cases.service import complete_stage_tasks, create_case, transition
from app.domain import models as m
from app.domain.access import parse_policy
from app.seed import PROFILES, seed_tenant
from app.tenancy import SCOPED, TenantError
from app.tenants import access_admin, onboarding, registry
from app.tenants.onboarding import OnboardingError
from app.tenants.templates import templates

TEMPLATES = sorted(templates())


def _bump(source: str, version: str = "1.1.0") -> str:
    assert "version: 1.0.0" in source
    return source.replace("version: 1.0.0", f"version: {version}")


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_is_a_complete_working_configuration(template):
    plan = onboarding.preview(template)
    policy = plan["policy"]
    assert any(p.can("manage_access") for p in policy.people.values()) and any(p.can("publish_workflow") for p in policy.people.values())
    assert any(p.read_only for p in policy.people.values()), "every template ships an independent read-only reviewer"
    for ct in plan["definitions"]:
        for t in ct["transitions"]:
            assert any(p.holds(t["roles"]) for p in policy.people.values()), f"{template}: nobody can '{t['label']}'"
        four_eyes = [t for t in ct["transitions"] if t.get("four_eyes")]
        for t in four_eyes:  # a second person must exist for every controlled step
            assert len({p.id for p in policy.people.values() if p.holds(t["roles"])}
                       | {p.id for x in ct["transitions"] if x["to"] == t["from"] for p in policy.people.values() if p.holds(x["roles"])}) >= 2


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_client_can_be_onboarded_and_worked_from_each_template(db, template):
    db.onboard("newco", template)
    with db.tenant("newco") as s:
        out = seed_tenant(s, PROFILES["starter"])  # the seed takes every step as one of the client's own people
        s.commit()
        assert out["cases"] > 5 and s.scalar(select(func.count()).select_from(m.Case).where(m.Case.closed.is_not(None))) > 0
        people = set(registry.runtime("newco").policy.people)
        actors = set(s.scalars(select(m.CaseEvent.actor).where(m.CaseEvent.actor_kind == "human").distinct()))
        assert actors and actors <= people, f"steps taken by people outside the client: {actors - people}"
        assert set(s.scalars(select(m.Case.case_type).distinct())) <= set(registry.runtime("newco").case_types)


def test_ten_clients_run_side_by_side_without_touching_each_other(db):
    ids = [f"client-{i:02d}" for i in range(10)]
    expected = {}
    for i, tid in enumerate(ids):
        db.onboard(tid, TEMPLATES[i % len(TEMPLATES)])
        with db.tenant(tid) as s:
            expected[tid] = seed_tenant(s, PROFILES["starter"])
            s.commit()
    for tid in ids:
        with db.tenant(tid) as s:
            assert s.scalar(select(func.count()).select_from(m.Case)) == expected[tid]["cases"]
            assert s.scalar(select(func.count()).select_from(m.Transaction)) == expected[tid]["transactions"]
            assert s.scalar(select(func.count()).select_from(m.Party)) == expected[tid]["parties"]
            ids_here = set(s.scalars(select(m.Case.id)))
            assert set(s.scalars(select(m.CaseEvent.case_id).distinct())) <= ids_here  # no audit event points at another client's case
            assert {a for a in s.scalars(select(m.Alert.case_id)) if a} <= ids_here
    with db.platform() as p:
        assert p.scalar(select(func.count()).select_from(m.Case)) == sum(e["cases"] for e in expected.values())
        for mp in m.Base.registry.mappers:
            if issubclass(mp.class_, SCOPED):
                assert set(p.scalars(select(mp.class_.tenant_id).distinct())) <= set(ids)
        owners = {c.tenant_id: c.owner for c in p.scalars(select(m.Case).where(m.Case.owner.is_not(None)))}
        for tid, owner in owners.items():
            assert owner in registry.runtime(tid).policy.people


@pytest.mark.parametrize("kw,message", [
    (dict(tenant_id="Bad Id"), "Client id"),
    (dict(tenant_id="ab"), "Client id"),
    (dict(tenant_id="platform"), "Client id"),
    (dict(name="  "), "name is required"),
    (dict(template="nope"), "Unknown template"),
    (dict(deployment="moon"), "Deployment"),
    (dict(case_types=[]), "at least one case type"),
    (dict(case_types=["made_up"]), "unknown case type"),
    (dict(template="fintech_payments", case_types=["kyc_onboarding"]), "unknown queue 'kyc'"),
    (dict(template="private_bank", case_types=["fraud_claim"]), "unknown queue 'fraud'"),
    (dict(access_source="people: ["), "not valid YAML"),
    (dict(access_source="roles: {}"), "missing"),
])
def test_onboarding_refuses_a_bad_request_and_leaves_nothing_behind(db, kw, message):
    args = dict(tenant_id="newco", name="New Co", template="full_service_bank") | kw
    with db.platform() as p:
        with pytest.raises(OnboardingError, match=message):
            onboarding.onboard(p, **args)
        p.rollback()
        assert p.scalar(select(func.count()).select_from(m.Tenant)) == 0
        assert p.scalar(select(func.count()).select_from(m.AccessPolicyVersion)) == 0
    assert registry.all_runtimes() == []


def test_a_client_id_is_used_once_and_onboarding_needs_a_platform_session(db):
    db.onboard("newco")
    with db.platform() as p:
        with pytest.raises(OnboardingError, match="already exists"):
            onboarding.onboard(p, tenant_id="newco", name="Again", template="private_bank")
    with db.tenant("newco") as s:
        for call in (lambda: onboarding.onboard(s, tenant_id="other", name="Other", template="private_bank"),
                     lambda: onboarding.hydrate(s), lambda: onboarding.set_status(s, "newco", "suspended")):
            with pytest.raises(TenantError):
                call()


def test_a_template_change_never_reaches_an_existing_client(db, monkeypatch):
    db.onboard("newco", "private_bank")
    before = registry.runtime("newco").policy
    changed = yaml.safe_load(templates()["private_bank"].access_source)
    changed["people"] = changed["people"][:-1]
    monkeypatch.setitem(templates(), "private_bank", templates()["private_bank"].__class__(
        "private_bank", "Private bank", "", templates()["private_bank"].case_types, yaml.safe_dump(changed)))
    with db.platform() as p:
        onboarding.hydrate(p)
    assert registry.runtime("newco").policy == before


def test_a_workflow_version_belongs_to_the_client_that_published_it(db):
    db.onboard("alpha"), db.onboard("bravo")
    src = library()["tm_alert_investigation"]["source"].replace("version: 4.0.0", "version: 4.1.0")
    with db.tenant("alpha") as s:
        publishing.publish(s, src, registry.runtime("alpha").policy.people["omar"], "alpha only")
        s.commit()
        assert load_case_types("alpha")["tm_alert_investigation"]["version"] == "4.1.0"
    assert load_case_types("bravo")["tm_alert_investigation"]["version"] == "4.0.0"
    with db.tenant("bravo") as s:
        assert s.scalar(select(func.count()).select_from(m.WorkflowVersion)) == 0
        s.add_all([m.Party(id="P1", name="x"), m.Account(id="A1", party_id="P1")])
        s.flush()
        assert create_case(s, load_case_types("bravo")["tm_alert_investigation"], "P1", "low").type_version == "4.0.0"
        publishing.publish(s, src, registry.runtime("bravo").policy.people["omar"])  # the same number is free at another client
        s.commit()
    registry.reset()
    with db.platform() as p:
        onboarding.hydrate(p)  # a restart gives each client back exactly its own versions
    assert {t: [v["version"] for v in versions(t, "tm_alert_investigation")]
            for t in ("alpha", "bravo")} == {"alpha": ["4.0.0", "4.1.0"], "bravo": ["4.0.0", "4.1.0"]}


def test_a_workflow_is_checked_against_the_publishing_clients_own_policy(db):
    db.onboard("bank"), db.onboard("fintech", "fintech_payments")
    src = library()["kyc_refresh"]["source"].replace("version: 3.0.0", "version: 3.1.0")
    with db.tenant("fintech") as s:
        with pytest.raises(CaseTypeError, match="unknown queue 'kyc'"):  # this client has no KYC team
            publishing.publish(s, src, registry.runtime("fintech").policy.people["pat"])
    with db.tenant("bank") as s:
        publishing.publish(s, src, registry.runtime("bank").policy.people["omar"])


def test_an_access_policy_change_stays_with_its_client(db):
    db.onboard("alpha"), db.onboard("bravo")
    raw = yaml.safe_load(templates()["full_service_bank"].access_source)
    raw["version"] = "1.1.0"
    raw["roles"]["investigator"]["permissions"].append("reassign")
    raw["people"].append({"id": "zoe", "name": "Zoe Hart", "role": "investigator", "queues": ["aml-l2"]})
    with db.tenant("alpha") as s:
        access_admin.publish(s, yaml.safe_dump(raw), registry.runtime("alpha").policy.people["omar"], "third investigator")
        s.commit()
    a, b = registry.runtime("alpha").policy, registry.runtime("bravo").policy
    assert a.version == "1.1.0" and "zoe" in a.people and a.people["ivan"].can("reassign")
    assert b.version == "1.0.0" and "zoe" not in b.people and not b.people["ivan"].can("reassign")
    registry.reset()
    with db.platform() as p:
        onboarding.hydrate(p)
    assert registry.runtime("alpha").policy == a and registry.runtime("bravo").policy == b
    with db.tenant("bravo") as s:
        assert [h["version"] for h in access_admin.history(s)] == ["1.0.0"]
    with db.tenant("alpha") as s:
        assert [(h["version"], h["active"]) for h in access_admin.history(s)] == [("1.1.0", True), ("1.0.0", False)]


def test_case_types_can_be_switched_per_client_but_not_from_under_open_cases(db):
    db.onboard("alpha"), db.onboard("bravo")
    with db.platform() as p:
        onboarding.set_case_types(p, "alpha", ["tm_alert_investigation", "sanctions_hit"])
        with pytest.raises(OnboardingError, match="Open cases still use: fraud_claim"):
            onboarding.set_case_types(p, "bravo", ["tm_alert_investigation"], in_use={"fraud_claim"})
        with pytest.raises(OnboardingError, match="unknown case type"):
            onboarding.set_case_types(p, "bravo", ["nope"])
        p.commit()
    assert set(load_case_types("alpha")) == {"tm_alert_investigation", "sanctions_hit"} and len(load_case_types("bravo")) == 6
    with db.platform() as p:
        onboarding.hydrate(p)
    assert set(load_case_types("alpha")) == {"tm_alert_investigation", "sanctions_hit"}


def test_qa_sampling_follows_the_clients_own_case_types(db, monkeypatch):
    """The fintech template has no QA case type, so closing a case there samples nothing; the bank samples."""
    monkeypatch.setattr("app.cases.qa.sampled", lambda case_id, rate: True)
    db.onboard("bank"), db.onboard("fintech", "fintech_payments")
    for tid, who in (("bank", "ana"), ("fintech", "nia")):
        with db.tenant(tid) as s:
            s.add_all([m.Party(id="P1", name="x"), m.Account(id="A1", party_id="P1")])
            s.flush()
            c = create_case(s, load_case_types(tid)["tm_alert_investigation"], "P1", "low")
            person = registry.runtime(tid).policy.people[who]
            complete_stage_tasks(s, c, person)
            transition(s, c, "closed_no_sar", person, "documented", reason="explained_by_documents")
            s.flush()
            assert s.scalar(select(func.count()).select_from(m.Case).where(m.Case.parent_id == c.id)) == (1 if tid == "bank" else 0)


def test_unknown_client_is_an_error_not_a_default():
    with pytest.raises(registry.UnknownTenant):
        registry.runtime("ghost")
    with pytest.raises(registry.UnknownTenant):
        load_case_types("ghost")
    assert parse_policy(yaml.safe_load(templates()["fintech_payments"].access_source)).people["nia"].roles == ("fraud_analyst", "analyst")
