"""Schema mapping: propose how a source feed's columns map to the canonical transaction model.

The proposer is a strategy. The default is a deterministic matcher (synonyms + string
similarity) so the demo runs offline; an LLM-backed proposer can implement the same protocol.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass
from typing import Protocol

CANONICAL_FIELDS: dict[str, dict] = {
    "txn_id": {"required": True, "desc": "Unique transaction reference", "synonyms": ["txn_ref", "trans_id", "reference", "txn_no", "id"]},
    "account_id": {"required": True, "desc": "Account identifier", "synonyms": ["acct_nbr", "acct_no", "account_number", "acct"]},
    "party_id": {"required": True, "desc": "Customer identifier", "synonyms": ["cust_no", "customer_id", "cif", "client_id"]},
    "ts": {"required": True, "desc": "Transaction timestamp", "synonyms": ["txn_dt", "trans_date", "posting_date", "value_date", "timestamp"]},
    "amount": {"required": True, "desc": "Amount in transaction currency", "synonyms": ["txn_amt", "amt", "value", "trans_amount"]},
    "currency": {"required": False, "desc": "ISO 4217 currency", "synonyms": ["ccy", "curr", "ccy_cd"]},
    "direction": {"required": True, "desc": "in (credit) or out (debit)", "synonyms": ["dr_cr_ind", "debit_credit", "dc_flag", "side"]},
    "channel": {"required": True, "desc": "cash, wire, ach, card or p2p", "synonyms": ["chnl", "txn_type", "trans_type", "method"]},
    "counterparty": {"required": False, "desc": "Counterparty name", "synonyms": ["cntrprty_nm", "beneficiary", "payee", "originator", "other_party"]},
    "counterparty_country": {"required": False, "desc": "Counterparty country, ISO 3166 alpha-2", "synonyms": ["cntry_cd", "ctry", "bene_country", "country"]},
}

# Value translations applied after column mapping (code lists differ per source system).
VALUE_MAPS: dict[str, dict[str, str]] = {
    "direction": {"CR": "in", "C": "in", "CREDIT": "in", "IN": "in", "DR": "out", "D": "out", "DEBIT": "out", "OUT": "out"},
    "channel": {"CSH": "cash", "CASH": "cash", "WIR": "wire", "WIRE": "wire", "ACH": "ach", "CRD": "card", "CARD": "card", "P2P": "p2p", "ZEL": "p2p"},
}


@dataclass
class FieldProposal:
    target: str
    source: str | None
    confidence: float
    required: bool
    reason: str
    samples: list[str]


class MappingProposer(Protocol):
    def propose(self, columns: list[str], sample_rows: list[dict]) -> list[FieldProposal]: ...


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class HeuristicProposer:
    """Synonym and similarity matcher. Deterministic, explainable, no network."""

    def propose(self, columns: list[str], sample_rows: list[dict]) -> list[FieldProposal]:
        scored: list[tuple[float, str, str, str]] = []
        for target, meta in CANONICAL_FIELDS.items():
            for col in columns:
                n = _norm(col)
                if n == _norm(target):
                    scored.append((0.99, target, col, "exact name match"))
                elif n in {_norm(x) for x in meta["synonyms"]}:
                    scored.append((0.95, target, col, "known alias for this field"))
                else:
                    sim = max(difflib.SequenceMatcher(None, n, _norm(x)).ratio() for x in [target, *meta["synonyms"]])
                    scored.append((sim, target, col, "similar name"))
        # Assign best pairs first so a weak match cannot take a column a strong match needs.
        chosen: dict[str, tuple[str, float, str]] = {}
        taken: set[str] = set()
        for score, target, col, reason in sorted(scored, reverse=True):
            if score < 0.7 or target in chosen or col in taken:
                continue
            chosen[target] = (col, score, reason)
            taken.add(col)
        out: list[FieldProposal] = []
        for target, meta in CANONICAL_FIELDS.items():
            if target in chosen:
                col, score, reason = chosen[target]
                samples = [str(r.get(col, "")) for r in sample_rows[:3]]
                out.append(FieldProposal(target, col, round(score, 2), meta["required"], reason, samples))
            else:
                out.append(FieldProposal(target, None, 0.0, meta["required"], "no close match", []))
        return out


def proposals_as_dicts(props: list[FieldProposal]) -> list[dict]:
    return [asdict(p) | {"desc": CANONICAL_FIELDS[p.target]["desc"]} for p in props]
