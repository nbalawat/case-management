"""Comparing names. Pure functions: no database, no client, no configuration."""
from __future__ import annotations

import re
import unicodedata

# Legal forms carry no identity: "Volkov Maritime Trading Co" and "Volkov Maritime Trading" are the same firm.
LEGAL_FORMS = {"llc", "ltd", "limited", "inc", "incorporated", "co", "corp", "corporation", "company", "plc", "gmbh", "ag", "sa", "sarl", "srl",
               "bv", "nv", "oy", "ab", "as", "pte", "pty", "fze", "fzc", "llp", "lp", "kg", "spa", "sas", "jsc", "ooo"}
NOISE = {"the", "and", "of"}
_split = re.compile(r"[^a-z0-9]+")
_dotted = re.compile(r"(?<![a-z0-9])(?:[a-z]\.){2,}")  # "L.L.C." and "S.A." are one word each


def fold(text: str) -> str:
    """Lower case, accents removed: 'Tomás Bergström' and 'tomas bergstrom' compare equal."""
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower()


def tokens(name: str) -> list[str]:
    folded = _dotted.sub(lambda m: m.group(0).replace(".", "") + " ", fold(name))
    raw = [t for t in _split.split(folded.replace("&", " and ")) if t]
    kept = [t for t in raw if t not in LEGAL_FORMS and t not in NOISE]
    return kept or raw  # a name that is nothing but a legal form keeps it


def key(name: str) -> str:
    """The form two spellings of one name share. Word order is kept: 'Chen Wei' and 'Wei Chen' may be different people."""
    return " ".join(tokens(name))


def _singular(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(len(a), len(b)) // 2 - 1
    hit_a, hit_b = [False] * len(a), [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not hit_b[j] and b[j] == ch:
                hit_a[i] = hit_b[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    k = swaps = 0
    for i, ch in enumerate(a):
        if hit_a[i]:
            while not hit_b[k]:
                k += 1
            swaps += ch != b[k]
            k += 1
    jaro = (matches / len(a) + matches / len(b) + (matches - swaps / 2) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def similarity(a: str, b: str) -> float:
    """0 to 1. Spelling closeness of the whole name, tempered by how many whole words the two share."""
    ta, tb = [_singular(t) for t in tokens(a)], [_singular(t) for t in tokens(b)]
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    if set(ta) == set(tb):  # same words, different order
        return 0.9
    spelled = jaro_winkler(" ".join(ta), " ".join(tb))
    long, short = (ta, tb) if len(ta) >= len(tb) else (tb, ta)
    # Each word of the longer name is matched to its closest word in the other; a near-miss spelling counts, a different word does not.
    best = [max(jaro_winkler(w, x) for x in short) for w in long]
    shared = sum(b if b >= 0.88 else 0.0 for b in best) / len(long)
    return round(0.6 * spelled + 0.4 * shared, 4)


def blocks(name: str) -> set[str]:
    """Cheap keys so only plausible pairs are ever compared: names sharing a word, or a four-letter prefix."""
    ts = [_singular(t) for t in tokens(name)]
    return {f"w:{t}" for t in ts if len(t) > 2} | ({f"p:{''.join(ts)[:4]}"} if ts else set())
