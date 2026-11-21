"""One renderer per regulator's format. Adding a regime is adding a module here and one line to FORMATS."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import fincen, goaml, ofac


@dataclass(frozen=True)
class Format:
    id: str
    title: str
    regime: str
    kinds: tuple[str, ...]
    extension: str
    render: Callable[[dict], str]
    check: Callable[[dict], list[str]]  # what this regulator would refuse, in words a person can act on


FORMATS: dict[str, Format] = {f.id: f for f in (
    Format("fincen_sar_xml", "FinCEN SAR (BSA XML)", "fincen", ("SAR",), "xml", fincen.render_sar, fincen.check_sar),
    Format("fincen_ctr_xml", "FinCEN CTR (BSA XML)", "fincen", ("CTR",), "xml", fincen.render_ctr, fincen.check_ctr),
    Format("goaml_str_xml", "goAML STR (XML)", "goaml", ("STR",), "xml", goaml.render_str, goaml.check_str),
    Format("goaml_ctr_xml", "goAML CTR (XML)", "goaml", ("CTR",), "xml", goaml.render_ctr, goaml.check_ctr),
    Format("ofac_report", "OFAC blocked or rejected property (structured record)", "ofac", ("BLOCK", "REJECT"), "xml", ofac.render, ofac.check),
    Format("sanctions_report", "Report to the sanctions authority (structured record)", "sanctions", ("BLOCK", "REJECT"), "xml", ofac.render, ofac.check),
)}
# Who a client reports blocked or rejected property to follows from where it reports suspicion: a FinCEN filer answers to OFAC.
SANCTIONS_REGIME = {"fincen": "ofac"}
# What a suspicious-activity report is called under each regime.
SUSPICION_KIND = {"fincen": "SAR", "goaml": "STR"}


def format_for(regime: str, kind: str) -> Format:
    for f in FORMATS.values():
        if f.regime == regime and kind in f.kinds:
            return f
    raise KeyError(f"No format for a {kind} under '{regime}'")
