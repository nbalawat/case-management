"""The kinds of data a pipeline can load, each with its canonical fields, its code lists and what it depends on.

A dataset knows how to turn one mapped source record into canonical values (`transform`) and how to put those
into the store (`load`). Adding a dataset is adding an entry here; the pipeline does not change.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import Account, Party, Transaction
from .mapping import CANONICAL_FIELDS, VALUE_MAPS
from .service import _parse_ts
from .service import transform as transform_transaction

CUSTOMER_FIELDS: dict[str, dict] = {
    "party_id": {"required": True, "desc": "Customer identifier", "synonyms": ["cust_no", "customer_id", "cif", "client_id", "cust_id"]},
    "name": {"required": True, "desc": "Full legal name", "synonyms": ["cust_nm", "customer_name", "full_name", "legal_name"]},
    "kind": {"required": False, "desc": "person or company", "synonyms": ["cust_type", "entity_type", "party_type"]},
    "country": {"required": False, "desc": "Country of residence, ISO 3166 alpha-2", "synonyms": ["res_cntry", "country_cd", "domicile", "resident_country"]},
    "occupation": {"required": False, "desc": "Occupation or line of business", "synonyms": ["occup", "occupation_desc", "industry", "job"]},
    "risk_rating": {"required": False, "desc": "low, medium or high", "synonyms": ["risk_cd", "risk", "risk_level", "cra_rating"]},
    "pep": {"required": False, "desc": "Politically exposed: yes or no", "synonyms": ["pep_flag", "pep_ind", "is_pep"]},
    "onboarded": {"required": False, "desc": "Date the relationship started", "synonyms": ["open_dt", "onboard_date", "since", "relationship_start"]},
}
ACCOUNT_FIELDS: dict[str, dict] = {
    "account_id": {"required": True, "desc": "Account identifier", "synonyms": ["acct_nbr", "acct_no", "account_number", "acct"]},
    "party_id": {"required": True, "desc": "Customer who owns the account", "synonyms": ["cust_no", "customer_id", "cif", "client_id", "owner"]},
    "product": {"required": False, "desc": "Product, for example checking or savings", "synonyms": ["prod_cd", "product_type", "acct_type"]},
    "status": {"required": False, "desc": "open or closed", "synonyms": ["acct_stat", "account_status", "stat_cd"]},
}
CUSTOMER_CODES = {
    "kind": {"P": "person", "I": "person", "IND": "person", "PERSON": "person", "C": "company", "ORG": "company", "COMPANY": "company", "B": "company"},
    "risk_rating": {"L": "low", "LOW": "low", "1": "low", "M": "medium", "MED": "medium", "MEDIUM": "medium", "2": "medium",
                    "H": "high", "HIGH": "high", "3": "high"},
    "pep": {"Y": "yes", "YES": "yes", "TRUE": "yes", "1": "yes", "N": "no", "NO": "no", "FALSE": "no", "0": "no"},
}
ACCOUNT_CODES = {"status": {"O": "open", "A": "open", "OPEN": "open", "ACTIVE": "open", "C": "closed", "CLOSED": "closed", "X": "closed"}}


def _mapped(row: dict, mapping: dict, fields: dict, codes: dict) -> dict:
    """Shared column mapping and code-list translation. Raises ValueError with the reason a record is unusable."""
    out: dict = {}
    for target, meta in fields.items():
        src = mapping.get(target)
        raw = (row.get(src) or "").strip() if src else ""
        if not raw:
            if meta["required"]:
                raise ValueError(f"missing {target}")
            continue
        if target in codes:
            if raw.upper() not in codes[target] and raw.lower() not in codes[target].values():
                raise ValueError(f"unknown {target} code {raw!r}")
            raw = codes[target].get(raw.upper(), raw.lower())
        out[target] = raw
    return out


def transform_customer(row: dict, mapping: dict) -> dict:
    out = _mapped(row, mapping, CUSTOMER_FIELDS, CUSTOMER_CODES)
    if "country" in out:
        if len(out["country"]) != 2 or not out["country"].isalpha():
            raise ValueError(f"country {out['country']!r} is not a two-letter code")
        out["country"] = out["country"].upper()
    if "pep" in out:
        out["pep"] = out["pep"] == "yes"
    if "onboarded" in out:
        out["onboarded"] = _parse_ts(out["onboarded"])
    return out


def transform_account(row: dict, mapping: dict) -> dict:
    return _mapped(row, mapping, ACCOUNT_FIELDS, ACCOUNT_CODES)


@dataclass
class LoadResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    amount: float = 0.0
    rejects: list[tuple[int, str]] = field(default_factory=list)  # (row number, reason) found only at load time
    parties: set[str] = field(default_factory=set)  # customers whose activity changed, for detection


def _ids(s: Session, column, wanted: set[str]) -> set[str]:
    found: set[str] = set()
    wanted_list = sorted(wanted)
    for i in range(0, len(wanted_list), 500):
        found |= set(s.scalars(select(column).where(column.in_(wanted_list[i:i + 500]))))
    return found


def load_customers(s: Session, rows: list[tuple[int, dict]], *, origin: str, rules: dict) -> LoadResult:
    """Insert new customers and update the ones whose details changed. The last record for an id wins."""
    out = LoadResult()
    latest = {r["party_id"]: r for _, r in rows}
    existing = {p.id: p for i in range(0, len(latest), 500)
                for p in s.scalars(select(Party).where(Party.id.in_(sorted(latest)[i:i + 500])))}
    for pid, r in latest.items():
        values = {k: v for k, v in r.items() if k != "party_id"}
        party = existing.get(pid)
        if party is None:
            s.add(Party(id=pid, **values))
            out.inserted += 1
        elif any(getattr(party, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(party, k, v)
            out.updated += 1
        else:
            out.unchanged += 1
    out.unchanged += len(rows) - len(latest)  # earlier duplicates of the same id within the file
    s.flush()
    return out


def load_accounts(s: Session, rows: list[tuple[int, dict]], *, origin: str, rules: dict) -> LoadResult:
    out = LoadResult()
    known_parties = _ids(s, Party.id, {r["party_id"] for _, r in rows})
    existing = {a.id: a for i in range(0, len(rows), 500)
                for a in s.scalars(select(Account).where(Account.id.in_([r["account_id"] for _, r in rows[i:i + 500]])))}
    seen: set[str] = set()
    for n, r in rows:
        if r["party_id"] not in known_parties:
            if rules.get("unknown_customer", "reject") == "reject":
                out.rejects.append((n, f"customer {r['party_id']} is not known"))
                continue
            s.add(Party(id=r["party_id"], name=f"Customer {r['party_id']}", risk_rating="medium"))
            known_parties.add(r["party_id"])
        account = existing.get(r["account_id"])
        values = {k: v for k, v in r.items() if k not in ("account_id", "party_id")}
        if r["account_id"] in seen:
            out.unchanged += 1
        elif account is None:
            s.add(Account(id=r["account_id"], party_id=r["party_id"], **values))
            out.inserted += 1
        elif account.party_id != r["party_id"]:
            out.rejects.append((n, f"account {r['account_id']} already belongs to another customer"))
            continue
        elif any(getattr(account, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(account, k, v)
            out.updated += 1
        else:
            out.unchanged += 1
        seen.add(r["account_id"])
    s.flush()
    return out


def load_transactions(s: Session, rows: list[tuple[int, dict]], *, origin: str, rules: dict) -> LoadResult:
    """Insert transactions not seen before. A transaction id that already exists is left alone, so replays are safe."""
    out = LoadResult()
    known_parties = _ids(s, Party.id, {r["party_id"] for _, r in rows})
    accounts = {a.id: a.party_id for i in range(0, len(rows), 500)
                for a in s.scalars(select(Account).where(Account.id.in_(sorted({r["account_id"] for _, r in rows[i:i + 500]}))))}
    known_txns = _ids(s, Transaction.id, {r["txn_id"] for _, r in rows})
    create = rules.get("unknown_customer", "reject") == "create"
    for n, r in rows:
        if r["txn_id"] in known_txns:
            out.unchanged += 1
            continue
        if r["party_id"] not in known_parties:
            if not create:
                out.rejects.append((n, f"customer {r['party_id']} is not known"))
                continue
            s.add(Party(id=r["party_id"], name=f"Customer {r['party_id']}", risk_rating="medium"))
            known_parties.add(r["party_id"])
        if r["account_id"] not in accounts:
            if not create:
                out.rejects.append((n, f"account {r['account_id']} is not known"))
                continue
            s.add(Account(id=r["account_id"], party_id=r["party_id"]))
            accounts[r["account_id"]] = r["party_id"]
        elif accounts[r["account_id"]] != r["party_id"]:
            out.rejects.append((n, f"account {r['account_id']} does not belong to customer {r['party_id']}"))
            continue
        s.add(Transaction(id=r["txn_id"], account_id=r["account_id"], party_id=r["party_id"], ts=r["ts"], amount=r["amount"],
                          currency=r["currency"], direction=r["direction"], channel=r["channel"], counterparty=r["counterparty"],
                          counterparty_country=r["counterparty_country"], feed_id=origin))
        known_txns.add(r["txn_id"])
        out.inserted += 1
        out.amount += r["amount"]
        out.parties.add(r["party_id"])
    s.flush()
    return out


@dataclass(frozen=True)
class Dataset:
    id: str
    label: str
    fields: dict[str, dict]
    codes: dict[str, dict[str, str]]
    transform: Callable[[dict, dict], dict]
    load: Callable[..., LoadResult]
    depends_on: tuple[str, ...] = ()
    amount_field: str | None = None  # the field whose total is reconciled, where there is one
    triggers_detection: bool = False


DATASETS: dict[str, Dataset] = {d.id: d for d in [
    Dataset("customers", "Customers", CUSTOMER_FIELDS, CUSTOMER_CODES, transform_customer, load_customers),
    Dataset("accounts", "Accounts", ACCOUNT_FIELDS, ACCOUNT_CODES, transform_account, load_accounts, depends_on=("customers",)),
    Dataset("transactions", "Transactions", CANONICAL_FIELDS, VALUE_MAPS, transform_transaction, load_transactions,
            depends_on=("customers", "accounts"), amount_field="amount", triggers_detection=True),
]}


def in_dependency_order(dataset_ids: list[str]) -> list[str]:
    """Datasets ordered so each comes after everything it depends on."""
    done: list[str] = []

    def visit(d: str) -> None:
        for dep in DATASETS[d].depends_on:
            visit(dep)
        if d not in done:
            done.append(d)
    for d in DATASETS:
        if d in dataset_ids:
            visit(d)
    return [d for d in done if d in dataset_ids]


__all__ = ["DATASETS", "Dataset", "LoadResult", "in_dependency_order"]
