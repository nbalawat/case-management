"""The case list at volume: paging, sorting, filters and facets in the database, under the same visibility rules."""
import csv
import io

import pytest


def h(who, tenant="meridian"):
    return {"X-Tenant": tenant, "X-Persona": who}


def full(client, who, status="open"):
    return client.get(f"/api/cases?status={status}", headers=h(who)).json()


def listing(client, who, **params):
    r = client.get("/api/case-list", headers=h(who), params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("who", ["audra", "ana", "ivan", "sofia"])
@pytest.mark.parametrize("status", ["open", "closed", "all"])
def test_pages_cover_exactly_what_the_person_may_see(client, who, status):
    expected = {c["id"] for c in full(client, who, status)}
    seen, page = [], 1
    while True:
        out = listing(client, who, status=status, size=7, page=page)
        assert out["total"] == len(expected)
        seen += [r["id"] for r in out["rows"]]
        if len(out["rows"]) < 7:
            break
        page += 1
    assert len(seen) == len(set(seen)), "pages overlap"
    assert set(seen) == expected


def test_default_order_is_priority_then_deadline(client):
    rows = listing(client, "audra", size=200)["rows"]
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    keys = [(rank[r["priority"]], r["sla_due"] or "9999") for r in rows]
    assert keys == sorted(keys)


def test_sorting_by_a_column_and_direction(client):
    rows = listing(client, "audra", status="all", sort="-created", size=200)["rows"]
    created = [r["created"] for r in rows]
    assert created == sorted(created, reverse=True)
    names = [r["party_name"] for r in listing(client, "audra", sort="customer", size=200)["rows"]]
    assert names == sorted(names)


def test_unknown_sort_or_bad_page_is_refused(client):
    assert client.get("/api/case-list?sort=stage_clock", headers=h("audra")).status_code == 422
    assert client.get("/api/case-list?size=1000", headers=h("audra")).status_code == 422
    assert client.get("/api/case-list?status=pending", headers=h("audra")).status_code == 422


def test_filters_narrow_and_facets_count_each_choice(client):
    everything = full(client, "audra")
    out = listing(client, "audra", size=1)
    by_type = out["facets"]["case_type"]
    assert sum(by_type.values()) == len(everything)
    some = next(iter(by_type))
    narrowed = listing(client, "audra", case_type=some, size=1)
    assert narrowed["total"] == by_type[some] == sum(1 for c in everything if c["case_type"] == some)
    # A facet ignores its own filter, so the other choices stay visible with their counts.
    assert narrowed["facets"]["case_type"] == by_type
    assert sum(narrowed["facets"]["priority"].values()) == narrowed["total"]
    two = listing(client, "audra", priority=["critical", "high"], size=200)
    assert {r["priority"] for r in two["rows"]} <= {"critical", "high"}


def test_text_owner_and_labels(client):
    case = full(client, "audra")[0]
    hit = listing(client, "audra", q=case["party_name"].split()[0].lower())
    assert case["id"] in {r["id"] for r in hit["rows"]}
    assert listing(client, "audra", q=case["id"].upper())["total"] >= 1
    mine = listing(client, "ivan", owner="me", size=200)
    assert mine["rows"] and all(r["owner"] == "ivan" for r in mine["rows"])
    assert all(r["owner"] is None for r in listing(client, "audra", owner="unassigned", size=200)["rows"])
    labels = listing(client, "audra", size=1)["labels"]
    assert labels["queue"]["aml-l2"] == "AML investigations (L2)"
    assert labels["case_type"]["sanctions_hit"]


def test_rows_match_the_classic_list(client):
    classic = {c["id"]: c for c in full(client, "audra")}
    for r in listing(client, "audra", size=200)["rows"]:
        assert r == classic[r["id"]]


def test_export_matches_the_filtered_list(client):
    out = listing(client, "ana", size=1)
    r = client.get("/api/case-list/export.csv", headers=h("ana"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == out["total"]
    assert {x["id"] for x in rows} == {c["id"] for c in full(client, "ana")}


def test_other_clients_cases_never_appear(client):
    mine = {c["id"] for c in full(client, "audra")}
    other = listing(client, "audra", status="all", size=200)
    assert {r["id"] for r in other["rows"]} <= mine | {c["id"] for c in full(client, "audra", "closed")}
    kestrel = client.get("/api/case-list?status=all&size=200", headers={"X-Tenant": "kestrel"}).json()
    assert not {r["id"] for r in kestrel["rows"]} & mine
