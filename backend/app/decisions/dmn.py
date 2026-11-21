"""Decision tables as DMN 1.3 XML, the OMG standard, so a table can move to and from Camunda, Kogito, Trisotech and
other DMN tools. Only decision tables are exchanged; decision requirement graphs and boxed expressions are not.

A CaseForge table carries a few things DMN has no place for (what it is bound to, the case types it covers). They
travel as an extension element in our own namespace, which other tools keep and ignore.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from .feel import NUMBER, TEXT, BOOL, to_feel
from .model import DecisionError, DecisionTable, from_dict, to_yaml

NS = "https://www.omg.org/spec/DMN/20191111/MODEL/"
CF = "https://caseforge.dev/dmn/extensions"
_TYPES = {NUMBER: "number", TEXT: "string", BOOL: "boolean"}
_KINDS = {v: k for k, v in _TYPES.items()} | {"double": NUMBER, "integer": NUMBER, "long": NUMBER}
MAX_BYTES = 2_000_000

ET.register_namespace("", NS)
ET.register_namespace("caseforge", CF)


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def export(t: DecisionTable) -> str:
    root = ET.Element(_q("definitions"), {"id": f"defs_{t.id}", "name": t.title, "namespace": CF})
    decision = ET.SubElement(root, _q("decision"), {"id": t.id, "name": t.title})
    if t.description:
        ET.SubElement(decision, _q("description")).text = t.description
    ext = ET.SubElement(decision, _q("extensionElements"))
    ET.SubElement(ext, f"{{{CF}}}settings").text = json.dumps(
        {"version": t.version, "context": t.context, "binding": t.binding, "enabled": t.enabled, "case_types": list(t.case_types)}, sort_keys=True)
    attrs = {"id": f"dt_{t.id}", "hitPolicy": t.hit_policy}
    if t.aggregation:
        attrs["aggregation"] = t.aggregation
    table = ET.SubElement(decision, _q("decisionTable"), attrs)
    for c in t.inputs:
        i = ET.SubElement(table, _q("input"), {"id": f"in_{c.name}", "label": c.label})
        e = ET.SubElement(i, _q("inputExpression"), {"id": f"ie_{c.name}", "typeRef": _TYPES[c.kind]})
        ET.SubElement(e, _q("text")).text = c.name
        if c.values:
            ET.SubElement(ET.SubElement(i, _q("inputValues")), _q("text")).text = ",".join(to_feel(v) for v in c.values)
    for c in t.outputs:
        o = ET.SubElement(table, _q("output"), {"id": f"out_{c.name}", "name": c.name, "label": c.label, "typeRef": _TYPES[c.kind]})
        if c.values:
            ET.SubElement(ET.SubElement(o, _q("outputValues")), _q("text")).text = ",".join(to_feel(v) for v in c.values)
        if c.default is not None:
            ET.SubElement(ET.SubElement(o, _q("defaultOutputEntry")), _q("text")).text = to_feel(c.default)
    for r in t.rules:
        rule = ET.SubElement(table, _q("rule"), {"id": r.id})
        if r.note:
            ET.SubElement(rule, _q("description")).text = r.note
        for c in t.inputs:
            src = r.tests[c.name].source
            ET.SubElement(ET.SubElement(rule, _q("inputEntry"), {"id": f"{r.id}_{c.name}"}), _q("text")).text = "-" if src in ("", "-") else _feel_test(src, c.kind)
        for c in t.outputs:
            ET.SubElement(ET.SubElement(rule, _q("outputEntry"), {"id": f"{r.id}_{c.name}"}), _q("text")).text = to_feel(r.outputs[c.name])
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _feel_test(src: str, kind: str) -> str:
    """Our cells accept bare words for text; strict FEEL wants them quoted."""
    if kind != TEXT:
        return src
    from .feel import _split  # noqa: PLC0415  shared tokeniser

    def quote(part: str) -> str:
        p = part.strip()
        if p.startswith(('"', "<", ">", "=", "!", "[", "(", "]")) or p.lower() in ("null", "true", "false"):
            return p
        return f'"{p}"'

    if src.startswith("not(") and src.endswith(")"):
        return "not(" + ", ".join(quote(p) for p in _split(src[4:-1])) + ")"
    return ", ".join(quote(p) for p in _split(src))


def _ident(raw: str | None, fallback: str) -> str:
    """Other tools allow ids such as Decision_0x9 or customer.age; ours are lower-case identifiers."""
    s = re.sub(r"[^a-z0-9_]+", "_", (raw or "").strip().lower()).strip("_")
    if not s or not s[0].isalpha():
        s = f"{fallback}_{s}".strip("_")
    return s[:60]


def _text(el, path: str) -> str | None:
    found = el.find(path)
    return found.text.strip() if found is not None and found.text is not None else None


def _values(text: str | None, kind: str) -> list:
    if not text:
        return []
    from .feel import _split, literal  # noqa: PLC0415

    return [literal(p, kind) for p in _split(text)]


def parse(xml: str | bytes) -> DecisionTable:
    """Read the first decision table in a DMN document. Documents with a DTD are refused (no entity expansion)."""
    raw = xml.encode() if isinstance(xml, str) else xml
    if len(raw) > MAX_BYTES:
        raise DecisionError("The DMN file is larger than 2 MB")
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise DecisionError("DMN files with a DOCTYPE are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise DecisionError(f"Not valid XML: {e}") from None
    ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    q = (lambda tag: f"{{{ns}}}{tag}") if ns else (lambda tag: tag)
    decision = root.find(q("decision"))
    table = decision.find(q("decisionTable")) if decision is not None else None
    if table is None:
        raise DecisionError("No decision table found in the DMN file")
    settings = {}
    ext = decision.find(f"{q('extensionElements')}/{{{CF}}}settings")
    if ext is not None and ext.text:
        try:
            settings = json.loads(ext.text)
        except ValueError:
            settings = {}
    inputs, outputs = [], []
    for i in table.findall(q("input")):
        expr = i.find(q("inputExpression"))
        name = _ident(_text(expr, q("text")) if expr is not None else None, "input")
        kind = _KINDS.get((expr.get("typeRef") or "string") if expr is not None else "string", TEXT)
        inputs.append({"name": name, "label": i.get("label") or name, "kind": kind, "values": _values(_text(i, f"{q('inputValues')}/{q('text')}"), kind)})
    for o in table.findall(q("output")):
        kind = _KINDS.get(o.get("typeRef") or "string", TEXT)
        name = _ident(o.get("name") or o.get("id"), "output")
        default = _text(o, f"{q('defaultOutputEntry')}/{q('text')}")
        outputs.append({"name": name, "label": o.get("label") or name, "kind": kind,
                        "values": _values(_text(o, f"{q('outputValues')}/{q('text')}"), kind), "default": default})
    rules = []
    for r in table.findall(q("rule")):
        ins = [_text(e, q("text")) for e in r.findall(q("inputEntry"))]
        outs = [_text(e, q("text")) for e in r.findall(q("outputEntry"))]
        if len(ins) != len(inputs) or len(outs) != len(outputs):
            raise DecisionError(f"Rule {r.get('id')} does not have one entry per column")
        rules.append({"id": r.get("id"), "note": _text(r, q("description")) or "",
                      "when": {c["name"]: v for c, v in zip(inputs, ins) if v not in (None, "", "-")},
                      "then": {c["name"]: v for c, v in zip(outputs, outs)}})
    doc = {"id": _ident(decision.get("id"), "decision"), "title": decision.get("name") or decision.get("id"), "version": settings.get("version", "1.0"),
           "description": _text(decision, q("description")) or "", "context": settings.get("context", "case"),
           "binding": settings.get("binding", "none"), "enabled": bool(settings.get("enabled", False)),
           "case_types": settings.get("case_types") or [], "hit_policy": table.get("hitPolicy", "UNIQUE"),
           "aggregation": table.get("aggregation"), "inputs": inputs, "outputs": outputs, "rules": rules}
    t = from_dict(doc)
    return from_dict(doc, to_yaml(t))
