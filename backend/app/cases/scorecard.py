"""QA scorecards: every sampled case is scored against the client's own criteria, and shortfalls carry a root cause."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reference
from ..domain.models import Case, now
from ..domain.personas import Persona
from ..visibility import system_work
from .qa import QA_CASE_TYPE
from .service import TransitionError, record

MARKS = {"met": 1.0, "partly": 0.5, "not_met": 0.0}
PASS_MARK = 80
POSITIVE = {"sar_filed", "fraud_confirmed", "true_match", "exit", "declined"}  # outcomes that mean the concern was real


@system_work
def score(s: Session, review: Case, persona: Persona, answers: dict, ts: datetime | None = None) -> dict:
    """Record the reviewer's marks. Every criterion needs a mark, and anything short of met needs a root cause."""
    if review.case_type != QA_CASE_TYPE or review.closed is not None:
        raise TransitionError("Only an open QA review can be scored")
    if persona.read_only or review.queue not in persona.queues:
        raise TransitionError("Scoring needs a reviewer from the QA queue")
    criteria = reference.codes(s, "qa_criteria")
    causes = {c["code"] for c in reference.codes(s, "qa_root_causes")}
    clean, earned, total = {}, 0.0, 0.0
    for c in criteria:
        a = answers.get(c["code"]) or {}
        mark = a.get("mark")
        if mark not in MARKS:
            raise TransitionError(f"Mark every criterion. Missing: {c['label']}")
        cause = a.get("root_cause") or ""
        if mark != "met" and cause not in causes:
            raise TransitionError(f"Choose a root cause for: {c['label']}")
        clean[c["code"]] = {"mark": mark, "root_cause": cause if mark != "met" else "", "comment": str(a.get("comment") or "")[:500]}
        weight = float(c.get("weight", 1))
        earned, total = earned + MARKS[mark] * weight, total + weight
    card = {"answers": clean, "score": round(100 * earned / total) if total else 0, "pass_mark": PASS_MARK, "by": persona.id, "at": (ts or now()).isoformat()}
    review.data = dict(review.data or {}) | {"scorecard": card}
    record(s, review.id, persona.id, "human", "qa.scored", {"score": card["score"]}, ts=ts)
    return card


def blocks(review: Case, passing: bool) -> str | None:
    """Why this review cannot be decided yet, as far as its scorecard goes."""
    card = (review.data or {}).get("scorecard")
    if not card:
        return "Score the case against the QA criteria first"
    if passing and card["score"] < card["pass_mark"]:
        return f"The scorecard is {card['score']}%, below the pass mark of {card['pass_mark']}%"
    return None


def report(s: Session) -> dict:
    """What QA found: how often work passes, where it falls short and why, and how the agent's advice fared under review."""
    reviews = [c for c in s.scalars(select(Case).where(Case.case_type == QA_CASE_TYPE)) if (c.data or {}).get("scorecard")]
    criteria = {c["code"]: c["label"] for c in reference.codes(s, "qa_criteria", include_retired=True)}
    by_person: dict[str, list[tuple[int, bool]]] = defaultdict(list)
    by_criterion: dict[str, Counter] = defaultdict(Counter)
    causes: Counter = Counter()
    agent = {"sampled": 0, "followed": 0, "followed_failed": 0, "overridden": 0, "overridden_failed": 0}
    parents = {p.id: p for p in s.scalars(select(Case).where(Case.id.in_([r.parent_id for r in reviews if r.parent_id])))} if reviews else {}
    for r in reviews:
        card, failed = r.data["scorecard"], r.disposition == "qa_failed"
        by_person[(r.data or {}).get("closed_by") or "unknown"].append((card["score"], failed))
        for code, a in card["answers"].items():
            by_criterion[code][a["mark"]] += 1
            if a["root_cause"]:
                causes[a["root_cause"]] += 1
        parent = parents.get(r.parent_id)
        if parent is not None and parent.agent_recommendation and r.closed is not None:
            # A failed review reopens the case, so what was decided is read from the review's own record of who closed it.
            decided = (r.data or {}).get("closed_as") or parent.disposition
            followed = (parent.agent_recommendation == "escalate") == (decided in POSITIVE) if decided else None
            if followed is not None:
                agent["sampled"] += 1
                key = "followed" if followed else "overridden"
                agent[key] += 1
                agent[f"{key}_failed"] += int(failed)
    decided = [r for r in reviews if r.closed is not None]
    scores = [r.data["scorecard"]["score"] for r in reviews]
    return {
        "reviews": len(reviews), "decided": len(decided), "passed": sum(1 for r in decided if r.disposition == "qa_passed"),
        "failed": sum(1 for r in decided if r.disposition == "qa_failed"), "average_score": round(sum(scores) / len(scores)) if scores else None,
        "pass_mark": PASS_MARK,
        "by_person": sorted(({"person": p, "reviews": len(v), "average_score": round(sum(x for x, _ in v) / len(v)), "failed": sum(f for _, f in v)}
                             for p, v in by_person.items()), key=lambda x: x["average_score"]),
        "by_criterion": [{"code": k, "label": criteria.get(k, k), "met": v["met"], "partly": v["partly"], "not_met": v["not_met"]} for k, v in by_criterion.items()],
        "root_causes": [{"code": k, "label": reference.label(s, "qa_root_causes", k), "count": n} for k, n in causes.most_common()],
        "agent": agent,
    }
