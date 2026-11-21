"""Model access goes through one small interface so providers can be swapped per tenant.

Milestone 1 ships only the offline provider. Hosted and self-hosted providers plug in here.
"""
from __future__ import annotations

from typing import Protocol

from ..config import settings


class LLMProvider(Protocol):
    name: str

    def write_narrative(self, facts: dict) -> str: ...


class TemplateProvider:
    """Deterministic writer used for offline demos and tests. Every sentence comes from computed facts."""

    name = "offline-template"

    def write_narrative(self, facts: dict) -> str:
        p = facts["party"]
        lines = [
            f"{p['name']} ({p['id']}), {p['occupation'] or 'occupation not recorded'}, customer since {p['onboarded']}, "
            f"risk rating {p['risk_rating']}, was reviewed following {facts['n_alerts']} alert(s): {', '.join(facts['scenarios'])}.",
            f"Between {facts['period'][0]} and {facts['period'][1]} the alerted activity comprised {facts['n_txns']} transactions "
            f"totalling ${facts['alerted_total']:,.0f}.",
        ]
        lines += [f["text"] for f in facts["key_findings"]]
        if facts["recommendation"] == "escalate":
            lines.append("The activity is not consistent with the customer's stated profile and no legitimate purpose was "
                         "identified from available records. Filing of a suspicious activity report is recommended.")
        else:
            lines.append("The activity is consistent with the customer's profile and history. No suspicious activity report "
                         "is recommended; the rationale above is retained on file.")
        return "\n\n".join(lines)


def get_provider() -> LLMProvider:
    # Only one provider exists in this milestone; the setting is the seam for the next ones.
    _ = settings.llm_provider
    return TemplateProvider()
