"""Entity resolution: turning the names in a client's data into one record per real-world party.

Three questions, three levels of caution:
  * Are two spellings of a counterparty the same firm?  Very close spellings merge by themselves; close ones go to a person.
  * Is a counterparty one of our own customers?         Always a person's call: a name alone is weak evidence.
  * Are two customer records the same person?           Always a person's call, and only proposed when more than the name agrees.

A run rebuilds entities and the money between them from the transactions, and re-applies every decision a person has
made. Decisions are stored against names, not against generated ids, so they survive any number of runs.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..detection.scenarios import DEMO_WATCHLIST, HIGH_RISK_COUNTRIES
from ..domain.models import Entity, EntityEdge, MatchCandidate, Party, Transaction, now
from ..domain.personas import Persona
from ..tenancy import tenant_of
from ..tenants.registry import runtime
from ..visibility import system_work
from . import names

DEFAULTS = {"auto_merge_at": 0.96, "review_at": 0.82, "duplicate_customer_at": 0.80, "watchlist_at": 0.90}
KINDS = {"counterparty_variant": "Two spellings of one counterparty?", "customer_as_counterparty": "Is this counterparty our own customer?",
         "duplicate_customer": "Two records for one customer?"}
RESOLVE = "resolve_entities"


class ResolutionError(ValueError):
    pass


def thresholds(tenant_id: str) -> dict:
    return DEFAULTS | ((runtime(tenant_id).settings or {}).get("entities") or {})


def entity_id(stable_key: str) -> str:
    return "ent-" + hashlib.sha1(stable_key.encode()).hexdigest()[:12]


def customer_entity_id(party_id: str) -> str:
    return f"ent-{party_id}"


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _candidate_id(kind: str, left: str, right: str) -> str:
    return "mc-" + hashlib.sha1(f"{kind}|{left}|{right}".encode()).hexdigest()[:12]


class _Union:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)  # the smallest key names the cluster, so its id is stable


@dataclass
class _Spelling:
    name: str
    key: str
    txns: int = 0
    countries: set = field(default_factory=set)


def _decisions(s: Session) -> dict[tuple[str, str, str], str]:
    return {(c.kind, c.left_key, c.right_key): c.status for c in s.scalars(select(MatchCandidate).where(MatchCandidate.status != "open"))}


@system_work
def resolve(s: Session) -> dict:
    """Rebuild this client's entities, edges and open questions from its data. Safe to run as often as you like."""
    tenant = tenant_of(s)
    limits = thresholds(tenant)
    decided = _decisions(s)
    parties = list(s.scalars(select(Party)))
    by_party_key: dict[str, list[Party]] = defaultdict(list)
    for p in parties:
        by_party_key[names.key(p.name)].append(p)

    # 1. Every spelling of every counterparty, with how much it was used.
    rows = s.execute(select(Transaction.counterparty, Transaction.counterparty_country, Transaction.party_id, Transaction.direction,
                            Transaction.channel, func.count(), func.sum(Transaction.amount), func.min(Transaction.ts), func.max(Transaction.ts))
                     .where(Transaction.counterparty != "")
                     .group_by(Transaction.counterparty, Transaction.counterparty_country, Transaction.party_id, Transaction.direction,
                               Transaction.channel)).all()
    spellings: dict[str, _Spelling] = {}
    for name, country, _pid, _dir, _ch, n, _total, _first, _last in rows:
        sp = spellings.setdefault(name, _Spelling(name, names.key(name)))
        sp.txns += n
        sp.countries.add(country)
    keys = sorted({sp.key for sp in spellings.values() if sp.key})

    # 2. Which keys are the same firm: decided by a person, or close enough to merge without one.
    union, found, apart = _Union(), {}, set()
    blocks: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        union.find(k)
        for b in names.blocks(k):
            blocks[b].append(k)
    seen: set[tuple[str, str]] = set()
    for members in blocks.values():
        if len(members) > 400:  # a word this common tells us nothing
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                pair = _pair(a, b)
                if pair in seen:
                    continue
                seen.add(pair)
                verdict = decided.get(("counterparty_variant", f"cp:{pair[0]}", f"cp:{pair[1]}"))
                if verdict == "different":
                    apart.add(pair)
                    continue
                score = 1.0 if verdict == "same" else names.similarity(a, b)
                if verdict == "same" or score >= limits["auto_merge_at"]:
                    union.join(a, b)
                elif score >= limits["review_at"]:
                    found[("counterparty_variant", f"cp:{pair[0]}", f"cp:{pair[1]}")] = (pair[0], pair[1], score, [f"Names are {score:.0%} alike"])
    # Once spellings have merged, ask about each pair of firms once, and never about two a person has already told apart.
    told_apart = {_pair(union.find(a), union.find(b)) for a, b in apart}
    best: dict[tuple[str, str], tuple] = {}
    for ckey, q in list(found.items()):
        firms = _pair(union.find(q[0]), union.find(q[1]))
        del found[ckey]
        rank = (_pair(q[0], q[1]) == firms, q[2])  # ask about the firms' main keys when those are alike enough themselves
        if firms[0] != firms[1] and firms not in told_apart and (firms not in best or rank > best[firms][0]):
            best[firms] = (rank, ckey, q)
    found.update({ckey: q for _rank, ckey, q in best.values()})
    clusters: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        clusters[union.find(k)].append(k)

    # 3. A counterparty that a person has confirmed is one of our customers points at that customer.
    owner_of: dict[str, str] = {}
    for root, members in clusters.items():
        for k in members:
            for p in by_party_key.get(k, []):
                ckey = ("customer_as_counterparty", f"cp:{root}", f"party:{p.id}")
                verdict = decided.get(ckey)
                if verdict == "same":
                    owner_of[root] = p.id
                elif verdict is None:
                    display = max((sp for sp in spellings.values() if sp.key == k), key=lambda sp: sp.txns).name
                    found[ckey] = (display, f"{p.name} ({p.id})", 0.75, ["The counterparty's name matches a customer's name exactly", "A name alone does not prove it is the same person"])

    # 4. Rebuild entities and edges.
    s.execute(delete(EntityEdge))
    s.execute(delete(Entity))
    s.flush()
    common_at = max(5, len(parties) // 10)
    cluster_of_spelling = {sp.name: union.find(sp.key) for sp in spellings.values() if sp.key}
    edges: dict[tuple[str, str], dict] = {}
    for name, _country, pid, direction, channel, n, total, first, last in rows:
        root = cluster_of_spelling.get(name)
        if root is None:
            continue
        target = customer_entity_id(owner_of[root]) if root in owner_of else entity_id(root)
        if target == customer_entity_id(pid):
            continue  # a payment to oneself is not a relationship
        e = edges.setdefault((pid, target), {"txns": 0, "sent": 0.0, "received": 0.0, "first": first, "last": last, "channels": set()})
        e["txns"] += n
        e["sent" if direction == "out" else "received"] += float(total or 0)
        e["first"], e["last"] = min(e["first"], first), max(e["last"], last)
        e["channels"].add(channel)
    degree: dict[str, set[str]] = defaultdict(set)
    for (pid, target) in edges:
        degree[target].add(pid)
    made = 0
    for root, members in clusters.items():
        if root in owner_of:
            continue
        mine = sorted((sp for sp in spellings.values() if sp.key in members), key=lambda sp: (-sp.txns, sp.name.isupper(), len(sp.name), sp.name))  # ties: mixed case, then the shortest
        eid = entity_id(root)
        countries = sorted(set().union(*(sp.countries for sp in mine)))
        best = max((names.similarity(root, w) for w in DEMO_WATCHLIST), default=0.0)
        flags = (["watchlist"] if best >= limits["watchlist_at"] else []) + (["high_risk_country"] if set(countries) & HIGH_RISK_COUNTRIES else []) \
            + (["common"] if len(degree[eid]) >= common_at else [])
        aliases = [{"name": sp.name, "count": sp.txns,
                    "how": "main spelling" if sp is mine[0] else "same once legal form and punctuation are removed" if sp.key == mine[0].key
                    else "confirmed by a person" if decided.get(("counterparty_variant", *(f"cp:{k}" for k in _pair(sp.key, mine[0].key)))) == "same"
                    else "very close spelling", "score": round(names.similarity(sp.name, mine[0].name), 3)} for sp in mine]
        s.add(Entity(id=eid, kind="counterparty", name=mine[0].name, key=root, countries=countries, aliases=aliases, flags=flags,
                     customers=len(degree[eid]), txns=sum(sp.txns for sp in mine),
                     total=round(sum(e["sent"] + e["received"] for (_, t), e in edges.items() if t == eid), 2)))
        made += 1
    for p in parties:
        eid = customer_entity_id(p.id)
        took = [root for root, pid in owner_of.items() if pid == p.id]
        aliases = [{"name": sp.name, "count": sp.txns, "how": "confirmed by a person as this customer", "score": round(names.similarity(sp.name, p.name), 3)}
                   for sp in spellings.values() if sp.key and union.find(sp.key) in took]
        s.add(Entity(id=eid, kind="customer", name=p.name, key=names.key(p.name), party_id=p.id, countries=[p.country], aliases=aliases,
                     flags=[], customers=len(degree[eid]), txns=0, total=0.0))
    for (pid, target), e in edges.items():
        s.add(EntityEdge(party_id=pid, entity_id=target, txns=e["txns"], sent=round(e["sent"], 2), received=round(e["received"], 2),
                         first=e["first"], last=e["last"], channels=sorted(e["channels"])))

    # 5. Customer records that may be one person: the name must match, and something else must agree as well.
    partners: dict[str, set[str]] = defaultdict(set)
    hubs = {t for t, who in degree.items() if len(who) >= common_at}
    for (pid, target) in edges:
        if target not in hubs:
            partners[pid].add(target)
    for group in by_party_key.values():
        for i, a in enumerate(group[:8]):
            for b in group[i + 1:8]:
                left, right = sorted([a, b], key=lambda p: p.id)
                ckey = ("duplicate_customer", f"party:{left.id}", f"party:{right.id}")
                if ckey in decided:
                    continue
                reasons, score = ["Same name"], 0.60
                if a.country == b.country:
                    reasons.append("Same country")
                    score += 0.05
                if a.occupation and a.occupation == b.occupation:
                    reasons.append("Same occupation")
                    score += 0.20
                shared = partners[a.id] & partners[b.id]
                if shared:
                    reasons.append(f"Both transact with {len(shared)} of the same uncommon counterparties")
                    score += 0.15
                if score >= limits["duplicate_customer_at"]:
                    found[ckey] = (f"{left.name} ({left.id})", f"{right.name} ({right.id})", round(min(score, 0.99), 2), reasons)

    # 6. Open questions: keep the ones still true, drop the ones the data no longer supports, add the new.
    existing = {(c.kind, c.left_key, c.right_key): c for c in s.scalars(select(MatchCandidate).where(MatchCandidate.status == "open"))}
    for ckey, c in existing.items():
        if ckey not in found:
            s.delete(c)
    for ckey, (left_name, right_name, score, reasons) in found.items():
        if ckey in existing:
            existing[ckey].score, existing[ckey].reasons = round(score, 3), reasons
        else:
            s.add(MatchCandidate(id=_candidate_id(*ckey), kind=ckey[0], left_key=ckey[1], right_key=ckey[2], left_name=left_name,
                                 right_name=right_name, score=round(score, 3), reasons=reasons))
    s.flush()
    merged = sum(1 for sp in spellings.values() if sp.key) - len(clusters)
    return {"customers": len(parties), "counterparties": made, "spellings": len(spellings), "merged_spellings": merged,
            "relationships": len(edges), "open_questions": len(found), "customers_as_counterparties": len(owner_of)}


@system_work
def decide(s: Session, candidate_id: str, persona: Persona, same: bool, note: str = "") -> MatchCandidate:
    """Record a person's answer and rebuild, so the answer shows everywhere at once. Answers can be changed later."""
    if not persona.can(RESOLVE):
        raise ResolutionError(f"{persona.title} cannot decide entity matches")
    c = s.scalars(select(MatchCandidate).where(MatchCandidate.id == candidate_id)).first()
    if c is None:
        raise ResolutionError("No such question")
    c.status, c.decided_by, c.decided_at, c.note = "same" if same else "different", persona.id, now(), note.strip()
    s.flush()
    resolve(s)
    return c


def questions(s: Session, status: str = "open", limit: int = 200) -> list[dict]:
    q = select(MatchCandidate).order_by(MatchCandidate.score.desc(), MatchCandidate.id).limit(limit)
    q = q.where(MatchCandidate.status == "open") if status == "open" else q.where(MatchCandidate.status != "open") if status == "decided" else q
    return [{"id": c.id, "kind": c.kind, "question": KINDS[c.kind], "left": c.left_name, "right": c.right_name, "score": c.score,
             "left_ref": _ref(c.left_key), "right_ref": _ref(c.right_key),
             "reasons": c.reasons, "status": c.status, "decided_by": c.decided_by, "decided_at": c.decided_at.isoformat() if c.decided_at else None,
             "note": c.note} for c in s.scalars(q)]


def _ref(stable_key: str) -> dict:
    kind, _, value = stable_key.partition(":")
    return {"kind": "customer", "id": value} if kind == "party" else {"kind": "counterparty", "id": entity_id(value)}


def duplicates_of(s: Session, party_id: str) -> list[dict]:
    """Other customer records a person has confirmed, or the platform suspects, are this same customer."""
    me = f"party:{party_id}"
    out = []
    for c in s.scalars(select(MatchCandidate).where(MatchCandidate.kind == "duplicate_customer", MatchCandidate.status != "different",
                                                    (MatchCandidate.left_key == me) | (MatchCandidate.right_key == me))):
        other = c.right_key if c.left_key == me else c.left_key
        out.append({"party_id": other.partition(":")[2], "name": c.right_name if c.left_key == me else c.left_name, "status": c.status,
                    "score": c.score, "candidate_id": c.id})
    return out
