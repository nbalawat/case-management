"""Offline planner: answers common questions with no model at all, so the demo runs on a laptop with no keys.

It is a deterministic phrase matcher, not a language model. It maps the words of a question onto the same tools
a model would call, then writes the answer from the tool results with fixed sentences. It handles one question
at a time (no follow-ups) and only phrasing it recognises; when it cannot place a question it says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..gateway import ModelTurn, ToolCall, ToolOutput, ToolSpec
from . import offline_policy
from .offline_landscape import named_object, plan as landscape_plan, write as landscape_write

CASE_ID = re.compile(r"\bcase-[0-9a-f]{10}\b")
COUNTRIES = {"united states": "US", "usa": "US", "mexico": "MX", "canada": "CA", "united kingdom": "GB", "uk": "GB", "britain": "GB",
             "germany": "DE", "france": "FR", "spain": "ES", "italy": "IT", "switzerland": "CH", "netherlands": "NL", "ireland": "IE",
             "china": "CN", "hong kong": "HK", "singapore": "SG", "japan": "JP", "india": "IN", "uae": "AE", "emirates": "AE",
             "dubai": "AE", "russia": "RU", "iran": "IR", "north korea": "KP", "syria": "SY", "cuba": "CU", "venezuela": "VE",
             "panama": "PA", "cayman": "KY", "cyprus": "CY", "malta": "MT", "turkey": "TR", "nigeria": "NG", "brazil": "BR",
             "colombia": "CO", "latvia": "LV", "belarus": "BY", "myanmar": "MM", "lebanon": "LB", "pakistan": "PK", "ukraine": "UA",
             "luxembourg": "LU", "bvi": "VG", "virgin islands": "VG", "seychelles": "SC", "belize": "BZ"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
# Words a question may use for a field it wants results split by. First match wins, so longer phrases come first.
GROUP_WORDS = [("customer country", "customer_country"), ("counterparty country", "counterparty_country"),
               ("case type", "case_type"), ("risk rating", "risk_rating"), ("workflow", "case_type"), ("type", "case_type"),
               ("stage", "stage"), ("status", "status"), ("priority", "priority"), ("owner", "owner"), ("analyst", "owner"),
               ("investigator", "owner"), ("person", "owner"), ("assignee", "owner"), ("queue", "queue"), ("team", "queue"),
               ("scenario", "scenario"), ("typology", "scenario"), ("typologies", "scenario"), ("country", "counterparty_country"),
               ("countries", "counterparty_country"), ("corridor", "counterparty_country"), ("jurisdiction", "counterparty_country"),
               ("risk", "risk_rating"), ("month", "MONTH"), ("week", "opened_week"), ("outcome", "outcome"),
               ("disposition", "outcome"), ("recommendation", "agent_recommendation")]
GENERIC = {"case", "cases", "investigation", "review", "claim", "customer", "transaction", "periodic", "and"}
MEASURE_TEXT = {"cases": "cases", "alerted_amount": "alerted", "avg_alerted_amount": "alerted per case on average",
                "avg_days_to_close": "days to close on average", "avg_age_days": "days old on average", "stage_overdue": "past the stage clock",
                "sars_filed": "SARs filed", "agent_agreement": "agreement with the agent", "share": "of the total"}


@dataclass
class Plan:
    tool: str
    args: dict
    reading: str  # how the question was read, shown to the person
    understood: bool = True
    notes: list[str] = field(default_factory=list)


def _has(q: str, *patterns: str) -> bool:
    return any(re.search(p, q) for p in patterns)


def _amount(number: str, unit: str | None) -> float:
    n = float(number.replace(",", ""))
    return n * {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((unit or "").lower(), 1)


def plan(question: str, context: dict, tools: set[str]) -> Plan:
    """Turn a question into one tool call."""
    q = " " + re.sub(r"\s+", " ", question.lower()).strip(" ?.!") + " "
    vocab, today = context["vocabulary"], date.fromisoformat(context["today"])

    ids = CASE_ID.findall(q)
    if ids:
        return Plan("case_detail", {"case_id": ids[0]}, f"a question about {ids[0]}")
    for name in context.get("customers", []):
        if f" {name.lower()}" in q:
            return Plan("customer", {"name_or_id": name}, f"a question about the customer {name}")
    quoted = re.search(r"customer (?:named |called )?[\"']([^\"']{2,60})[\"']", question, re.I)
    if quoted:
        return Plan("customer", {"name_or_id": quoted.group(1)}, f"a customer search for '{quoted.group(1)}'")
    if _has(q, r"\bworkload\b", r"\bcapacity\b", r"\boverloaded\b", r"\bbusiest\b", r"who has the most", r"who is carrying"):
        if "team_workload" in tools:
            return Plan("team_workload", {}, "a question about team workload")
        return Plan("team_workload", {}, "a question about team workload")  # the tool layer refuses and explains why

    filters: list[dict] = []
    said: list[str] = []

    def add(field_id: str, op: str, value, phrase: str) -> None:
        filters.append({"field": field_id, "op": op, "value": value})
        said.append(phrase)

    closed = _has(q, r"\bclosed\b", r"\bresolved\b", r"\bcompleted\b")
    if closed:
        add("status", "=", "closed", "closed")
    elif _has(q, r"\bopen\b", r"\bactive\b", r"\bbacklog\b", r"\boutstanding\b", r"\bin progress\b"):
        add("status", "=", "open", "open")

    types = []
    for title in vocab["case_types"]:
        words = [w for w in re.findall(r"[a-z]+", title.lower()) if w not in GENERIC and len(w) > 2]
        if title.lower() in q or any(re.search(rf"\b{w}\b", q) for w in words):
            types.append(title)
    if _has(q, r"\baml\b", r"\btm\b"):
        types += [t for t in vocab["case_types"] if "monitoring" in t.lower() and t not in types]
    if types and len(types) < len(vocab["case_types"]):
        add("case_type", "in", types, "case type " + " or ".join(types))

    m = re.search(r"\b(critical|high|medium|low)[- ]priority\b|\bpriority (?:is |of )?(critical|high|medium|low)\b", q)
    if m:
        add("priority", "=", m.group(1) or m.group(2), f"{m.group(1) or m.group(2)} priority")
    elif _has(q, r"\bcritical\b"):
        add("priority", "=", "critical", "critical priority")
    m = re.search(r"\b(high|medium|low)[- ]risk\b", q)
    if m:
        add("risk_rating", "=", m.group(1), f"{m.group(1)}-risk customers")
    if _has(q, r"\bpeps?\b", r"politically exposed"):
        add("pep", "=", True, "politically exposed customers")
    if _has(q, r"\bmy\b", r"\bmine\b", r"\bi own\b", r"assigned to me", r"\bdo i have\b", r"\bam i\b"):
        add("mine", "=", True, "assigned to you")
    if _has(q, r"\bunassigned\b", r"\bunowned\b", r"no owner", r"nobody (?:owns|has)"):
        add("owner", "=", "Unassigned", "unassigned")
    firsts = [p.split()[0].lower() for p in vocab["people"]]
    for person in vocab["people"]:
        first = person.split()[0].lower()
        if f" {person.lower()}" in q or (firsts.count(first) == 1 and re.search(rf"\b{re.escape(first)}(?:'s)?\b", q)):
            add("owner", "=", person, f"owned by {person}")
            break
    for queue in vocab["queues"]:
        if queue.lower() in q:
            add("queue", "=", queue, f"in the {queue} queue")
            break
    for stage in sorted({s for stages in vocab["stages"].values() for s in stages}, key=len, reverse=True):
        st = re.escape(stage.lower())
        if re.search(rf"\b(?:in|at)\s+(?:the\s+)?{st}\b|\b{st}\s+stage\b", q):
            add("stage", "=", stage, f"at the {stage} stage")
            break
    scenarios = [sc for sc in vocab["scenarios"] if re.search(rf"\b{re.escape(sc.replace('_', ' '))}\b", q.replace("_", " "))]
    if scenarios:
        add("scenario", "in", scenarios, "scenario " + " or ".join(s.replace("_", " ") for s in scenarios))

    sar_rate = _has(q, r"sar (?:rate|conversion|yield)", r"(?:share|percent\w*|proportion|rate) of .*\bsars?\b", r"produces? the most sars?",
                    r"most sars?")
    if _has(q, r"\bno sar\b", r"without a sar"):
        add("outcome", "=", "no sar", "closed with no SAR")
    elif not sar_rate and _has(q, r"\bsars?\b", r"suspicious activity reports?"):
        add("sar_filed", "=", True, "SAR filed")
    if _has(q, r"\boverdue\b", r"\blate\b", r"\bbreach\w*", r"past (?:the |their |its )?(?:stage )?clock", r"\bbehind\b", r"\bstuck\b"):
        add("stage_overdue", "=", True, "past the stage clock")
    if _has(q, r"\bdeadline\b"):
        add("past_deadline", "=", True, "past the regulatory deadline")
    if _has(q, r"\bwaiting\b", r"\bon hold\b", r"\bawaiting\b"):
        add("waiting", "=", True, "waiting on someone outside the team")
    if _has(q, r"\bdisagree\w*", r"\boverrul\w*", r"\boverrode\b", r"\boverrid\w*"):
        add("agent_agreed", "=", False, "the reviewer disagreed with the agent")
    m = re.search(r"agent (?:recommended|recommends|said|suggested) (?:to )?(escalat|clos)", q)
    if m:
        rec = "escalate" if m.group(1) == "escalat" else "close"
        add("agent_recommendation", "=", rec, f"the agent recommended {rec}")

    codes = {c for name, c in COUNTRIES.items() if re.search(rf"\b{name}\b", q)}
    codes |= {c for c in re.findall(r"\b[A-Z]{2}\b", question) if c in context.get("countries", [])}
    if codes:
        fld = "customer_country" if _has(q, r"customers? (?:in|from|based in|located in)") else "counterparty_country"
        add(fld, "in", sorted(codes), ("customers in " if fld == "customer_country" else "counterparties in ") + ", ".join(sorted(codes)))

    for m in re.finditer(r"\b(over|above|more than|greater than|at least|exceed\w*|under|below|less than|older than|younger than)\s+(\$?)([\d][\d,]*(?:\.\d+)?)\s*(k|m|million|thousand)?\b\s*(days?|weeks?)?", q):
        word, dollar, num, unit, time_unit = m.groups()
        op = "<" if word in ("under", "below", "less than", "younger than") else ">"
        if time_unit or word in ("older than", "younger than"):
            days = float(num.replace(",", "")) * (7 if (time_unit or "").startswith("week") else 1)
            add("age_days", op, days, f"{'older' if op == '>' else 'younger'} than {days:g} days")
        elif dollar or unit or float(num.replace(",", "")) >= 1000:
            amount = _amount(num, unit)
            add("alerted_amount", op, amount, f"alerted amount {'over' if op == '>' else 'under'} ${amount:,.0f}")

    when = "closed" if closed or _has(q, r"\bfiled?\b") else "opened"
    m = re.search(r"\b(?:last|past|previous)\s+(\d+)\s+(day|week|month)s?\b", q)
    if m:
        days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
        add(when, ">=", (today - timedelta(days=days)).isoformat(), f"{when} in the last {m.group(1)} {m.group(2)}s")
    elif _has(q, r"\bthis month\b"):
        add(f"{when}_month", "=", today.strftime("%Y-%m"), f"{when} this month")
    elif _has(q, r"\blast month\b"):
        prev = today.replace(day=1) - timedelta(days=1)
        add(f"{when}_month", "=", prev.strftime("%Y-%m"), f"{when} last month")
    elif _has(q, r"\bthis week\b"):
        add(when, ">=", (today - timedelta(days=today.weekday())).isoformat(), f"{when} this week")
    elif _has(q, r"\blast week\b"):
        monday = today - timedelta(days=today.weekday())
        add(when, ">=", (monday - timedelta(days=7)).isoformat(), f"{when} last week")
        filters.append({"field": when, "op": "<", "value": monday.isoformat()})
    elif _has(q, r"\btoday\b"):
        add(when, ">=", today.isoformat(), f"{when} today")
    elif _has(q, r"\bthis year\b"):
        add(when, ">=", today.replace(month=1, day=1).isoformat(), f"{when} this year")
    else:
        for i, name in enumerate(MONTHS, start=1):
            if re.search(rf"\b(?:in|during|for) {name}\b", q):
                year = today.year if i <= today.month else today.year - 1
                add(f"{when}_month", "=", f"{year}-{i:02d}", f"{when} in {name.title()} {year}")
                break

    group_by: list[str] = []
    for words, fld in GROUP_WORDS:
        if re.search(rf"\b(?:by|per|each|every|which|what|across|between)\s+(?:the\s+)?(?:\w+\s+)?{words}s?\b", q):
            fld = f"{when}_month" if fld == "MONTH" else fld
            if fld not in group_by and len(group_by) < 2:
                group_by.append(fld)
    if len(group_by) == 1:  # "by case type and status"
        for words, fld in GROUP_WORDS:
            if fld not in ("MONTH", *group_by) and re.search(rf"\b(?:and|then by)\s+(?:the\s+)?{words}s?\b", q):
                group_by.append(fld)
                break
    if not group_by and _has(q, r"\btrend\b", r"over time", r"\bmonthly\b", r"month by month"):
        group_by.append(f"{when}_month")
    if not group_by and _has(q, r"\bweekly\b", r"week by week"):
        group_by.append("opened_week")
    if "counterparty_country" in group_by and "customer_country" in group_by:
        group_by.remove("counterparty_country")

    measures: list[str] = []
    average = _has(q, r"\baverage\b", r"\bavg\b", r"\bmean\b", r"\btypical\w*\b", r"how long", r"\bturnaround\b")
    if average and _has(q, r"\bclose\b", r"\bresolve\b", r"how long", r"\bturnaround\b", r"\btake\b", r"\bduration\b", r"days to"):
        measures.append("avg_days_to_close")
    elif average and _has(q, r"\bage\b", r"\bold\b"):
        measures.append("avg_age_days")
    elif average and _has(q, r"\bamount\b", r"\bvalue\b", r"\bsize\b"):
        measures.append("avg_alerted_amount")
    elif _has(q, r"\bamounts?\b", r"\bvalue\b", r"\bexposure\b", r"how much", r"\bdollars?\b", r"total \$"):
        measures.append("alerted_amount")
    if _has(q, r"\bagree\w*\b", r"agreement"):
        measures.append("agent_agreement")
    if sar_rate:
        measures.append("sars_filed")
    measures.append("cases")
    if _has(q, r"\bshare\b", r"\bpercent\w*\b", r"\bproportion\b", r"%") and group_by:
        measures.append("share")

    wants_list = _has(q, r"\blist\b", r"\bshow\b", r"which cases", r"what cases", r"\btop \d+\b", r"\blargest\b", r"\bbiggest\b",
                      r"\boldest\b", r"\bnewest\b", r"\blatest\b", r"most recent", r"\bhighest\b", r"\bfind\b", r"what are my cases",
                      r"\bwhat should i\b", r"\bwork on\b")
    counting = _has(q, r"how many", r"\bcount\b", r"number of", r"\btotal\b", r"\bbreakdown\b", r"\bsplit\b", r"\bcompare\b",
                    r"how much", r"how often") or average
    todo = _has(q, r"\bwhat should i\b", r"\bwork on\b")
    if todo:
        if not any(f["field"] == "mine" for f in filters):
            add("mine", "=", True, "assigned to you")
        if not any(f["field"] == "status" for f in filters):
            add("status", "=", "open", "open")
    where = "; filters: " + ", ".join(said) if said else ""

    if wants_list and not group_by and not counting:
        sort_by, how = "opened", "most recent"
        if _has(q, r"\blargest\b", r"\bbiggest\b", r"\bhighest\b", r"\bvalue\b", r"\bamount\b"):
            sort_by, how = "alerted_amount", "largest by alerted amount"
        elif todo or _has(q, r"\boldest\b", r"\blongest\b"):
            sort_by, how = "age_days", "oldest"
        n = re.search(r"\b(?:top|first|show|list)\s+(\d+)\b", q)
        return Plan("list_cases", {"filters": filters, "sort_by": sort_by, "descending": True, "limit": int(n.group(1)) if n else 10},
                    f"the {how} cases{where}")

    understood = bool(filters or group_by or counting or measures != ["cases"])
    if not understood:
        group_by = ["case_type", "status"]
    split = "; split by " + " and ".join(g.replace("_", " ") for g in group_by) if group_by else ""
    return Plan("breakdown", {"group_by": group_by, "measures": measures, "filters": filters, "sort_by": measures[0], "descending": True},
                f"{' and '.join(MEASURE_TEXT[m] for m in measures)}{where}{split}", understood)


# --- writing the answer from the tool result -------------------------------------
def _n(v, measure: str = "") -> str:
    """A value with its measure, as words."""
    one = {"cases": "case", "sars_filed": "SAR filed"}
    return f"{_v(v, measure)} {one[measure] if v == 1 and measure in one else MEASURE_TEXT[measure]}"


def _v(v, measure: str = "") -> str:
    if v is None:
        return "n/a"
    if "amount" in measure:
        return f"${v:,.0f}"
    if measure in ("share", "agent_agreement"):
        return f"{v:g}%"
    if measure.startswith("avg"):
        return f"{v:g}"
    return f"{v:,}" if isinstance(v, int) else f"{v:g}"


def _write_breakdown(p: Plan, d: dict) -> str:
    measures, rows, total = d["measures"], d["rows"], d["matching_cases"]
    if not d["group_by"]:
        row = rows[0]
        parts = [f"**{_v(v, m)}** {'case' if m == 'cases' and v == 1 else MEASURE_TEXT[m]}" for m, v in zip(measures, row["values"])]
        text = ("No cases match." if total == 0 else " and ".join(parts) + ".")
        if row["case_ids"]:
            text += " Most recent: " + ", ".join(row["case_ids"]) + "."
        return text
    if not rows:
        return "No cases match."
    lead = measures[0]
    top = rows[0]
    lines = [f"{total:,} case{'' if total == 1 else 's'} match{'es' if total == 1 else ''}. **{' / '.join(map(str, top['key']))}** leads with {_n(top['values'][0], lead)}" + (f" (for example {top['case_ids'][0]})." if top["case_ids"] else ".")]
    for r in rows[1:4]:
        vals = ", ".join(_n(v, m) for m, v in zip(measures, r["values"]))
        lines.append(f"- {' / '.join(map(str, r['key']))}: {vals}" + (f" (e.g. {r['case_ids'][0]})" if r["case_ids"] else ""))
    if d["groups"] > 4:
        lines.append(f"The chart below has all {d['groups']} groups.")
    return "\n".join(lines)


def _write_list(p: Plan, d: dict) -> str:
    if not d["cases"]:
        return "No cases match."
    n = d["matching_cases"]
    lines = [f"{n:,} case{'' if n == 1 else 's'} match{'es' if n == 1 else ''}. "
             + ("Here it is:" if n == 1 else "The top ones:")]
    for c in d["cases"][:3]:
        lines.append(f"- {c['id']}: {c['customer']}, {c['stage']}, ${c['alerted_amount']:,.0f} alerted, {c['age_days']:g} day{'' if c['age_days'] == 1 else 's'} old, "
                     f"owner {c['owner']}")
    if len(d["cases"]) > 3:
        lines.append("The rest are in the table below.")
    return "\n".join(lines)


def _write_case(p: Plan, d: dict) -> str:
    c, cu = d["case"], d["customer"]
    lines = [f"**{c['id']}** is a {c['case_type']} case on {cu['name']} ({cu['risk_rating']} risk{', politically exposed' if cu['pep'] else ''}). "
             f"It is {c['status']} at **{c['stage']}**, {c['priority']} priority, owner {c['owner']}, opened {c['opened']}"
             + (f" and closed {c['closed']} with outcome {c['outcome']}." if c["closed"] else f", {c['age_days']:g} day{'' if c['age_days'] == 1 else 's'} old.")]
    if d["alerts"]:
        lines.append(f"{len(d['alerts'])} alert(s), ${c['alerted_amount']:,.0f} alerted:")
        lines += [f"- {a['scenario'].replace('_', ' ')}: {a['explanation']}" for a in d["alerts"][:5]]
    if d["agent"]:
        lines.append(f"The agent recommended **{d['agent']['recommendation']}** (confidence {d['agent']['confidence']:.0%}).")
    if c["stage_overdue"] == "yes":
        lines.append("It is past its stage clock.")
    open_requests = [r for r in d["information_requests"] if r["status"] == "open"]
    if open_requests:
        lines.append(f"{len(open_requests)} information request(s) are still open, to: " + ", ".join(r["to"] for r in open_requests) + ".")
    if d["related_case_ids"]:
        lines.append("Related: " + ", ".join(d["related_case_ids"]) + ".")
    return "\n".join(lines)


def _write_customer(p: Plan, d: dict) -> str:
    if not d["customers"]:
        return "No customer matches that name."
    lines = []
    for cu in d["customers"][:3]:
        lines.append(f"**{cu['name']}** ({cu['id']}): {cu['occupation'] or 'occupation not recorded'}, {cu['country']}, {cu['risk_rating']} risk"
                     f"{', politically exposed' if cu['pep'] else ''}, customer since {cu['customer_since']}. {cu['cases']} case(s), "
                     f"{cu['open_cases']} open, {cu['sars_filed']} SAR(s) filed, ${cu['alerted_amount']:,.0f} alerted in total.")
        lines += [f"- {c['id']}: {c['case_type']}, {c['stage']}, opened {c['opened']}" for c in cu["case_list"][:6]]
    if d["matching_customers"] > 3:
        lines.append(f"{d['matching_customers']} customers match; these are the ones with the most cases.")
    return "\n".join(lines)


def _write_team(p: Plan, d: dict) -> str:
    people = [x for x in d["people"] if x["open_cases"]]
    lines = ["Open work by person:"] + [f"- {x['name']} ({x['title']}): {x['open_cases']} open, {x['past_stage_clock']} past the stage clock"
                                        for x in people[:8]]
    waiting = sorted((x for x in d["queues"] if x["unassigned"]), key=lambda x: -x["unassigned"])
    if waiting:
        lines.append("Unassigned work: " + ", ".join(f"{x['queue']} ({x['unassigned']})" for x in waiting[:5]) + ".")
    if d["oldest_past_stage_clock"]:
        lines.append("Oldest cases past their stage clock: " + ", ".join(x["id"] for x in d["oldest_past_stage_clock"][:5]) + ".")
    return "\n".join(lines)


WRITERS = {"breakdown": _write_breakdown, "list_cases": _write_list, "case_detail": _write_case, "customer": _write_customer,
           "team_workload": _write_team}


class OfflineConversation:
    def __init__(self, tools: list[ToolSpec], context: dict):
        self.tools, self.context, self.plan, self.other, self.rules = {t.name for t in tools}, context, None, None, False

    def ask(self, question: str) -> ModelTurn:
        if "policy_search" in self.tools and offline_policy.wants_rules(question):
            self.rules = True
            return ModelTurn("tools", tool_calls=[ToolCall("offline-1", "policy_search", {"query": question})], model=OfflinePlanner.name)
        objects = self.context.get("objects") or {}
        self.other = landscape_plan(question, date.fromisoformat(self.context["today"]), objects) if objects else None
        if self.other and self.other["tool"] in self.tools:
            return ModelTurn("tools", tool_calls=[ToolCall("offline-1", self.other["tool"], self.other["args"])], model=OfflinePlanner.name)
        self.other = None
        self.plan = plan(question, self.context, self.tools)
        # A question about records this role may not read: ask for those records anyway, so the tool's refusal, with its
        # reason, is the answer rather than an unrelated figure about cases. SARs are the exception: a case's SAR outcome
        # is part of the case, so "how many SARs" stays a question about cases the person can see.
        denied = named_object(question, self.context.get("objects_denied") or ())
        if denied == "filings" and self.plan.understood and re.search(r"\bsars?\b", question.lower()):
            denied = None
        if denied and "breakdown" in self.tools:
            return ModelTurn("tools", tool_calls=[ToolCall("offline-1", "breakdown", {"object": denied})], model=OfflinePlanner.name)
        return ModelTurn("tools", tool_calls=[ToolCall("offline-1", self.plan.tool, self.plan.args)], model=OfflinePlanner.name)

    def results(self, outputs: list[ToolOutput]) -> ModelTurn:
        out = outputs[0]
        if out.error:
            return ModelTurn("answer", f"I could not answer that. {out.content['error']}", model=OfflinePlanner.name)
        if self.rules:
            return ModelTurn("answer", offline_policy.write(out.content), model=OfflinePlanner.name)
        if self.other:
            vocab = self.context["objects"][self.other["args"]["object"]]
            text = landscape_write(self.other, out.content, vocab)
            return ModelTurn("answer", f"{text}\n\nRead as: {self.other['reading']}.", model=OfflinePlanner.name)
        p = self.plan
        text = WRITERS[p.tool](p, out.content)
        if not p.understood:
            text = ("I could not place that question, so here is the overall picture instead. The offline planner only recognises "
                    "common phrasing; connect a model for free-form questions.\n" + text)
        return ModelTurn("answer", f"{text}\n\nRead as: {p.reading}.", model=OfflinePlanner.name)


class OfflinePlanner:
    name = "offline-planner"

    def start(self, *, instructions: str, tools: list[ToolSpec], context: dict, history: list[tuple[str, str]]) -> OfflineConversation:
        return OfflineConversation(tools, context)
