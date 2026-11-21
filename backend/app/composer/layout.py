"""Place blocks on the canvas for designs that arrive without positions (templates, imported definitions, BPMN without
diagram data).

Top to bottom by distance from the start, so a long workflow stays readable on a laptop screen: one row per step,
side by side where a stage has several ways forward. Waiting stages sit beside the stage that parks into them, and
outcomes share the bottom row. It only needs to be tidy enough to start from: people move blocks anyway.
"""
from __future__ import annotations

from .model import Design, Position

COL, ROW, X0, Y0 = 280, 170, 40, 40


def auto_layout(design: Design) -> Design:
    from .compiler import kind_of

    start = next((n for n in design.nodes if n.type == "start"), None)
    if start is None:
        return design
    depth = {start.id: 0}
    order = [start.id]
    queue = [start.id]
    while queue:
        cur = queue.pop(0)
        for e in design.out_of(cur):
            nxt = design.node(e.target)
            if nxt is None or e.target in depth or kind_of(nxt) == "outcome" or nxt.type == "wait":
                continue
            depth[e.target] = depth[cur] + 1
            order.append(e.target)
            queue.append(e.target)
    rows: dict[int, list[str]] = {}
    for nid in order:
        rows.setdefault(depth[nid], []).append(nid)
    width = max([len(r) for r in rows.values()] + [1])
    ends = [n for n in design.nodes if kind_of(n) == "outcome"]
    width = max(width, len(ends))
    for d, ids in rows.items():
        for i, nid in enumerate(ids):
            design.node(nid).position = Position(x=X0 + (i + (width - len(ids)) / 2) * COL, y=Y0 + d * ROW)
    right = X0 + width * COL
    waits = [n for n in design.nodes if n.type == "wait" and n.id not in depth]
    for i, w in enumerate(waits):
        parent = next((e.source for e in design.into(w.id) if e.source in depth), None)
        y = Y0 + (depth[parent] if parent else i) * ROW
        w.position = Position(x=right + 30, y=y)
    last = max(depth.values(), default=0) + 1
    for i, n in enumerate(ends):
        n.position = Position(x=X0 + (i + (width - len(ends)) / 2) * COL, y=Y0 + last * ROW)
    placed = set(depth) | {w.id for w in waits} | {n.id for n in ends}
    for i, n in enumerate(x for x in design.nodes if x.id not in placed):
        n.position = Position(x=X0 + i * COL, y=Y0 + (last + 1) * ROW)
    return design
