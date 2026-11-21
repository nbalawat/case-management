"""BM25 over the passages one client may read."""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from .corpus import Passage, documents

K1, B = 1.5, 0.75
STOP = frozenset("a an and are as at be by for from has have how i in is it its of on or that the this to was what when "
                 "where which who why will with do does should can say says about our we us me my you your please explain describe".split())
WORD = re.compile(r"[a-z0-9]+")
# How people ask, mapped to the words the library uses. Applied to queries only, after stemming.
SYNONYMS = {"sign": ("indicator",), "flag": ("indicator",), "warn": ("indicator",), "symptom": ("indicator",),
            "due": ("deadline",), "soon": ("deadline",), "quickly": ("rapid",), "fast": ("rapid",), "mov": ("movement",), "late": ("deadline",), "must": ("deadline",),
            "tip": ("tell",), "tipp": ("tell",), "excuse": ("mitigat",), "innocent": ("mitigat",), "legitimate": ("mitigat",),
            "pep": ("politically", "expos"), "peps": ("politically", "expos"), "smurf": ("structur",)}


def words(text: str) -> list[str]:
    out = []
    for w in WORD.findall(text.lower()):
        if w in STOP:
            continue
        # a light stem, enough for plurals and -ing/-ed forms ("deposits", "structuring", "filed")
        for suffix in ("ing", "ed", "es", "s"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.append(w)
    return out


def query_words(text: str) -> list[str]:
    out = words(text)
    return out + [s for w in out for s in SYNONYMS.get(w, ()) if s not in out]


class Index:
    def __init__(self, passages: tuple[Passage, ...]):
        self.passages = passages
        self.docs = [Counter(words(f"{p.title} {p.section} {p.section} {' '.join(p.scenarios)} {p.text}")) for p in passages]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        df = Counter(w for d in self.docs for w in d)
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def score(self, i: int, terms: list[str]) -> float:
        d, length = self.docs[i], self.lengths[i]
        s = 0.0
        for t in terms:
            f = d.get(t, 0)
            if f:
                s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * length / self.avg))
        return s


@lru_cache(maxsize=32)
def _index(passages: tuple[Passage, ...]) -> Index:
    return Index(passages)


def search(client: str | None, query: str, *, kind: str | None = None, scenario: str | None = None, limit: int = 5) -> list[dict]:
    """The best passages for a query, each with its citation and a score. Only the platform library and this client's
    own documents are searched."""
    idx = _index(documents(client))
    terms = query_words(query)
    hits = []
    for i, p in enumerate(idx.passages):
        if kind and p.kind != kind:
            continue
        if scenario and scenario not in p.scenarios:
            continue
        s = idx.score(i, terms) if terms else 0.0
        if s > 0 or (not terms and (kind or scenario)):
            hits.append((s, i))
    hits.sort(key=lambda h: (-h[0], idx.passages[h[1]].id))
    return [idx.passages[i].cite() | {"text": idx.passages[i].text, "score": round(s, 3)} for s, i in hits[:max(1, min(limit, 10))]]
