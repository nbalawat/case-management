"""The offline planner's reading of questions about records other than cases: transactions, alerts, filings, holds,
disputes, KYC, agents and the rest. Like the case planner it is a phrase matcher, not a model: it picks the object
the question names, the fields it splits by, a few filters and the measure, and asks the same tools a model would.
Anything it cannot place goes back to the case planner, which says so.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# Words that name each object, longest first within an object. Cases are the case planner's.
OBJECT_WORDS: list[tuple[str, str]] = [
    ("transactions", r"\btransactions?\b|\bpayments?\b|\bwires?\b|\btransfers?\b|\bdeposits?\b|\bwithdrawals?\b"),
    ("alerts", r"\balerts?\b"),
    ("filings", r"\bfilings?\b|\bregulatory reports?\b|\bctrs?\b|\bstrs?\b|\bsars? (?:filed|submitted|prepared|due|pending|rejected)\b|"
                r"\b(?:filed|submitted|pending|rejected|overdue) sars?\b|\breports? to (?:the )?regulator"),
    ("holds", r"\bholds?\b|\bblocked payments?\b|\bheld payments?\b|\bfrozen\b"),
    ("disputes", r"\bdisputes?\b|\bchargebacks?\b|\bclaims? (?:loss|losses)\b|\blosses\b|\brecover(?:y|ies|ed)\b"),
    ("risk_assessments", r"\brisk (?:assessments?|ratings? overrid\w*|scores?)\b|\bkyc (?:ratings?|bands?|assessments?)\b|\boverrid\w* (?:of )?(?:the )?risk"),
    ("kyc_triggers", r"\bkyc triggers?\b|\btriggers?\b|\btrigger events?\b"),
    ("agent_actions", r"\bagent (?:actions?|ledger|steps?)\b|\bagents? acted\b|\bacted on (?:its|their) own\b"),
    ("agent_runs", r"\bagent runs?\b|\bagents?'? recommendations?\b|\boverride rate\b|\bagents?'? (?:accuracy|confidence)\b"),
    ("detection_changes", r"\bdetection changes?\b|\btuning changes?\b|\bscenario changes?\b|\bthreshold changes?\b"),
    ("lookbacks", r"\blookbacks?\b"),
    ("pipeline_runs", r"\bpipeline runs?\b|\bdata loads?\b|\bpipelines?\b"),
    ("customers", r"\bcustomers?\b|\bclients? of the bank\b"),
]
COUNTRY = re.compile(r"\b(?:to|from|in|with)\s+([A-Z]{2})\b")


def _has(q: str, *patterns: str) -> bool:
    return any(re.search(p, q) for p in patterns)


def _amount(num: str, unit: str | None) -> float:
    return float(num.replace(",", "")) * {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((unit or "").lower(), 1)


def which_object(q: str, objects: dict) -> str | None:
    """The object the question is about. 'Cases' wins when the question names cases at all."""
    # "by case type" and "alert-to-case rate" name a field of the object, not a question about cases
    if re.search(r"(?<![-\w])cases?\b(?! type| id| behind| rate)", q):
        return None
    for oid, pattern in OBJECT_WORDS:
        if oid in objects and re.search(pattern, q):
            if oid == "customers" and any(re.search(p, q) for o, p in OBJECT_WORDS if o not in ("customers",) and o in objects):
                continue  # "transactions of high-risk customers" is about transactions
            return oid
    return None


def named_object(question: str, objects) -> str | None:
    """The object a question names, among `objects` (any container of object ids)."""
    return which_object(" " + re.sub(r"\s+", " ", question.lower()).strip(" ?.!") + " ", objects)


def _date_dim(vocab: dict) -> str | None:
    return next((k for k, v in vocab["fields"].items() if "(date" in v), None)


def _month_dim(vocab: dict) -> str | None:
    return next((k for k in vocab["fields"] if k.endswith("_month")), None)


def plan(question: str, today: date, objects: dict) -> dict | None:
    """A tool call for a question about a landscape object, or None to leave it to the case planner.
    `objects` maps object id to its vocabulary (Obj.vocabulary())."""
    q = " " + re.sub(r"\s+", " ", question.lower()).strip(" ?.!") + " "
    oid = which_object(q, objects)
    if oid is None:
        return None
    vocab = objects[oid]
    fields = vocab["fields"]
    filters: list[dict] = []
    said: list[str] = []

    def add(fld: str, op: str, value, phrase: str) -> None:
        if fld in fields:
            filters.append({"field": fld, "op": op, "value": value})
            said.append(phrase)

    # amounts
    for m in re.finditer(r"\b(over|above|more than|greater than|at least|exceed\w*|under|below|less than)\s+\$?([\d][\d,]*(?:\.\d+)?)\s*(k|m|million|thousand)?\b", q):
        op = "<" if m.group(1) in ("under", "below", "less than") else ">"
        amount = _amount(m.group(2), m.group(3))
        add("amount", op, amount, f"amount {'over' if op == '>' else 'under'} ${amount:,.0f}")
    # who and where
    m = re.search(r"\b(high|medium|low)[- ]risk\b", q)
    if m:
        add("customer_risk_rating", "=", m.group(1), f"{m.group(1)}-risk customers")
    if _has(q, r"\bpeps?\b", r"politically exposed"):
        add("customer_pep", "=", True, "politically exposed customers")
    codes = sorted({c for c in COUNTRY.findall(question) if len(c) == 2})
    if codes:
        add("counterparty_country" if "counterparty_country" in fields else "customer_country", "in", codes, "country " + ", ".join(codes))
    for word in ("cash", "wire", "ach", "card", "p2p"):
        if re.search(rf"\b{word}\b", q):
            add("channel" if "channel" in fields else "rail", "=", word, word)
            break
    if _has(q, r"\bincoming\b", r"\breceived\b", r"\binbound\b"):
        add("direction", "=", "in", "incoming")
    elif _has(q, r"\boutgoing\b", r"\bsent\b", r"\boutbound\b"):
        add("direction", "=", "out", "outgoing")
    # state words, matched against the object's own vocabulary
    if _has(q, r"\boverdue\b", r"\blate\b", r"past (?:the |their |its )?(?:deadline|due|review)"):
        for f in ("past_due", "review_overdue", "credit_overdue", "overdue"):
            if f in fields:
                add(f, "=", True, fields[f].split(" (")[0].lower())
                break
    if oid == "holds" and _has(q, r"\bactive\b", r"\bcurrent\b", r"\bstill held\b"):
        add("status", "=", "active", "active")
    if oid == "filings":
        m = re.search(r"\b(sars?|ctrs?|strs?)\b", q)
        if m:
            add("kind", "=", m.group(1).rstrip("s").upper(), m.group(1).rstrip("s").upper())
        if _has(q, r"\brejected\b"):
            add("status", "=", "rejected", "rejected")
        elif _has(q, r"\bnot (?:yet )?(?:sent|submitted)\b", r"\bpending\b", r"\bwaiting\b"):
            add("status", "=", "prepared", "not yet sent")
    asks_rate = _has(q, r"\bhow often\b", r"\brate\b", r"\bshare\b", r"\bpercent\w*", r"\bproportion\b")
    if oid == "risk_assessments" and _has(q, r"\boverrid\w*") and not asks_rate:  # "how often ... overridden" is the rate, not a filter
        add("overridden", "=", True, "overridden by a person")
    if oid == "risk_assessments" and _has(q, r"\bcurrent\b"):
        add("current", "=", True, "current")
    if oid == "alerts" and _has(q, r"\bauto[- ]?closed\b", r"closed by (?:an |the )?agents?"):
        add("closed_by_agent", "=", True, "closed by an agent")
    if oid == "alerts" and _has(q, r"\bhibernat\w*", r"\bwaiting\b", r"\bintake\b"):
        add("status", "=", "hibernated", "waiting in intake")
    if oid == "agent_actions" and _has(q, r"\brefused\b"):
        add("outcome", "=", "refused", "refused")
    # time
    dd = _date_dim(vocab)
    m = re.search(r"\b(?:last|past|previous)\s+(\d+)\s+(day|week|month)s?\b", q)
    if dd and m:
        days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
        add(dd, ">=", (today - timedelta(days=days)).isoformat(), f"in the last {m.group(1)} {m.group(2)}s")
    elif dd and _has(q, r"\bthis month\b"):
        add(dd, ">=", today.replace(day=1).isoformat(), "this month")
    elif dd and _has(q, r"\bthis week\b"):
        add(dd, ">=", (today - timedelta(days=today.weekday())).isoformat(), "this week")

    # split by
    group_by: list[str] = []
    labels = {k: v.split(" (")[0].lower() for k, v in fields.items() if "filter and sort only" not in v}
    for m in re.finditer(r"\b(?:by|per|each|across)\s+(?:the\s+)?([a-z][a-z ]{1,30}?)(?=\s(?:and|then|for|in|over|with|of|last|this)\b|\s*$)", q):
        phrase = m.group(1).strip()
        if re.match(r"(?:a|an|the|its|their|one)\b", phrase):  # "overridden by a person" is not a split
            continue
        best = next((k for k, lab in labels.items() if phrase in (lab, k.replace("_", " ")) or lab.endswith(phrase) or k.endswith(phrase.replace(" ", "_"))), None)
        if best and best not in group_by and len(group_by) < 2:
            group_by.append(best)
    if not group_by:  # "which scenario has the lowest rate" splits by scenario
        m = re.search(r"\b(?:which|what)\s+([a-z]+(?: [a-z]+)?)\s+(?:has|have|had|is|are|gets?|shows?|produces?|sees?)\b", q)
        if m:
            phrase = m.group(1).strip()
            best = next((k for k, lab in labels.items() if phrase in (lab, k.replace("_", " ")) or lab.endswith(phrase)), None)
            if best:
                group_by.append(best)
    if not group_by and _has(q, r"\btrend\b", r"over time", r"\bmonthly\b", r"month by month", r"\bper month\b"):
        md = _month_dim(vocab)
        if md:
            group_by.append(md)

    # what to measure
    measures = list(vocab["measures"])
    chosen = [measures[0]]
    if _has(q, r"\baverage\b", r"\bavg\b", r"\bmean\b", r"\btypical\b"):
        chosen = [next((x for x in measures if x.startswith("avg")), measures[0])]
    elif _has(q, r"\btotal\b", r"how much", r"\bvalue\b", r"\bsum\b", r"\bexposure\b", r"\bamount\b"):
        chosen = [next((x for x in measures if x.startswith("total")), measures[0])]
    elif asks_rate:
        chosen = [next((x for x in measures if x.endswith(("rate", "share", "agreement"))), measures[0])]
    if _has(q, r"\bloss(?:es)?\b") and "total_loss" in measures:
        chosen = ["total_loss"]
    if _has(q, r"\brecover\w*") and "total_recovered" in measures:
        chosen = ["total_recovered"] + (["recovery_rate"] if "recovery_rate" in measures else [])
    if _has(q, r"\boverride rate\b", r"\boverrid\w*") and "override_rate" in measures:
        chosen = ["override_rate"]
    if _has(q, r"\bagree\w*") and "agreement" in measures:
        chosen = ["agreement"]
    if chosen[0] != measures[0]:
        chosen.append(measures[0])

    where = "; filters: " + ", ".join(said) if said else ""
    wants_list = _has(q, r"\blist\b", r"\bshow\b", r"\btop \d+\b", r"\blargest\b", r"\bbiggest\b", r"\bhighest\b", r"\blatest\b",
                      r"\bnewest\b", r"\boldest\b", r"most recent", r"\bwhich\b", r"\bwhat are\b")
    counting = _has(q, r"how many", r"\bcount\b", r"number of", r"\btotal\b", r"how much", r"\baverage\b", r"\brate\b", r"\bshare\b",
                    r"\bpercent", r"\bbreakdown\b", r"\bsplit\b", r"\bcompare\b")
    if wants_list and not group_by and not counting:
        sort = _date_dim(vocab) or next(iter(fields))
        how = "most recent"
        if _has(q, r"\blargest\b", r"\bbiggest\b", r"\bhighest\b") and "amount" in fields:
            sort, how = "amount", "largest"
        elif _has(q, r"\boldest\b"):
            how = "oldest"
        n = re.search(r"\b(?:top|first|show|list)\s+(\d+)\b", q)
        return {"tool": "list_records", "args": {"object": oid, "filters": filters, "sort_by": sort, "descending": how != "oldest",
                                                 "limit": min(int(n.group(1)), 25) if n else 10},
                "reading": f"the {how} {vocab['label']}{where}"}
    split = "; split by " + " and ".join(labels.get(g, g) for g in group_by) if group_by else ""
    lowest = bool(group_by) and _has(q, r"\blowest\b", r"\bleast\b", r"\bfewest\b", r"\bsmallest\b", r"\bbottom\b", r"\bworst\b")
    args = {"object": oid, "group_by": group_by, "measures": chosen, "filters": filters}
    if lowest:
        args |= {"sort_by": chosen[0], "descending": False}
    return {"tool": "breakdown", "args": args,
            "reading": f"{' and '.join(vocab['measures'][x].lower() for x in chosen)} of {vocab['label']}{where}{split}{', lowest first' if lowest else ''}"}


def _v(value, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "usd":
        return f"${value:,.2f}" if abs(value) < 100 else f"${value:,.0f}"
    if unit == "percent":
        return f"{value:g}%"
    return f"{value:,}" if isinstance(value, int) else f"{value:g}"


MONEY = re.compile(r"amount|loss|recovered|credited|value")
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?$")


def _cell(field: str, v) -> str:
    """A field of a listed record as a sentence would write it: money as dollars, timestamps without the T."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and MONEY.search(field):
        return _v(v, "usd")
    if isinstance(v, str) and STAMP.match(v):
        return STAMP.sub(r"\1 \2", v)
    return str(v)


def write(call: dict, d: dict, vocab: dict) -> str:
    """The answer, in fixed sentences, from the tool result."""
    label = vocab["label"]
    if call["tool"] == "list_records":
        if not d["records"]:
            return f"No {label} match."
        n = d["matching"]
        lines = [f"{n:,} {label} match. " + ("Here it is:" if n == 1 else "The top ones:")]
        for r in d["records"][:5]:
            bits = [f"{k.replace('_', ' ')} {_cell(k, v)}" for k, v in r.items() if k not in ("id", "customer_id", "case_id") and v not in (None, "")][:6]
            lines.append(f"- {r.get('id') or r.get('customer_id')}: " + ", ".join(str(b) for b in bits))
        if len(d["records"]) > 5:
            lines.append("The rest are in the table below.")
        return "\n".join(lines)
    measures, units = d["measures"], d["units"]
    names = [vocab["measures"][m].lower() for m in measures]
    if not d["group_by"]:
        row = d["rows"][0] if d["rows"] else {"values": [0] * len(measures), "ids": []}
        text = "; ".join(f"{n}: **{_v(v, u)}**" for n, v, u in zip(names, row["values"], units)) + "."
        if d["matching"] == 0:
            return f"No {label} match."
        if row["ids"]:
            text += " For example: " + ", ".join(row["ids"][:3]) + "."
        return text[0].upper() + text[1:]
    if not d["rows"]:
        return f"No {label} match."
    top = d["rows"][0]
    verb = "is lowest with" if call["args"].get("descending") is False else "leads with"
    lines = [f"{d['matching']:,} {label} match. **{' / '.join(map(str, top['key']))}** {verb} {_v(top['values'][0], units[0])} ({names[0]})"
             + (f", for example {top['ids'][0]}." if top["ids"] else ".")]
    for r in d["rows"][1:5]:
        lines.append(f"- {' / '.join(map(str, r['key']))}: " + ", ".join(f"{_v(v, u)} {n}" for v, u, n in zip(r["values"], units, names)))
    if d["groups"] > 5:
        lines.append(f"The chart below has all {d['groups']} groups.")
    return "\n".join(lines)
