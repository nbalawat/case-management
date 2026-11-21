"""What an agent can touch. Every call goes through a toolbox that knows the agent's declaration and its level.

Three rules are enforced here rather than left to each agent:
  * an agent may only use tools its declaration lists;
  * the actions reserved for people are not tools at all, at any level;
  * an agent's one declared action is available only at L3.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import MediaItem
from .catalog import ACTS, HUMAN_ONLY, AgentSpec


class ToolDenied(PermissionError):
    pass


class HumanOnly(ToolDenied):
    pass


@dataclass
class Toolbox:
    spec: AgentSpec
    level: str
    calls: list[dict] = field(default_factory=list)

    def use(self, tool: str, detail: str = "") -> None:
        """Record a tool call, refusing one the agent was not given."""
        if tool in HUMAN_ONLY:
            raise HumanOnly(f"{HUMAN_ONLY[tool]} is a human action. No agent can do it.")
        if tool not in self.spec.tools:
            raise ToolDenied(f"{self.spec.title} is not given the '{tool}' tool")
        self.calls.append({"tool": tool, "detail": detail})

    def act(self, action: str, detail: str = "") -> None:
        """Take the agent's own action. Raises unless this agent, at this level, may."""
        if action in HUMAN_ONLY:
            raise HumanOnly(f"{HUMAN_ONLY[action]} is a human action. No agent can do it.")
        if action not in ACTS or self.spec.acts != action:
            raise ToolDenied(f"{self.spec.title} may not '{action}'")
        if self.level != "L3":
            raise ToolDenied(f"{self.spec.title} is at {self.level}; it may only recommend")
        self.calls.append({"tool": action, "detail": detail, "action": True})


# --- outside content -----------------------------------------------------------------------------------------------
# Articles and registry entries are written by strangers and may be written to manipulate whoever reads them.
# The agent never sees their text. A separate extractor, which has no tools and no access to the case, turns each
# document into a few typed fields; only those fields reach the agent.

CATEGORIES = {
    "financial_crime": ("money laundering", "laundering", "fraud", "embezzl", "bribery", "bribe", "corruption", "sanction", "indicted",
                        "charged with", "convicted", "ponzi", "tax evasion", "smuggling", "trafficking"),
    "regulatory": ("fined", "penalty", "enforcement action", "licence revoked", "license revoked", "regulator", "consent order"),
    "litigation": ("lawsuit", "sued", "court filing", "insolvency", "bankruptcy"),
}
INSTRUCTION = re.compile(
    r"\b(ignore (all|any|the|your|previous|prior)|disregard (all|any|the|your|previous|prior)|you are (now|an? )|system prompt|"
    r"as an ai|instructions?:|do not (escalate|report|flag)|must (close|approve|clear|mark)|close this case|mark (this|the) (case|alert)|"
    r"recommend clos|assistant[,:]|new instructions?)", re.I)


@dataclass(frozen=True)
class Extract:
    """All that survives of a document. `quote` is one sentence, only ever shown as a quotation from the source."""
    doc_id: str
    source: str
    published: str
    title: str
    category: str | None
    names_subject: bool
    quote: str
    instructions_removed: int


def extract(doc: MediaItem, subject: str) -> Extract:
    """Tool-less by construction: a pure function of one document and the subject's name."""
    text = f"{doc.title}. {doc.body}"
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    clean = [x for x in sentences if not INSTRUCTION.search(x)]
    low = " ".join(clean).lower()
    category = next((c for c, words in CATEGORIES.items() if any(w in low for w in words)), None)
    parts = [p for p in re.split(r"\s+", subject.lower()) if len(p) > 2]
    names = bool(parts) and all(p in low for p in parts)
    hit = next((x for x in clean[1:] if category and any(w in x.lower() for w in CATEGORIES[category])), clean[1] if len(clean) > 1 else "")
    title = doc.title if not INSTRUCTION.search(doc.title) else "(title withheld)"
    return Extract(doc.id, doc.source, doc.published.date().isoformat(), title[:160], category, names, hit[:240], len(sentences) - len(clean))


def media_search(s: Session, party_id: str) -> list[MediaItem]:
    return list(s.scalars(select(MediaItem).where(MediaItem.party_id == party_id).order_by(MediaItem.published.desc())))
