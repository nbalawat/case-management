"""Tenant isolation in the data layer. These tests are the contract: no client can read or write another's rows."""
from datetime import datetime

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import aliased

from app.alerts.hub import ingest_external_alert
from app.db import Base, session_scope
from app.domain import models as m
from app.ai.usage import ModelUsage
from app.views.store import GridSetting, SavedView
from app.notifications.store import Notification
from app.public import models as pm
from app.tenancy import SCOPED, TenantError, tenant_of

A, B = "alpha", "bravo"
NOW = datetime(2026, 5, 1, 9)
SCOPED_MODELS = sorted((mp.class_ for mp in Base.registry.mappers if issubclass(mp.class_, SCOPED)), key=lambda c: c.__name__)

# One way to build a valid row of every tenant-scoped table. A new table must be added here, or the suite fails.
FACTORY = {
    "Party": lambda k: m.Party(id="P1", name=f"Person {k}"),
    "PartyIdentifier": lambda k: m.PartyIdentifier(id=f"pid-{k}", party_id="P1", kind="ssn", value="900-10-0001", normalized="900100001"),
    "PartyEvent": lambda k: m.PartyEvent(id=f"pev-{k}", party_id="P1", by="x", kind="identity.updated"),
    "Account": lambda k: m.Account(id="A1", party_id="P1"),
    "Transaction": lambda k: m.Transaction(id="T1", account_id="A1", party_id="P1", ts=NOW, amount=10, direction="in", channel="cash"),
    "Alert": lambda k: m.Alert(id=f"alr-{k}", dedupe_key="same-key", scenario="structuring", scenario_version="1", party_id="P1",
                               score=0.5, explanation="x"),
    "Case": lambda k: m.Case(id=f"case-{k}", case_type="tm_alert_investigation", type_version="4.0.0", state="triage", party_id="P1"),
    "CaseEvent": lambda k: m.CaseEvent(case_id=f"case-{k}", actor="x", actor_kind="system", kind="note"),
    "AgentRun": lambda k: m.AgentRun(id=f"run-{k}", case_id=f"case-{k}"),
    "Feed": lambda k: m.Feed(id=f"feed-{k}", name="f", source_system="core"),
    "CodeList": lambda k: m.CodeList(id="disposition_reasons", codes=[{"code": k, "label": k}], updated_by="x"),
    "OwnershipStructure": lambda k: m.OwnershipStructure(id=f"own-{k}", party_id="P1", recorded_by="x"),
    "RiskAssessment": lambda k: m.RiskAssessment(id=f"risk-{k}", party_id="P1", by="x", score=1, band="low", final_band="low"),
    "Evidence": lambda k: m.Evidence(id=f"ev-{k}", party_id="P1", kind="passport", title="t", filename="f", media_type="text/plain", size=1, sha256="0" * 64,
                                     key=f"k-{k}", uploaded_by="x"),
    "KycTrigger": lambda k: m.KycTrigger(id=f"trg-{k}", party_id="P1", kind="document_expired", key="same key", outcome="noted"),
    "Watchlist": lambda k: m.Watchlist(id=f"wl-{k}", code="same_code", name="n"),
    "WatchlistEntry": lambda k: m.WatchlistEntry(id=f"wle-{k}", list_id=f"wl-{k}", ref="R1", name="n"),
    "ScreeningExemption": lambda k: m.ScreeningExemption(id=f"exm-{k}", subject="n", subject_key="n", entry_id=f"wle-{k}", proposed_by="x"),
    "Hold": lambda k: m.Hold(id=f"hld-{k}", case_id=f"case-{k}", party_id="P1", target="card", reason_code="suspected_fraud", placed_by="x"),
    "Dispute": lambda k: m.Dispute(id=f"dsp-{k}", case_id=f"case-{k}", party_id="P1", amount=1.0, rail="card", reason_code="unauthorised", opened_by="x"),
    "ScenarioVersion": lambda k: m.ScenarioVersion(id=f"scv-{k}", scenario="structuring", number=1, proposed_by="x"),
    "StreamJobRun": lambda k: m.StreamJobRun(id=f"sjr-{k}", job="structuring", action="started", by="x"),
    "Lookback": lambda k: m.Lookback(id=f"lbk-{k}", name="n", scenario="structuring", period_from=NOW, period_to=NOW, created_by="x"),
    "OutboxEvent": lambda k: m.OutboxEvent(event_id=f"ev-{k}", topic="t", key="same key", type="x", payload="{}"),
    "StreamStat": lambda k: m.StreamStat(stream="transactions"),
    "Notification": lambda k: Notification(id=f"ntf-{k}", person="x", kind="case.assigned", title="t"),
    "GridSetting": lambda k: GridSetting(id=f"gs-{k}", person="x", grid="cases", name="density", value="compact"),
    "SavedView": lambda k: SavedView(id=f"view-{k}", owner="x", grid="cases", name="Mine", query="state=open"),
    "ModelUsage": lambda k: ModelUsage(purpose="ask", endpoint="claude", model="claude-opus-5-5", input_tokens=10, output_tokens=2),
    "AccessReview": lambda k: m.AccessReview(id=f"acr-{k}", name="n", started_by="x", due=NOW),
    "InfoShare": lambda k: m.InfoShare(id=f"lea-{k}", reference="same reference", due=NOW, loaded_by="x"),
    "Filing": lambda k: m.Filing(id=f"sar-{k}", case_id=f"case-{k}", kind="SAR", filed_by="x", sha256="0" * 64),
    "InfoRequest": lambda k: m.InfoRequest(id=f"rfi-{k}", case_id=f"case-{k}", recipient="customer", question="q", raised_by="x", due=NOW),
    "FormVersion": lambda k: m.FormVersion(form_id="sar_decision", version="9.0", source="x", published_by="x"),
    "FormSubmission": lambda k: m.FormSubmission(id=f"frm-{k}", case_id="same case", form_id="sar_decision", form_version="1.0", updated_by="x"),
    "DecisionVersion": lambda k: m.DecisionVersion(table_id="case_priority", version="9.0", source="x", published_by="x"),
    "WorkflowVersion": lambda k: m.WorkflowVersion(case_type="tm_alert_investigation", version="9.0.0", source="x", published_by="x"),
    "WorkflowDraft": lambda k: m.WorkflowDraft(id=f"wfd-{k}", case_type="same_key", title="t", design={}, created_by="x", updated_by="x"),
    "AccessPolicyVersion": lambda k: m.AccessPolicyVersion(version="9.0.0", source="x", published_by="x"),
    "DataSource": lambda k: m.DataSource(id=f"src-{k}", name="same name", dataset="transactions", root="lake", created_by="x"),
    "PipelineRun": lambda k: m.PipelineRun(id=f"run-{k}", source_id=f"src-{k}", batch_id="b", started_by="x"),
    "IngestedObject": lambda k: m.IngestedObject(id=f"obj-{k}", source_id=f"src-{k}", run_id=f"run-{k}", key="same/key.csv",
                                                 fingerprint="same", status="loaded"),
    "AgentPolicyVersion": lambda k: m.AgentPolicyVersion(version=9, body={}, published_by="x"),
    "AgentStep": lambda k: m.AgentStep(run_id=f"run-{k}", agent="triage", agent_version="1", level="L2"),
    "AgentJob": lambda k: m.AgentJob(id=f"job-{k}", kind="evaluation", started_by="x"),
    "MediaItem": lambda k: m.MediaItem(id=f"med-{k}", party_id="P1", source="s", title="t", body="b"),
    "Entity": lambda k: m.Entity(id=f"ent-{k}", kind="counterparty", name="Acme", key="acme"),
    "EntityEdge": lambda k: m.EntityEdge(party_id="P1", entity_id=f"ent-{k}", first=NOW, last=NOW),
    "MatchCandidate": lambda k: m.MatchCandidate(id=f"mc-{k}", kind="counterparty_variant", left_key="same a", right_key="same b",
                                                 left_name="A", right_name="B", score=0.9),
    "AskLog": lambda k: m.AskLog(id=f"q-{k}", conversation_id=f"ask-{k}", asked_by="x", role="analyst", question="q"),
    "ApiClient": lambda k: pm.ApiClient(id=f"cfc_{k}", name="App", roles=["analyst"], scopes=["cases:read"], created_by="x"),
    "ApiClientSecret": lambda k: pm.ApiClientSecret(id=f"sec-{k}", client_id=f"cfc_{k}", digest=f"d-{k}", hint="abcd"),
    "ApiToken": lambda k: pm.ApiToken(id=f"tok-{k}", digest=f"t-{k}", client_id=f"cfc_{k}", expires=NOW),
    "ApiIdempotency": lambda k: pm.ApiIdempotency(caller="app:x", key="same-key", fingerprint="f"),
    "ApiCall": lambda k: pm.ApiCall(request_id="r", caller="app:x", caller_kind="app", method="GET", route="/cases", status=200),
    "WebhookEndpoint": lambda k: pm.WebhookEndpoint(id=f"whk-{k}", client_id="app:x", url="https://example.com", secret="s", created_by="x"),
    "WebhookDelivery": lambda k: pm.WebhookDelivery(id=f"msg-{k}", endpoint_id=f"whk-{k}", event_id="same-event", event_type="t", payload="{}"),
    "Engagement": lambda k: m.Engagement(id=f"eng-{k}", staff_id="priya", roles=["analyst"], ends=NOW, proposed_by="x"),
    "AccessGrant": lambda k: m.AccessGrant(id=f"brk-{k}", person="x", kind="case", target=f"case-{k}", reason="r", ends=NOW),
    "AccessEvent": lambda k: m.AccessEvent(actor="x", org="provider", kind="provider.request"),
}
# Tables that hold no client data at all: the client list itself and the provider's own staff directory.
PLATFORM_TABLES = {"Tenant", "ProviderStaff"}
ORDER = ["Party", "Account", "Transaction", "Case", "Alert"]  # parents first; the rest in any order


def _names():
    return ORDER + [c.__name__ for c in SCOPED_MODELS if c.__name__ not in ORDER]


@pytest.fixture
def two(db):
    """Two clients, each holding one row of every scoped table, built from the same business ids."""
    for tid in (A, B):
        db.onboard(tid)
        with db.tenant(tid) as s:
            for name in _names():
                s.add(FACTORY[name](tid))
                s.flush()
            s.commit()
    return db


def test_every_business_table_is_tenant_scoped():
    unscoped = {mp.class_.__name__ for mp in Base.registry.mappers if not issubclass(mp.class_, SCOPED)}
    assert unscoped == PLATFORM_TABLES, f"tables without a tenant: {unscoped - PLATFORM_TABLES}"
    for cls in SCOPED_MODELS:
        col = cls.__table__.c.tenant_id
        assert not col.nullable and (col.index or col.primary_key), f"{cls.__name__}.tenant_id must be required and indexed"
    assert {c.__name__ for c in SCOPED_MODELS} == set(FACTORY), "add new scoped tables to FACTORY so they are isolation-tested"


@pytest.mark.parametrize("model", SCOPED_MODELS, ids=lambda c: c.__name__)
def test_a_client_sees_only_its_own_rows(two, model):
    with two.platform() as p:
        assert p.scalar(select(func.count()).select_from(model)) >= 2  # both clients really have rows
    for tid, other in ((A, B), (B, A)):
        with two.tenant(tid) as s:
            rows = list(s.scalars(select(model)))
            assert rows and {r.tenant_id for r in rows} == {tid}
            assert s.scalar(select(func.count()).select_from(model)) == len(rows)
            assert s.scalars(select(model).where(model.tenant_id == other)).first() is None
            assert list(s.scalars(select(model.tenant_id).distinct())) == [tid]


@pytest.mark.parametrize("model", SCOPED_MODELS, ids=lambda c: c.__name__)
def test_another_clients_primary_key_finds_nothing(two, model):
    with two.platform() as p:
        theirs = p.scalars(select(model).where(model.tenant_id == B)).first()
        key = p.identity_key(instance=theirs)[1]
    with two.tenant(A) as s:
        found = s.get(model, key)
        assert found is None or found.tenant_id == A  # ids that both clients use resolve to the caller's own row


@pytest.mark.parametrize("model", SCOPED_MODELS, ids=lambda c: c.__name__)
def test_bulk_update_and_delete_stay_inside_the_client(two, model):
    order = [t.name for t in m.Base.metadata.sorted_tables]  # parents first
    after = [x for x in SCOPED_MODELS if order.index(x.__tablename__) > order.index(model.__tablename__)]
    with two.tenant(A) as s:
        for dependent in sorted(after, key=lambda x: -order.index(x.__tablename__)):  # rows that point at this one go first
            s.execute(delete(dependent))
        s.execute(delete(model))
        s.commit()
    with two.platform() as p:
        left = list(p.scalars(select(model.tenant_id)))
        assert left and set(left) == {B}


def test_bulk_update_cannot_reach_another_client(two):
    with two.tenant(A) as s:
        s.execute(update(m.Case).values(priority="critical"))
        s.execute(update(m.Case).where(m.Case.tenant_id == B).values(owner="intruder"))
        s.commit()
    with two.platform() as p:
        rows = {c.tenant_id: (c.priority, c.owner) for c in p.scalars(select(m.Case))}
    assert rows == {A: ("critical", None), B: ("medium", None)}


def test_joins_aliases_subqueries_and_relationships_are_filtered(two):
    with two.tenant(A) as s:
        joined = s.execute(select(m.Case, m.Party).join(m.Party, m.Party.id == m.Case.party_id)).all()
        assert [(c.tenant_id, p.tenant_id) for c, p in joined] == [(A, A)]  # joining on a shared business id does not cross over
        other = aliased(m.Case)
        assert [c.tenant_id for c in s.scalars(select(other))] == [A]
        sub = select(m.Alert.party_id).scalar_subquery()
        assert [p.name for p in s.scalars(select(m.Party).where(m.Party.id.in_(sub)))] == [f"Person {A}"]
        case = s.scalars(select(m.Case)).one()
        assert case.party.name == f"Person {A}" and [a.tenant_id for a in case.party.accounts] == [A]
        assert s.scalar(select(func.sum(m.Transaction.amount))) == 10


def test_new_rows_are_stamped_and_foreign_rows_are_refused(two):
    with two.tenant(A) as s:
        feed = m.Feed(id="feed-new", name="n", source_system="core")
        s.add(feed)
        s.flush()
        assert feed.tenant_id == A
        s.add(m.Feed(id="feed-smuggled", name="n", source_system="core", tenant_id=B))
        with pytest.raises(TenantError, match="belongs to tenant 'bravo'"):
            s.flush()
        s.rollback()
        case = s.scalars(select(m.Case)).one()
        case.tenant_id = B  # moving a row to another client
        with pytest.raises(TenantError):
            s.flush()


def test_statements_that_cannot_be_filtered_are_refused(two):
    with two.tenant(A) as s:
        with pytest.raises(TenantError, match="Raw SQL"):
            s.execute(text("SELECT * FROM \"case\""))
        with pytest.raises(TenantError, match="Raw SQL"):
            s.execute(select(m.Case.__table__))
        with pytest.raises(TenantError, match="Bulk inserts"):
            s.execute(insert(m.Feed).values(id="f", name="n", source_system="c", tenant_id=B))


def test_a_session_must_declare_who_it_is_for(two):
    with two.factory() as bare:
        with pytest.raises(TenantError, match="no tenant"):
            bare.scalars(select(m.Case)).all()
        bare.add(m.Feed(id="orphan", name="n", source_system="core"))
        with pytest.raises(TenantError, match="without a tenant"):
            bare.flush()
    with pytest.raises(TenantError):
        with session_scope():
            pass
    with pytest.raises(TenantError):
        with session_scope("alpha", platform=True):
            pass
    with two.platform() as p:
        assert {c.tenant_id for c in p.scalars(select(m.Case))} == {A, B}
        with pytest.raises(TenantError):
            tenant_of(p)


def test_the_same_business_ids_live_side_by_side(two):
    with two.platform() as p:
        parties = {(x.tenant_id, x.id): x.name for x in p.scalars(select(m.Party))}
        assert parties == {(A, "P1"): "Person alpha", (B, "P1"): "Person bravo"}
        assert p.scalar(select(func.count()).select_from(m.Alert).where(m.Alert.dedupe_key == "same-key")) == 2


def test_an_incoming_alert_only_ever_touches_the_receiving_client(two):
    kw = dict(source="legacy-tm", external_id="X-1", scenario="structuring", party_id="P1", score=0.8, explanation="rule 12")
    with two.tenant(A) as s:
        alert, case = ingest_external_alert(s, **kw)
        assert case.tenant_id == alert.tenant_id == A and case.party.name == "Person alpha"
        assert ingest_external_alert(s, **kw)[1] is None  # replay inside the client is a duplicate
        s.commit()
    with two.tenant(B) as s:
        alert_b, case_b = ingest_external_alert(s, **kw)  # same external id at another client is a new alert
        assert case_b is not None and case_b.id != case.id and case_b.tenant_id == B
        s.commit()
    with two.tenant(A) as s:
        assert s.get(m.Case, case_b.id) is None and s.get(m.Alert, alert_b.id) is None
        assert s.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == case_b.id)).first() is None


def test_the_guard_covers_every_engine_the_application_builds():
    """The guard recognises the application's database by a mark set where engines are made, so there must be one such place."""
    from pathlib import Path

    from app.db import make_engine

    assert make_engine("sqlite://").caseforge is True
    app_dir = Path(__file__).resolve().parents[1] / "app"
    makers = sorted(str(p.relative_to(app_dir)) for p in app_dir.rglob("*.py") if "create_engine(" in p.read_text())
    assert makers == ["db.py"]


def test_a_session_with_no_engine_is_still_guarded():
    from sqlalchemy.orm import Session as PlainSession

    with PlainSession() as s, pytest.raises(TenantError):
        s.execute(select(m.Case))


def test_a_library_session_on_its_own_database_is_left_alone():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as PlainSession

    with PlainSession(create_engine("sqlite://")) as s:
        assert s.execute(text("select 1")).scalar() == 1
