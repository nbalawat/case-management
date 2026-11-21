"""Access control is configuration, per client. These tests hold for every template and for any policy a client publishes."""
import pytest
import yaml
from sqlalchemy import select

from app.cases import assignments
from app.cases.case_types import library, load_case_types
from app.cases.service import TransitionError, allowed_transitions, create_case, transition
from app.domain import models as m
from app.domain.access import AccessPolicyError, parse_policy
from app.tenants import access_admin, onboarding, registry
from app.tenants.templates import templates

MATRIX = [(tpl.id, name, i) for tpl in templates().values() for name in tpl.case_types
          for i, _ in enumerate(library()[name]["transitions"])]


def _policy(template: str) -> dict:
    return yaml.safe_load(templates()[template].access_source)


def _case(s, tenant: str, case_type: str = "tm_alert_investigation", state: str | None = None, pid: str = "P1"):
    if s.get(m.Party, {"tenant_id": tenant, "id": pid}) is None:
        s.add_all([m.Party(id=pid, name="Test Person"), m.Account(id=f"A-{pid}", party_id=pid)])
        s.flush()
    ct = load_case_types(tenant)[case_type]
    c = create_case(s, ct, pid, "medium")
    if state:
        c.state, c.queue = state, ct["states"][state].get("queue")
    s.flush()
    return c


@pytest.mark.parametrize("template,case_type,index", MATRIX, ids=[f"{a}:{b}#{i}" for a, b, i in MATRIX])
def test_at_every_client_only_holders_of_a_named_role_can_take_a_step(db, template, case_type, index):
    db.onboard("client", template)
    policy = registry.runtime("client").policy
    step = library()[case_type]["transitions"][index]
    with db.tenant("client") as s:
        c = _case(s, "client", case_type, step["from"])
        for person in policy.people.values():
            offered = [t for t in allowed_transitions(s, c, person) if t["to"] == step["to"]]
            may = person.holds(step["roles"]) and not person.read_only
            for t in offered:
                role_refusal = bool(t["blocked"]) and (t["blocked"].startswith("Needs role") or t["blocked"] == "Read-only access")
                assert role_refusal == (not may), f"{person.id} on '{step['label']}': {t['blocked']}"
            if not may:
                with pytest.raises(TransitionError):
                    transition(s, c, step["to"], person, "note")
                assert c.state == step["from"]


@pytest.mark.parametrize("template", sorted(templates()))
def test_claiming_follows_the_clients_own_queues(db, template):
    db.onboard("client", template)
    policy = registry.runtime("client").policy
    with db.tenant("client") as s:
        for n, (name, ct) in enumerate(load_case_types("client").items()):
            c = _case(s, "client", name, pid=f"P{n}")
            for person in policy.people.values():
                assert assignments.can_claim(c, person) == (c.queue in person.queues and person.can("work"))


def test_a_person_with_two_roles_can_do_both_jobs_but_never_approve_their_own_work(db):
    db.onboard("fintech", "fintech_payments")
    people = registry.runtime("fintech").policy.people
    joy, sam, ravi = people["joy"], people["sam"], people["ravi"]
    assert joy.roles == ("mlro", "team_lead") and joy.permissions >= {"work", "view_team", "reassign", "run_timers"}
    with db.tenant("fintech") as s:
        fraud = _case(s, "fintech", "fraud_claim", "refund_approval")
        fraud.data = {"last_actor": "nia"}
        transition(s, fraud, "refunded", joy, "approved as operations lead")  # team_lead step
        sar = _case(s, "fintech", "tm_alert_investigation", "mlro_review", pid="P2")
        sar.narrative, sar.drafted_by, sar.data = "text", "joy", {"last_actor": "ravi"}
        with pytest.raises(TransitionError, match="Four-eyes"):
            transition(s, sar, "filed", joy)  # mlro step, but joy drafted it
        sar.drafted_by = "ravi"
        transition(s, sar, "filed", joy)
        sanctions = _case(s, "fintech", "sanctions_hit", "escalated", pid="P3")
        sanctions.data = {"last_actor": "ravi"}
        with pytest.raises(TransitionError, match="Four-eyes"):
            transition(s, sanctions, "blocked", ravi, "x")  # ravi holds the role twice over, and still needs a second person
        with pytest.raises(TransitionError, match="Needs role"):
            transition(s, sanctions, "blocked", sam, "x")


def test_a_custom_read_only_role_can_look_and_never_change(db):
    db.onboard("alder", "private_bank")
    gwen = registry.runtime("alder").policy.people["gwen"]
    assert gwen.read_only and gwen.roles == ("relationship_manager",) and not gwen.can("view_team")
    with db.tenant("alder") as s:
        c = _case(s, "alder", "kyc_onboarding")
        assert all(t["blocked"] == "Read-only access" for t in allowed_transitions(s, c, gwen))
        assert not assignments.can_claim(c, gwen)


# --- changing a client's policy ---------------------------------------------------
def _draft(template="full_service_bank", version="1.1.0", edit=lambda raw: None) -> str:
    raw = _policy(template)
    raw["version"] = version
    edit(raw)
    return yaml.safe_dump(raw, sort_keys=False)


def _drop(raw, pid):
    raw["people"] = [p for p in raw["people"] if p["id"] != pid]


@pytest.mark.parametrize("edit,message", [
    (lambda r: r.update(version="1.0.0"), "version must be higher"),
    (lambda r: r.update(version="0.9"), "version must be higher"),
    (lambda r: r["roles"]["admin"]["permissions"].remove("manage_access"), "could never be changed again"),
    (lambda r: _drop(r, "omar"), "could never be changed again"),
    (lambda r: _drop(r, "quinn"), "nobody in queue 'qa'"),
    (lambda r: _drop(r, "hugo"), "nobody in queue 'senior-approval'"),
    (lambda r: [p.update(queues=[]) for p in r["people"] if p["id"] == "kai"], "nobody in queue 'kyc'"),
    (lambda r: r["queues"].pop("fraud"), "unknown queue"),
    (lambda r: r["roles"].pop("head_fiu"), "unknown role"),
    (lambda r: r["roles"]["auditor"]["permissions"].append("work"), "read-only"),
    (lambda r: r["people"][0].update(roles=["analyst", "auditor"]), "combines a read-only role"),
    (lambda r: r["people"][0].pop("role"), "needs a role"),
    (lambda r: r["roles"]["analyst"]["permissions"].append("delete_everything"), "unknown permission"),
])
def test_a_policy_that_would_break_the_client_is_refused(s, edit, message):
    with pytest.raises(AccessPolicyError, match=message):
        access_admin.check(s, _draft(edit=edit))


def test_invalid_yaml_is_refused_with_a_reason(s):
    with pytest.raises(AccessPolicyError, match="not valid YAML"):
        access_admin.check(s, "people: [")
    with pytest.raises(AccessPolicyError, match="must be a YAML mapping"):
        access_admin.check(s, "- just\n- a list")


def test_a_policy_must_still_serve_open_cases_on_older_workflow_versions(s):
    """A newer workflow no longer needs the head of FIU, but a case opened under the old one still does."""
    from app.cases import publishing
    old = _case(s, "meridian", state="senior_approval")
    raw = yaml.safe_load(library()["tm_alert_investigation"]["source"])
    raw["version"] = "5.0.0"
    del raw["states"]["senior_approval"]
    raw["transitions"] = [t for t in raw["transitions"] if "senior_approval" not in (t["from"], t["to"])]
    src = yaml.safe_dump(raw, sort_keys=False)
    publishing.publish(s, src, registry.runtime("meridian").policy.people["omar"])
    with pytest.raises(AccessPolicyError, match="4.0.0.*senior-approval"):
        access_admin.check(s, _draft(edit=lambda r: _drop(r, "hugo")))
    old.closed = old.created  # once no open case is pinned to the old version, the change is allowed
    s.flush()
    assert access_admin.check(s, _draft(edit=lambda r: _drop(r, "hugo")))["changes"] == ["Removes Hugo Laurent (Head of FIU)"]


def test_publishing_needs_the_permission_and_describes_what_changes(s):
    people = registry.runtime("meridian").policy.people

    def edit(r):
        r["roles"]["investigator"]["permissions"].append("reassign")
        r["roles"]["mlro"]["permissions"].remove("view_team")
        r["roles"]["trainee"] = {"title": "Trainee analyst", "permissions": ["work"]}
        r["queues"]["aml-vip"] = "Private client alerts"
        r["people"].append({"id": "zoe", "name": "Zoe Hart", "roles": ["trainee", "analyst"], "queues": ["aml-l1"]})
        next(p for p in r["people"] if p["id"] == "lena").update(queues=["aml-l2", "aml-vip"])
        next(p for p in r["people"] if p["id"] == "sofia").update(queues=["sanctions-l2"])
        _drop(r, "audra")

    for who in people.values():
        if not who.can("manage_access"):
            with pytest.raises(AccessPolicyError, match="manage access"):
                access_admin.publish(s, _draft(edit=edit), who)
    result = access_admin.publish(s, _draft(edit=edit), people["omar"], "spring reorganisation")
    assert set(result["changes"]) == {
        "'L2 investigator' gains: reassign", "'MLRO / BSA officer' loses: view team", "Adds role 'Trainee analyst'",
        "Adds queue 'Private client alerts'", "Adds Zoe Hart (Trainee analyst)", "Lena Fischer joins: Private client alerts",
        "Sofia Marin leaves: Sanctions adjudication", "Removes Audra Klein (Internal auditor)"}
    now = registry.runtime("meridian").policy
    assert now.version == "1.1.0" and now.people["ivan"].can("reassign") and not now.people["maya"].can("view_team")
    assert "audra" not in now.people and now.people["zoe"].holds(["analyst"])
    assert access_admin.active(s).note == "spring reorganisation" and access_admin.active(s).published_by == "omar"
    with pytest.raises(AccessPolicyError, match="higher"):
        access_admin.publish(s, _draft(edit=edit), now.people["omar"])


def test_the_new_policy_takes_effect_on_the_next_action(s):
    c = _case(s, "meridian")  # triage, aml-l1
    before = registry.runtime("meridian").policy.people
    assert not assignments.can_claim(c, before["kai"])
    with pytest.raises(TransitionError, match="Needs role"):
        transition(s, c, "investigation", before["kai"])
    access_admin.publish(s, _draft(edit=lambda r: next(p for p in r["people"] if p["id"] == "kai").update(
        roles=["kyc_analyst", "analyst"], queues=["kyc", "aml-l1"])), before["omar"])
    kai = registry.runtime("meridian").policy.people["kai"]
    assert assignments.can_claim(c, kai)
    assert [t["blocked"] for t in allowed_transitions(s, c, kai) if t["to"] == "investigation"][0].startswith("Finish required task")


def test_cases_owned_by_someone_who_leaves_a_queue_go_back_to_the_queue(s):
    people = registry.runtime("meridian").policy.people
    mine, other, closed = (_case(s, "meridian", state="investigation", pid=f"P{i}") for i in range(3))
    assignments.claim(s, mine, people["lena"])
    assignments.claim(s, other, people["ivan"])
    assignments.claim(s, closed, people["lena"])
    closed.closed = closed.created
    s.flush()
    draft = _draft(edit=lambda r: _drop(r, "lena"))
    assert access_admin.check(s, draft)["warnings"] == ["1 open case(s) go back to their queue because the owner is no longer a member"]
    access_admin.publish(s, draft, people["omar"])
    assert (mine.owner, other.owner, closed.owner) == (None, "ivan", "lena")
    event = s.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == mine.id, m.CaseEvent.kind == "case.handed_off")).one()
    assert event.payload == {"from_owner": "lena", "queue": "aml-l2", "reason": "access policy 1.1.0"}


def test_a_check_changes_nothing(s):
    before = registry.runtime("meridian").policy
    access_admin.check(s, _draft(edit=lambda r: _drop(r, "audra")))
    assert registry.runtime("meridian").policy is before and len(access_admin.history(s)) == 1


def test_every_shipped_policy_parses_and_names_only_known_permissions():
    known = set(parse_policy(_policy("full_service_bank")).permissions)
    for tpl in templates().values():
        policy = parse_policy(yaml.safe_load(tpl.access_source), tpl.id)
        assert set(policy.permissions) == known, f"{tpl.id} drifts from the permission vocabulary"
        assert onboarding.preview(tpl.id)["case_types"] == list(tpl.case_types)
