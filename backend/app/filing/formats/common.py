"""What every format shares: the checks any regulator makes, and small XML helpers."""
from __future__ import annotations

from xml.dom import minidom
from xml.etree import ElementTree as ET

from ..report import INSTITUTION_FIELDS


def common_problems(report: dict) -> list[str]:
    """What any regulator needs, whatever the format."""
    out = []
    missing = [f.replace("_", " ") for f in INSTITUTION_FIELDS if not str(report["institution"].get(f) or "").strip()]
    if missing:
        out.append(f"The filing institution's details are incomplete: {', '.join(missing)}. An operator sets these for the client.")
    if not report["subject"]["name"].strip():
        out.append("The subject has no name")
    if not report["transactions"]:
        out.append("No transactions are attached to the report")
    return out


def add(parent: ET.Element, tag: str, text: object | None = None, **attrs: object) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def pretty(root: ET.Element) -> str:
    return minidom.parseString(ET.tostring(root, encoding="utf-8")).toprettyxml(indent="  ", encoding="UTF-8").decode()


def compact_date(iso: str | None) -> str:
    return (iso or "")[:10].replace("-", "")


def split_name(name: str) -> tuple[str, str]:
    """First and last name from one string. Good enough for a draft; a person checks the report before it is sent."""
    parts = name.split()
    return (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", name)
