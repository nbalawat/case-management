"""Bring a workflow in from elsewhere, or take one out: one `Format` per file type, all through the same two calls.

    caseforge   the designer's own JSON (lossless; what drafts are saved as)
    case_type   the case-type YAML both engines run; any shipped or hand-written definition opens in the designer
    bpmn        BPMN 2.0 XML, the open standard most modelling tools export (Camunda, Flowable, Signavio, bpmn.io...)

Import never refuses a file for being incomplete. It brings in what it can, fills sensible defaults, and returns notes
in plain words about everything it had to guess; the designer's checks then say what is left to decide.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

import yaml

from ..cases import conditions as language
from ..cases.case_types import CaseTypeError, parse as parse_case_type
from . import conditions as cond
from .compiler import Context, compile_design, kind_of, slug, state_ids, to_yaml
from .layout import auto_layout
from .model import FORMAT, Design, Edge, Node, Position, Settings


class InterchangeError(ValueError):
    """The file could not be read at all."""


@dataclass(frozen=True)
class Format:
    id: str
    label: str
    extension: str
    media_type: str
    read: Callable[[str, Context], tuple[Design, list[str]]]
    write: Callable[[Design, Context], str]
    description: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "extension": self.extension, "media_type": self.media_type, "description": self.description}


FORMATS: dict[str, Format] = {}


def register(fmt: Format) -> Format:
    FORMATS[fmt.id] = fmt
    return fmt


def detect(content: str) -> str:
    head = content.lstrip()[:400]
    if head.startswith("{"):
        return "caseforge"
    if head.startswith("<"):
        return "bpmn"
    return "case_type"


def read(content: str, ctx: Context, fmt: str | None = None) -> tuple[Design, list[str]]:
    fmt = fmt or detect(content)
    if fmt not in FORMATS:
        raise InterchangeError(f"Unknown format '{fmt}'. Use one of: {', '.join(FORMATS)}")
    if len(content) > 2_000_000:
        raise InterchangeError("The file is larger than 2 MB")
    return FORMATS[fmt].read(content, ctx)


def write(design: Design, ctx: Context, fmt: str) -> str:
    if fmt not in FORMATS:
        raise InterchangeError(f"Unknown format '{fmt}'")
    return FORMATS[fmt].write(design, ctx)


# --- the designer's own JSON ---------------------------------------------------------------------------------------------
def _read_native(content: str, ctx: Context) -> tuple[Design, list[str]]:
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        raise InterchangeError(f"Not valid JSON: {e.msg} at line {e.lineno}") from e
    if not isinstance(raw, dict) or raw.get("format", FORMAT) != FORMAT:
        raise InterchangeError(f"This is not a CaseForge workflow file (expected format {FORMAT})")
    try:
        return Design.model_validate(raw), []
    except ValueError as e:
        raise InterchangeError(f"The workflow file is not valid: {e}") from e


def _write_native(design: Design, ctx: Context) -> str:
    return json.dumps(design.model_dump(mode="json"), indent=2)


# --- case-type YAML ------------------------------------------------------------------------------------------------------
def from_definition(ct: dict) -> Design:
    """Open any case-type definition in the designer: states become blocks, transitions become steps."""
    nodes = [Node(id="n_start", type="start", config={"label": "Case opened", "scenarios": list(ct.get("scenarios", []))})]
    edges = [Edge(id="e_start", source="n_start", target=f"n_{ct['initial_state']}")]
    four_eyes_from = {t["from"] for t in ct["transitions"] if t.get("four_eyes")}
    for sid, st in ct["states"].items():
        kind = st.get("kind", "open")
        if kind == "closed":
            nodes.append(Node(id=f"n_{sid}", type="outcome", config={"label": st["label"], "disposition": st.get("disposition") or sid, "state_id": sid}))
            continue
        btype = "wait" if kind == "wait" else "approval" if sid in four_eyes_from else "stage"
        cfg = {"label": st["label"], "queue": st.get("queue"), "sla_hours": st.get("sla_hours"), "state_id": sid,
               "tasks": [{"id": t["id"], "label": t["label"], "required": t.get("required", True)} for t in st.get("tasks", [])],
               "actions": list(st.get("on_enter") or [])}
        if st.get("description"):
            cfg["description"] = st["description"]
        if btype == "approval":
            cfg["approvers"] = sorted({r for t in ct["transitions"] if t["from"] == sid and t.get("four_eyes") for r in t["roles"]})
            cfg["four_eyes"] = True
        nodes.append(Node(id=f"n_{sid}", type=btype, config=cfg))
    for i, t in enumerate(ct["transitions"]):
        cfg = {"label": t["label"], "roles": list(t["roles"]), "requires": list(t.get("requires", [])), "four_eyes": bool(t.get("four_eyes"))}
        if t.get("when"):
            cfg["when"] = cond.decompile(t["when"])
        edges.append(Edge(id=f"e_{i}", source=f"n_{t['from']}", target=f"n_{t['to']}", config=cfg))
    qa = ct.get("qa") or {}
    esc = ct.get("escalation") or {}
    agents = ct.get("agents") or {}
    settings = Settings(deadline_label=ct.get("sla", {}).get("clock", "Case deadline"), deadline_days=ct.get("sla", {}).get("due_days", 30),
                        escalation_raise_priority=esc.get("raise_priority", True), escalation_notify=esc.get("notify", "team_lead"),
                        qa_sample_rate=float(qa.get("sample_rate", 0)), qa_dispositions=list(qa.get("dispositions", [])),
                        qa_reopen_to=f"n_{qa['reopen_to']}" if qa.get("reopen_to") else None,
                        agents=list(agents.get("on_create", [])), autonomy=agents.get("autonomy", "L2"))
    design = Design(key=ct["case_type"], title=ct["title"], domain=ct.get("domain", ""), description=ct.get("description", ""),
                    version=str(ct["version"]), nodes=nodes, edges=edges, settings=settings)
    return auto_layout(design)


def _read_case_type(content: str, ctx: Context) -> tuple[Design, list[str]]:
    try:
        ct = parse_case_type(content, "file")
    except CaseTypeError as e:
        raise InterchangeError(str(e)) from e
    return from_definition(ct), []


def _write_case_type(design: Design, ctx: Context) -> str:
    return to_yaml(compile_design(design, ctx))


# --- BPMN 2.0 ------------------------------------------------------------------------------------------------------------
BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI = "http://www.omg.org/spec/BPMN/20100524/DI"
DC = "http://www.omg.org/spec/DD/20100524/DC"
DI = "http://www.omg.org/spec/DD/20100524/DI"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
CF = "https://caseforge.dev/schema/bpmn/1"

TASKS = {"task", "userTask", "manualTask", "serviceTask", "scriptTask", "businessRuleTask", "sendTask", "callActivity", "subProcess"}
WAITS = {"receiveTask", "intermediateCatchEvent"}
GATEWAYS = {"exclusiveGateway", "inclusiveGateway", "eventBasedGateway", "parallelGateway", "complexGateway"}
AUTOMATIC = {"serviceTask", "scriptTask", "sendTask", "businessRuleTask"}
SCALE_X, SCALE_Y = 2.0, 1.6


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _name(el, fallback: str = "") -> str:
    return re.sub(r"\s+", " ", el.get("name") or "").strip() or fallback


def _iso_hours(text: str) -> float | None:
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", (text or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mins = (int(x or 0) for x in m.groups())
    return float(d * 24 + h + round(mins / 60, 2))


def _translate(expr: str) -> tuple[dict | None, str | None]:
    """A BPMN condition as a structured group, if it is written in terms this platform knows."""
    text = re.sub(r"^\s*[$#]\{(.*)\}\s*$", r"\1", expr or "").strip()
    if not text:
        return None, None
    match = "any" if re.search(r"\|\||\bor\b", text) else "all"
    parts = re.split(r"\s*(?:&&|\|\||\band\b|\bor\b)\s*", text)
    out = []
    for p in parts:
        p = p.strip().strip("()").replace("===", "==").replace("'", "").replace('"', "")
        try:
            fact, op, value = language.parse(p)
        except language.ConditionError:
            return None, f"The condition '{expr.strip()}' is not written in facts this platform knows, so the branch needs a condition."
        out.append({"fact": fact, "op": op, "value": int(value) if isinstance(value, float) and value.is_integer() else value})
    return {"match": match, "conditions": out}, None


def _match_queue(lane: str, ctx: Context) -> str | None:
    want = slug(lane)
    for qid, label in ctx.policy.queues.items():
        if want in (slug(qid), slug(label)) or slug(label).startswith(want) or want.startswith(slug(label)):
            return qid
    return None


def queue_roles(ctx: Context, queue: str | None) -> list[str]:
    """The roles people in a queue hold: a sensible default for who takes steps out of a stage that queue works."""
    if not queue:
        return []
    return sorted({r for p in ctx.policy.queue_members(queue) if not p.read_only for r in (p.roles or (p.role,))})


def _read_bpmn(content: str, ctx: Context) -> tuple[Design, list[str]]:
    if re.search(r"<!DOCTYPE|<!ENTITY", content, re.I):
        raise InterchangeError("BPMN files with a DOCTYPE or entities are not accepted")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise InterchangeError(f"Not valid XML: {e}") from e
    processes = [el for el in root.iter() if _local(el.tag) == "process"]
    if not processes:
        raise InterchangeError("No BPMN process found in the file")
    notes: list[str] = []
    proc = max(processes, key=lambda p: len(list(p)))
    if len(processes) > 1:
        notes.append(f"The file has {len(processes)} processes; imported '{_name(proc, proc.get('id', ''))}', the largest.")
    lane_of: dict[str, str] = {}
    for lane in (el for el in proc.iter() if _local(el.tag) == "lane"):
        for ref in lane:
            if _local(ref.tag) == "flowNodeRef" and ref.text:
                lane_of[ref.text.strip()] = _name(lane, lane.get("id", ""))
    bounds = {}
    for shape in (el for el in root.iter() if _local(el.tag) == "BPMNShape"):
        b = next((c for c in shape if _local(c.tag) == "Bounds"), None)
        if b is not None:
            bounds[shape.get("bpmnElement")] = (float(b.get("x", 0)), float(b.get("y", 0)))
    nodes: dict[str, Node] = {}
    gateways: dict[str, ET.Element] = {}
    timers: dict[str, float] = {}
    starts = 0
    unmatched_lanes = set()
    for el in proc.iter():
        tag, eid = _local(el.tag), el.get("id")
        if not eid:
            continue
        own = _own_config(el)
        if tag == "startEvent":
            starts += 1
            if starts > 1:
                notes.append(f"Extra start '{_name(el, eid)}' ignored: a case starts in one place.")
                continue
            nodes[eid] = Node(id=eid, type="start", config=own if own is not None else {"label": _name(el, "Case opened"), "scenarios": []})
        elif tag in TASKS or tag in WAITS:
            if tag == "intermediateCatchEvent" and _is_timer(el):
                notes.append(f"Timer '{_name(el, eid)}' became a waiting stage; its duration is the stage clock.")
            queue = _match_queue(lane_of[eid], ctx) if eid in lane_of else None
            if eid in lane_of and queue is None:
                unmatched_lanes.add(lane_of[eid])
            cfg = own if own is not None else {"label": _name(el, tag), "queue": queue, "sla_hours": 48 if tag in TASKS else 240, "tasks": [], "actions": []}
            if own is None:
                doc = next((c.text for c in el if _local(c.tag) == "documentation" and c.text), "")
                if doc:
                    cfg["description"] = doc.strip()[:2000]
                if tag in AUTOMATIC:
                    notes.append(f"'{cfg['label']}' was an automatic {tag} in BPMN. Here it is a stage people work; an automatic action may fit better.")
            btype = (el.get(f"{{{CF}}}block") or ("wait" if tag in WAITS else "stage"))
            nodes[eid] = Node(id=eid, type=btype, config=cfg)
        elif tag in GATEWAYS:
            gateways[eid] = el
            nodes[eid] = Node(id=eid, type="decision", config=own if own is not None else {"label": _name(el, "Decision"), "branches": [], "otherwise": False})
            if tag == "parallelGateway":
                notes.append(f"Parallel gateway '{_name(el, eid)}': a case is in one stage at a time here, so its paths became decision branches.")
        elif tag == "endEvent":
            label = _name(el, "Closed")
            nodes[eid] = Node(id=eid, type="outcome", config=own if own is not None else {"label": label, "disposition": slug(label, "closed")})
        elif tag == "boundaryEvent" and _is_timer(el):
            hours = _iso_hours(_timer_text(el))
            if hours and el.get("attachedToRef"):
                timers[el.get("attachedToRef")] = hours
                notes.append(f"Timer on '{el.get('attachedToRef')}' became its stage clock ({hours:g} hours). Late cases escalate as the "
                             "workflow settings say.")
    for tid, hours in timers.items():
        if tid in nodes and nodes[tid].type in ("stage", "wait", "approval"):
            nodes[tid].config["sla_hours"] = hours
    for lane in sorted(unmatched_lanes):
        notes.append(f"Lane '{lane}' matches no queue at this client; choose who works its stages.")
    flows = [el for el in proc.iter() if _local(el.tag) == "sequenceFlow"]
    timer_ids = {el.get("id") for el in proc.iter() if _local(el.tag) == "boundaryEvent"}
    edges: list[Edge] = []
    for f in flows:
        src, dst = f.get("sourceRef"), f.get("targetRef")
        if src in timer_ids:
            continue
        if src not in nodes or dst not in nodes:
            notes.append(f"Flow '{_name(f, f.get('id', ''))}' joins elements that were not imported and was skipped.")
            continue
        own = _own_config(f)
        edges.append(Edge(id=f.get("id") or f"e_{len(edges)}", source=src, target=dst, handle=f.get(f"{{{CF}}}handle"),
                          config=own if own is not None else {"label": _name(f)}))
        if src in gateways and own is None:
            gw = gateways[src]
            if gw.get("default") == f.get("id"):
                nodes[src].config["otherwise"] = True
                nodes[src].config["otherwise_label"] = _name(f, "Otherwise")
                edges[-1].handle = "otherwise"
                continue
            expr = next((c.text or "" for c in f if _local(c.tag) == "conditionExpression"), "")
            group, why = _translate(expr)
            if why:
                notes.append(why)
            bid = f"b_{f.get('id')}"
            nodes[src].config["branches"].append({"id": bid, "label": _name(f, nodes[dst].config.get("label", "Branch")),
                                                  **(group or {"match": "all", "conditions": []})})
            edges[-1].handle = bid
            edges[-1].config = {}
    _collapse_pass_through(nodes, edges)
    for e in edges:
        src, dst = nodes.get(e.source), nodes.get(e.target)
        if src is None or dst is None or kind_of(src) != "stage" or e.config.get("roles"):
            continue
        if not e.config.get("label"):
            e.config["label"] = f"Close as {dst.config['label'].lower()}" if kind_of(dst) == "outcome" else f"Send to {dst.config['label']}"
        e.config.setdefault("roles", queue_roles(ctx, src.config.get("queue")))
        e.config.setdefault("requires", [])
    title = _name(proc, "") or _name(root.find(f".//{{{BPMN}}}participant") if root.find(f".//{{{BPMN}}}participant") is not None else proc, "") \
        or proc.get("id", "Imported workflow")
    key = proc.get(f"{{{CF}}}key") or slug(title, "imported")[:58]
    if not re.match(r"^[a-z]", key):
        key = f"wf_{key}"
    design = Design(key=key, title=title[:120], domain=proc.get(f"{{{CF}}}domain", "Custom"), version=proc.get(f"{{{CF}}}version", "1.0.0"),
                    nodes=list(nodes.values()), edges=edges)
    settings = _own_config(proc)
    if settings is not None:
        design.settings = Settings.model_validate(settings)
    if bounds and all(n.id in bounds for n in design.nodes):
        x0 = min(x for x, _ in bounds.values())
        y0 = min(y for _, y in bounds.values())
        for n in design.nodes:
            x, y = bounds[n.id]
            n.position = Position(x=40 + (x - x0) * SCALE_X, y=80 + (y - y0) * SCALE_Y)
    else:
        auto_layout(design)
    if not starts:
        notes.append("The process has no start event; add a Start and connect it to the first stage.")
    return design, notes


def _collapse_pass_through(nodes: dict[str, Node], edges: list[Edge]) -> None:
    """A gateway with one way in or out only joins or forks drawing lines: route straight through it."""
    for gid in [k for k, n in nodes.items() if n.type == "decision"]:
        outs = [e for e in edges if e.source == gid]
        ins = [e for e in edges if e.target == gid]
        if len(outs) == 1 and ins:
            for e in ins:
                e.target = outs[0].target
            edges.remove(outs[0])
            del nodes[gid]


def _is_timer(el) -> bool:
    return any(_local(c.tag) == "timerEventDefinition" for c in el)


def _timer_text(el) -> str:
    for c in el.iter():
        if _local(c.tag) in ("timeDuration", "timeDate", "timeCycle") and c.text:
            return c.text
    return ""


def _own_config(el) -> dict | None:
    """Settings a CaseForge export put in the file, so a round trip loses nothing."""
    for ext in el:
        if _local(ext.tag) == "extensionElements":
            for c in ext:
                if c.tag == f"{{{CF}}}config" and c.text:
                    try:
                        return json.loads(c.text)
                    except json.JSONDecodeError:
                        return None
    return None


def _write_bpmn(design: Design, ctx: Context) -> str:
    for prefix, uri in (("bpmn", BPMN), ("bpmndi", BPMNDI), ("dc", DC), ("di", DI), ("xsi", XSI), ("caseforge", CF)):
        ET.register_namespace(prefix, uri)
    q = lambda ns, tag: f"{{{ns}}}{tag}"  # noqa: E731
    defs = ET.Element(q(BPMN, "definitions"), {"id": f"defs_{design.key}", "targetNamespace": "https://caseforge.dev/workflows",
                                                "exporter": "CaseForge workflow designer", "exporterVersion": "1"})
    proc = ET.SubElement(defs, q(BPMN, "process"), {"id": f"proc_{design.key}", "name": design.title, "isExecutable": "false",
                                                    q(CF, "key"): design.key, q(CF, "version"): design.version, q(CF, "domain"): design.domain})
    _ext(proc, design.settings.model_dump(mode="json"))
    queues = sorted({n.config["queue"] for n in design.nodes if n.config.get("queue")})
    if queues:
        lanes = ET.SubElement(proc, q(BPMN, "laneSet"), {"id": "lanes"})
        for qid in queues:
            lane = ET.SubElement(lanes, q(BPMN, "lane"), {"id": f"lane_{slug(qid)}", "name": ctx.policy.queues.get(qid, qid)})
            for n in design.nodes:
                if n.config.get("queue") == qid:
                    ET.SubElement(lane, q(BPMN, "flowNodeRef")).text = n.id
    tags = {"start": "startEvent", "stage": "userTask", "approval": "userTask", "wait": "receiveTask", "decision": "exclusiveGateway",
            "outcome": "endEvent"}
    compiled_when = {}
    for n in design.nodes:
        if n.type == "decision":
            for b in n.config.get("branches") or []:
                compiled_when[(n.id, b.get("id"))] = cond.compile_group(b)
    for n in design.nodes:
        attrs = {"id": n.id, "name": str(n.config.get("label", "")), q(CF, "block"): n.type}
        if n.type == "decision" and n.config.get("otherwise", True):
            default = next((e.id for e in design.out_of(n.id) if e.handle == "otherwise"), None)
            if default:
                attrs["default"] = default
        el = ET.SubElement(proc, q(BPMN, tags.get(n.type, "task")), attrs)
        if n.config.get("description"):
            ET.SubElement(el, q(BPMN, "documentation")).text = n.config["description"]
        _ext(el, n.config)
    for e in design.edges:
        label = e.config.get("label") or ""
        src = design.node(e.source)
        if src is not None and src.type == "decision" and not label:
            b = next((b for b in src.config.get("branches") or [] if b.get("id") == e.handle), None)
            label = b.get("label", "") if b else src.config.get("otherwise_label", "Otherwise") if e.handle == "otherwise" else ""
        attrs = {"id": e.id, "sourceRef": e.source, "targetRef": e.target, "name": label}
        if e.handle:
            attrs[q(CF, "handle")] = e.handle
        el = ET.SubElement(proc, q(BPMN, "sequenceFlow"), attrs)
        _ext(el, e.config)
        when = compiled_when.get((e.source, e.handle))
        if when:
            text = " && ".join(w if isinstance(w, str) else "(" + " || ".join(w["any"]) + ")" for w in when)
            ce = ET.SubElement(el, q(BPMN, "conditionExpression"), {q(XSI, "type"): "bpmn:tFormalExpression"})
            ce.text = "${" + text + "}"
    diagram = ET.SubElement(defs, q(BPMNDI, "BPMNDiagram"), {"id": "diagram"})
    plane = ET.SubElement(diagram, q(BPMNDI, "BPMNPlane"), {"id": "plane", "bpmnElement": proc.get("id")})
    size = {"start": (36, 36), "outcome": (36, 36), "decision": (50, 50)}
    centre = {}
    for n in design.nodes:
        w, h = size.get(n.type, (110, 80))
        x, y = n.position.x / SCALE_X, n.position.y / SCALE_Y
        centre[n.id] = (x + w / 2, y + h / 2)
        shape = ET.SubElement(plane, q(BPMNDI, "BPMNShape"), {"id": f"{n.id}_di", "bpmnElement": n.id})
        ET.SubElement(shape, q(DC, "Bounds"), {"x": f"{x:.0f}", "y": f"{y:.0f}", "width": str(w), "height": str(h)})
    for e in design.edges:
        if e.source in centre and e.target in centre:
            edge = ET.SubElement(plane, q(BPMNDI, "BPMNEdge"), {"id": f"{e.id}_di", "bpmnElement": e.id})
            for x, y in (centre[e.source], centre[e.target]):
                ET.SubElement(edge, q(DI, "waypoint"), {"x": f"{x:.0f}", "y": f"{y:.0f}"})
    ET.indent(defs)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(defs, encoding="unicode")


def _ext(el, config: dict) -> None:
    ext = ET.SubElement(el, f"{{{BPMN}}}extensionElements")
    ET.SubElement(ext, f"{{{CF}}}config").text = json.dumps(config, sort_keys=True)


register(Format("caseforge", "CaseForge workflow (JSON)", ".workflow.json", "application/json", _read_native, _write_native,
                "Everything the designer knows, including layout. Best for moving a workflow between clients or environments."))
register(Format("case_type", "Case type definition (YAML)", ".yaml", "application/yaml", _read_case_type, _write_case_type,
                "What the engines run. Readable, reviewable in a pull request, and accepted by the classic editor."))
register(Format("bpmn", "BPMN 2.0 (XML)", ".bpmn", "application/xml", _read_bpmn, _write_bpmn,
                "The open standard. Opens in Camunda Modeler, bpmn.io, Signavio and most process tools; imports from them too."))

__all__ = ["FORMATS", "Format", "InterchangeError", "detect", "from_definition", "queue_roles", "read", "register", "state_ids", "write"]
