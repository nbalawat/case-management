"""The offline planner's reading of questions about the rules rather than the records: typologies, policy and
procedure. It hands the question to `policy_search` and quotes the best passages with their ids, so the answer can
be checked and opened like any other."""
from __future__ import annotations

import re

ASKS_RULES = re.compile(r"""
    \b(signs?|indicators?|red\ flags?|typology|typologies|mitigating)\b
  | \bwhat\ (is|are|does)\ (a\ |an\ |the\ )?(policy|procedure|guidance|rule|requirement)
  | \b(policy|procedure|guidance|playbook)\b.*\b(say|says|require|requires|for|on)\b
  | \bwhat\ (should|must|do)\ (i|we)\ do\b
  | \bhow\ (should|do|must)\ (i|we)\ (handle|treat|review|decide|escalate|clear|work)\b
  | \b(when|how\ soon|how\ quickly|deadline)\b.*\b(file|filed|filing|sar|str)\b.*\b(must|need|due|deadline|required|should)\b
  | \b(must|need\ to|required\ to)\b.*\b(file|tell|notify)\b
  | \btipping\ off\b
""", re.X | re.I)
ABOUT_RECORDS = re.compile(r"\b(how many|count|number of|total|sum|average|list|show me|which (customer|case|analyst)s?)\b", re.I)


def wants_rules(question: str) -> bool:
    """A question about what the rules are, not about what is in the records."""
    return bool(ASKS_RULES.search(question)) and not ABOUT_RECORDS.search(question)


def write(data: dict) -> str:
    hits = data.get("passages") or []
    if not hits:
        return ("The knowledge library has nothing on that. It holds the platform's typologies and procedures and this "
                "client's own policies; ask your administrator to add the document you need.")
    top = hits[0]
    lines = [f"From {top['title']}, {top['section']} ({top['id']}):", "", top["text"]]
    more = [h for h in hits[1:3] if h["score"] >= top["score"] * 0.5]
    if more:
        lines += ["", "See also: " + "; ".join(f"{h['title']}, {h['section']} ({h['id']})" for h in more) + "."]
    return "\n".join(lines)
