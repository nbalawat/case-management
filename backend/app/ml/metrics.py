"""How a model did on alerts it never saw, in the terms an alert operation cares about."""
from __future__ import annotations

REVIEW_SHARES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
BINS = 10


def workload(y: list[int], p: list[float]) -> dict:
    """Work through alerts highest score first: what share of the ones that mattered is reached after reviewing each
    share of alerts, and the share that could wait while still reaching 95% and 99% of them."""
    order = sorted(range(len(p)), key=lambda i: -p[i])
    positives = sum(y) or 1
    found, curve, needed = 0, [], {}
    for rank, i in enumerate(order, start=1):
        found += y[i]
        share = rank / len(order)
        for target in (0.95, 0.99):
            if target not in needed and found / positives >= target:
                needed[target] = share
        curve.append((share, found / positives))
    points = [{"reviewed": s, "found": round(next(f for sh, f in curve if sh >= s - 1e-9), 4)} for s in REVIEW_SHARES]
    return {"curve": points, "could_wait_at_95": round(1 - needed.get(0.95, 1.0), 4), "could_wait_at_99": round(1 - needed.get(0.99, 1.0), 4)}


def reference(column: list[float]) -> dict:
    """The training distribution of one feature, as decile edges and shares, for drift checks later."""
    values = sorted(column)
    if not values:
        return {"edges": [], "shares": []}
    edges = sorted({values[min(len(values) - 1, int(len(values) * q / BINS))] for q in range(1, BINS)})
    return {"edges": edges, "shares": shares(column, edges)}


def shares(column: list[float], edges: list[float]) -> list[float]:
    from bisect import bisect_right

    counts = [0] * (len(edges) + 1)
    for v in column:
        counts[bisect_right(edges, v)] += 1
    n = len(column) or 1
    return [round(c / n, 6) for c in counts]


def psi(expected: list[float], actual: list[float]) -> float:
    """Population stability index: under 0.1 stable, 0.1 to 0.25 worth a look, over 0.25 the population has moved."""
    import math

    total = 0.0
    for e, a in zip(expected, actual):
        e, a = max(e, 1e-4), max(a, 1e-4)
        total += (a - e) * math.log(a / e)
    return round(total, 4)
