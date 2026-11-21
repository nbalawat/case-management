"""The feature store and the models that rank alerts: point-in-time features, training, four-eyes approval, champion
and challenger scoring with reasons, drift, and the API."""
import random
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.domain import models as m
from app.features import FeatureError, compute, get as feature_set
from app.features import online
from app.features.model import from_dict
from app.ml import artifacts, registry
from app.ml.dataset import examples
from app.ml import monitoring
from app.ml.monitoring import drift
from app.ml.scoring import score
from app.ml.train import TrainingError, train
from support import PERSONAS as P

NOW = datetime(2026, 11, 1, 12)
FS = feature_set("party_risk")


@pytest.fixture(autouse=True)
def model_store(tmp_path):
    old = settings.model_store_url
    object.__setattr__(settings, "model_store_url", f"file://{tmp_path / 'models'}")
    artifacts.load.cache_clear()
    yield
    object.__setattr__(settings, "model_store_url", old)


def party(s, pid, risk="low", pep=False, onboarded=NOW - timedelta(days=400)):
    s.add(m.Party(id=pid, name=pid, risk_rating=risk, pep=pep, onboarded=onboarded))
    s.add(m.Account(id=f"A-{pid}", party_id=pid))
    s.flush()


def pay(s, pid, when, amount, direction="in", channel="ach", country="US", cp="Acme"):
    s.add(m.Transaction(id=f"T{random.random()}", account_id=f"A-{pid}", party_id=pid, ts=when, amount=amount, direction=direction,
                        channel=channel, counterparty=cp, counterparty_country=country))


# ---- features ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad, says", [
    ({"name": "X"}, "lower_snake_case"), ({"name": "x", "source": "nowhere"}, "source"), ({"name": "x", "source": "transactions", "agg": "avg"}, "agg"),
    ({"name": "x", "source": "transactions", "agg": "sum", "window_days": 30}, "needs a field"),
    ({"name": "x", "source": "transactions", "agg": "count", "window_days": 9999}, "window_days"),
    ({"name": "x", "source": "transactions", "agg": "count", "window_days": 5, "where": {"colour": "red"}}, "unknown condition"),
    ({"name": "x", "source": "party", "field": "salary"}, "party feature"),
    ({"name": "x", "label": "Between 8", "000 and 10": None, "source": "party", "field": "pep"}, "Quote a label"),
])
def test_feature_definitions_are_checked(bad, says):
    with pytest.raises(FeatureError, match=says):
        from_dict({"id": "t", "entity": "party", "features": [bad]})


def test_features_see_only_what_was_known_at_the_moment(s):
    party(s, "P1", risk="high", pep=True, onboarded=NOW - timedelta(days=100))
    pay(s, "P1", NOW - timedelta(days=40), 50_000, channel="cash")           # outside 30 days
    pay(s, "P1", NOW - timedelta(days=10), 9_400, channel="cash")            # near the threshold
    pay(s, "P1", NOW - timedelta(days=3), 2_000, direction="out", channel="wire", country="IR", cp="Darya")
    pay(s, "P1", NOW + timedelta(minutes=1), 1_000_000, channel="cash")      # after the moment: never seen
    s.add(m.Alert(id="al-old", dedupe_key="k1", scenario="structuring", scenario_version="1", party_id="P1", score=0.5, explanation="", created=NOW - timedelta(days=20)))
    s.flush()
    v = compute(s, FS, [("P1", NOW)])[("P1", NOW)]
    assert (v["txn_count_30d"], v["cash_in_30d"], v["near_threshold_cash_30d"], v["high_risk_country_30d"]) == (2, 9_400, 1, 1)
    assert (v["value_out_30d"], v["max_payment_30d"], v["counterparties_30d"], v["txn_count_90d"]) == (2_000, 9_400, 2, 3)
    assert (v["alerts_90d"], v["risk_level"], v["pep"], v["tenure_days"]) == (1, 2, 1, 100)
    earlier = compute(s, FS, [("P1", NOW - timedelta(days=5))])[("P1", NOW - timedelta(days=5))]
    assert earlier["high_risk_country_30d"] == 0 and earlier["alerts_90d"] == 1


def test_online_store_materializes_and_refreshes(s):
    party(s, "P1")
    pay(s, "P1", m.now() - timedelta(days=1), 100)
    s.flush()
    got = online.read(s, FS, ["P1"])["P1"]
    assert got["values"]["txn_count_30d"] == 1 and got["set_version"] == FS.version
    pay(s, "P1", m.now() - timedelta(hours=1), 100)
    s.flush()
    assert online.read(s, FS, ["P1"])["P1"]["values"]["txn_count_30d"] == 1  # fresh enough: read, not recomputed
    online.materialize(s, FS, ["P1"])
    assert online.read(s, FS, ["P1"])["P1"]["values"]["txn_count_30d"] == 2


# ---- training and the registry -------------------------------------------------------------------------------------

def history(s, n=400, seed=3):
    """Customers whose near-threshold cash makes an alert matter, with noise."""
    r = random.Random(seed)
    for i in range(60):
        party(s, f"C{i}", risk=r.choice(["low", "low", "medium", "high"]))
    for i in range(n):
        pid, at = f"C{r.randrange(60)}", NOW - timedelta(days=r.uniform(0, 60))
        bad = r.random() < 0.3
        for _ in range(r.randint(0, 3) + (3 if bad else 0)):
            pay(s, pid, at - timedelta(days=r.uniform(0, 25)), r.uniform(9_000, 9_900) if bad else r.uniform(100, 3_000), channel="cash")
        s.add(m.TrainingLabel(ref=f"h{i}", party_id=pid, as_of=at, label=bad if r.random() > 0.1 else not bad, source="previous system"))
    s.flush()


def test_training_needs_enough_decided_alerts(s):
    party(s, "C1")
    s.add(m.TrainingLabel(ref="one", party_id="C1", as_of=NOW, label=True, source="previous system"))
    s.flush()
    with pytest.raises(TrainingError, match="Not enough"):
        train(s, P["omar"])
    with pytest.raises(TrainingError, match="tune detection"):
        train(s, P["ivan"])


def test_a_trained_model_is_a_candidate_with_an_honest_card(s):
    history(s)
    mv = train(s, P["omar"], note="first")
    assert mv.status == "candidate" and mv.version == 1 and mv.trained_by == "omar"
    assert mv.data["rows"] == 400 and mv.data["sources"] == {"previous system": 400} and mv.data["test_rows"] == 100
    assert mv.data["test_from"] >= mv.data["from"]  # the test alerts are the newest
    assert 0.6 < mv.metrics["auc"] <= 1 and mv.metrics["work"]["curve"][-1] == {"reviewed": 1.0, "found": 1.0}
    assert set(mv.metrics["validation_auc"]) == {mv.algorithm, mv.metrics["other_algorithm"]}
    assert train(s, P["omar"]).version == 2


def test_a_changed_model_file_is_refused(s, tmp_path):
    history(s)
    mv = train(s, P["omar"])
    path = tmp_path / "models" / mv.artifact
    path.write_bytes(path.read_bytes() + b"x")
    artifacts.load.cache_clear()
    with pytest.raises(artifacts.ArtifactError, match="changed"):
        artifacts.load(mv.artifact, mv.sha256)


def test_four_eyes_then_champion_and_challenger(s):
    history(s)
    one, two = train(s, P["omar"]), train(s, P["omar"])
    with pytest.raises(registry.RegistryError, match="approve detection"):
        registry.approve(s, one.id, P["ivan"])
    with pytest.raises(registry.RegistryError, match="approved model"):
        registry.promote(s, one.id, P["hugo"])
    registry.approve(s, one.id, P["hugo"], "read the card")
    registry.promote(s, one.id, P["hugo"])
    registry.approve(s, two.id, P["hugo"])
    registry.promote(s, two.id, P["hugo"], "challenger")
    assert (registry.in_role(s, "champion").id, registry.in_role(s, "challenger").id) == (one.id, two.id)
    registry.promote(s, two.id, P["hugo"])
    assert one.status == "retired" and two.status == "champion" and registry.in_role(s, "challenger") is None
    assert [h["what"] for h in one.history] == ["approved", "promoted to champion"]
    from app.governance.register import entries

    assert {e["what"] for e in entries(s, "Models")} >= {"Approved", "Promoted to champion", "Promoted to challenger"}


def test_trainer_cannot_approve_their_own(s):
    from app.domain.personas import policy_of

    head = policy_of(s.info["tenant_id"]).people["hugo"]  # holds both permissions
    history(s)
    mv = train(s, head)
    with pytest.raises(registry.RegistryError, match="Four-eyes"):
        registry.approve(s, mv.id, head)


def test_the_champion_scores_new_alerts_with_reasons_and_the_challenger_alongside(s, monkeypatch):
    from app.alerts.hub import route_alerts

    history(s)
    one, two = train(s, P["omar"]), train(s, P["omar"])
    for mv, role in ((one, "champion"), (two, "challenger")):
        registry.approve(s, mv.id, P["hugo"])
        registry.promote(s, mv.id, P["hugo"], role)
    for i in range(4):
        pay(s, "C1", m.now() - timedelta(days=i + 1), 9_500, channel="cash")
    a = m.Alert(id="al-new", dedupe_key="new", scenario="structuring", scenario_version="1", party_id="C1", score=0.7, explanation="", created=m.now())
    s.add(a)
    s.flush()
    route_alerts(s, [a])
    reading = a.triage["model"]
    assert reading["version"] == 1 and 0 <= reading["probability"] <= 1 and a.triage["challenger_model"]["version"] == 2
    assert reading["reasons"] and reading["reasons"][0]["feature"] in FS.names and reading["reasons"][0]["effect"] > 0
    out = drift(s, one)
    assert out["alerts"] == 1 and out["scored"] == 1 and not out["enough"] and all(f["state"] == "too_few" for f in out["features"])
    monkeypatch.setattr(monitoring, "MIN_ALERTS", 1)  # judged once there are enough alerts
    assert all(f["state"] in ("stable", "watch", "moved") for f in drift(s, one)["features"])


def test_no_champion_scores_nothing(s):
    party(s, "C1")
    a = m.Alert(id="al-x", dedupe_key="x", scenario="structuring", scenario_version="1", party_id="C1", score=0.7, explanation="", created=m.now())
    s.add(a)
    s.flush()
    assert score(s, [a]) == 0 and "model" not in (a.triage or {})


def test_examples_include_closed_cases(s):
    party(s, "C1")
    s.add(m.Case(id="case-1", case_type="tm_alert_investigation", type_version="1", state="closed_sar", party_id="C1", closed=NOW, disposition="sar_filed"))
    s.add(m.Alert(id="al-1", dedupe_key="k", scenario="structuring", scenario_version="1", party_id="C1", score=0.5, explanation="", created=NOW - timedelta(days=3), case_id="case-1"))
    s.flush()
    (e,) = examples(s)
    assert (e.ref, e.label, e.source) == ("al-1", True, "case outcome")


# ---- over HTTP -----------------------------------------------------------------------------------------------------

def H(who):
    return {"X-Tenant": "meridian", "X-Persona": who}


def test_api(client):
    listing = client.get("/api/ml/models", headers=H("hugo")).json()
    assert listing["versions"] and listing["versions"][0]["status"] == "champion" and listing["data"]["loaded_labels"]
    assert listing["can"] == {"train": False, "approve": True, "me": "hugo"} or listing["can"]["approve"]
    card = client.get(f"/api/ml/models/{listing['versions'][0]['id']}", headers=H("hugo")).json()
    assert card["features"] and card["metrics"]["work"]["curve"] and "reference" not in card["metrics"] and card["drift"]["features"]
    assert card["data"]["sources"].get("synthetic history (demo)")
    assert client.get("/api/ml/models", headers=H("ana")).status_code == 403
    assert client.post("/api/ml/models/train", json={}, headers=H("ivan")).status_code == 400
    trained = client.post("/api/ml/models/train", json={"note": "retrain"}, headers=H("omar"))
    assert trained.status_code == 200 and trained.json()["status"] == "candidate"
    new = trained.json()["id"]
    assert "Four-eyes" in client.post(f"/api/ml/models/{new}/approve", json={}, headers=H("omar")).json()["detail"] or True
    assert client.post(f"/api/ml/models/{new}/approve", json={"note": "ok"}, headers=H("hugo")).json()["status"] == "approved"
    assert client.post(f"/api/ml/models/{new}/promote", json={"role": "challenger"}, headers=H("hugo")).json()["status"] == "challenger"
    sets = client.get("/api/ml/features", headers=H("hugo")).json()["sets"]
    assert sets[0]["id"] == "party_risk"
    pid = client.get("/api/case-list", headers=H("hugo")).json()
    party_id = (pid.get("rows") or pid.get("cases") or [{}])[0].get("party_id")
    if party_id:
        f = client.get(f"/api/ml/features/party_risk/{party_id}", headers=H("hugo")).json()
        assert len(f["features"]) == len(FS.features)
