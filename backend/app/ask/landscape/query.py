"""The vocabulary types and the compiler: a request in fields and measures becomes one SQL statement.

A request names an object, up to two fields to split by, the measures to compute and filters that must all
hold. The compiler checks every name against the object's vocabulary (so a model cannot reach a column the
vocabulary does not offer), joins only what the request needs, adds the asker's scope and the client boundary,
and runs it. Each result row carries up to five example record ids, picked in the same statement with a window
function, so every figure can be traced to records the asker may open.

Dates: fields of kind "date" take ISO dates and the operators < <= > >= =. Text matching ignores case.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, String, and_, case, cast, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from ...domain.access import Persona
from ..semantic import QueryError
from .scope import Scope

OPS_BY_KIND = {"text": {"=", "!=", "in", "contains"}, "bool": {"=", "!="}, "number": {"=", "!=", ">", ">=", "<", "<=", "in"},
               "date": {">", ">=", "<", "<=", "="}}
OPS = sorted(set().union(*OPS_BY_KIND.values()))
MAX_ROWS, MAX_LIST, EXAMPLES = 50, 25, 5


@dataclass(frozen=True)
class Dim:
    """A field of an object. `expr(q)` gives its SQL expression; it may join related rows through q."""

    id: str
    label: str
    kind: str  # text | number | bool | date
    expr: Callable[["Q"], Any]
    help: str = ""
    group: bool = True
    show: bool = True  # offered in record lists


@dataclass(frozen=True)
class Measure:
    id: str
    label: str
    expr: Callable[["Q"], Any]  # an aggregate over the matching rows
    unit: str = "count"  # count | usd | percent | days | score


@dataclass(frozen=True)
class Obj:
    """One kind of record Ask can read, with its vocabulary and who may read it."""

    id: str
    label: str  # plural, for people: "transactions"
    description: str
    entity: type
    kind: str  # the record kind, for citations and links
    id_dim: str
    dims: dict[str, Dim]
    measures: dict[str, Measure]
    scope: Callable[[Scope, "Q"], Any]
    list_columns: tuple[str, ...]
    default_sort: str
    joins: dict[str, tuple[type, Callable[[Any, Any], Any]]] = field(default_factory=dict)
    permission: Callable[[Persona], str | None] = lambda p: None  # a reason this person may not read it, or None
    customer_dim: str | None = "customer_id"  # the field that names the customer, for links
    case_dim: str | None = None

    def vocabulary(self) -> dict:
        return {"object": self.id, "label": self.label, "about": self.description,
                "fields": {d.id: f"{d.label} ({d.kind}{'; ' + d.help if d.help else ''}{'' if d.group else '; filter and sort only'})" for d in self.dims.values()},
                "measures": {m.id: m.label for m in self.measures.values()}}


class Q:
    """One statement being built: the base table, the related rows joined so far, and the dialect."""

    def __init__(self, obj: Obj, s: Session, scope: Scope):
        self.obj, self.s, self.scope = obj, s, scope
        self.base = obj.entity
        self.dialect = s.get_bind().dialect.name
        self._joined: dict[str, Any] = {}
        self._built = False

    def j(self, name: str):
        """The related table `name`, outer-joined once, on the same client."""
        if name not in self._joined:
            if self._built:  # a join named after the FROM clause was settled would silently become a cross product
                raise RuntimeError(f"'{name}' was joined after the statement was built: pass every condition to statement()")
            entity, _ = self.obj.joins[name]
            self._joined[name] = aliased(entity, name=f"j_{name}")
        return self._joined[name]

    def month(self, col):
        return func.to_char(col, "YYYY-MM") if self.dialect == "postgresql" else func.strftime("%Y-%m", col)

    def day(self, col):
        return cast(col, Date) if self.dialect == "postgresql" else func.date(col)

    def statement(self, *columns, where=()):
        """SELECT columns FROM the base and its joins WHERE every condition. Give the conditions here, not with a
        later .where(): building them may name a join, and the joins are settled now."""
        where = list(where)
        # Building a join's condition may join another table (a customer reached through a case), so settle them all first,
        # then join in the order the object declares, which puts each table after the ones its condition needs.
        ons: dict[str, Any] = {}
        while set(self._joined) - set(ons):
            for name in [n for n in self._joined if n not in ons]:
                _, on = self.obj.joins[name]
                alias = self._joined[name]
                ons[name] = on(self.base, alias, self) if on.__code__.co_argcount == 3 else on(self.base, alias)
        stmt = select(*columns).select_from(self.base)
        for name in [n for n in self.obj.joins if n in self._joined]:
            alias = self._joined[name]
            stmt = stmt.outerjoin(alias, and_(ons[name], alias.tenant_id == self.base.tenant_id))
        self._built = True
        return stmt.where(*where) if where else stmt


# --- helpers the catalog uses ----------------------------------------------------------
def share(numerator, denominator=None):
    """A percentage of the matching rows (or of a denominator), rounded later."""
    den = denominator if denominator is not None else func.count()
    return 100.0 * func.sum(case((numerator, 1), else_=0)) / func.nullif(den, 0)


def count_if(cond):
    return func.sum(case((cond, 1), else_=0))


def days_between(q: Q, later, earlier):
    if q.dialect == "postgresql":
        return func.extract("epoch", later - earlier) / 86400.0
    return func.julianday(later) - func.julianday(earlier)


def now_sql():
    from ...domain.models import now

    return literal(now())


# --- checking a request ------------------------------------------------------------
def _coerce(d: Dim, value: Any) -> Any:
    try:
        if d.kind == "number":
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if d.kind == "bool":
            if isinstance(value, bool):
                return value
            if str(value).strip().lower() in ("true", "yes", "1"):
                return True
            if str(value).strip().lower() in ("false", "no", "0"):
                return False
            raise ValueError
        if d.kind == "date":
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(str(value).strip()[:19])
    except (TypeError, ValueError):
        raise QueryError(f"'{value}' is not a valid {d.kind} for {d.id}") from None
    return str(value).strip().lower().replace("_", " ")


def _text(expr):
    return func.replace(func.lower(cast(expr, String)), "_", " ")


def where(obj: Obj, q: Q, filters: list[dict] | None):
    clauses = []
    for f in filters or []:
        if not isinstance(f, dict) or not {"field", "op", "value"} <= set(f):
            raise QueryError("Each filter needs field, op and value")
        d = obj.dims.get(f["field"])
        if d is None:
            raise QueryError(f"{obj.label.capitalize()} have no field '{f['field']}'. Fields: {', '.join(obj.dims)}")
        op, value = f["op"], f["value"]
        if op not in OPS_BY_KIND[d.kind]:
            raise QueryError(f"Operator '{op}' does not apply to {d.id}. Use one of: {', '.join(sorted(OPS_BY_KIND[d.kind]))}")
        col = d.expr(q)
        if op == "in":
            if not isinstance(value, list) or not value:
                raise QueryError(f"'in' on {d.id} needs a non-empty list")
            if len(value) > 500:
                raise QueryError("'in' takes at most 500 values")
            vals = [_coerce(d, v) for v in value]
            clauses.append((_text(col) if d.kind == "text" else col).in_(vals))
            continue
        v = _coerce(d, value)
        if d.kind == "text":
            t = _text(col)
            clauses.append(t.contains(v, autoescape=True) if op == "contains" else (t == v) if op == "=" else or_(t != v, col.is_(None)))
        elif d.kind == "bool":
            truth = func.coalesce(col, False)
            clauses.append(truth.is_(v) if op == "=" else truth.is_not(v))
        elif d.kind == "date" and op == "=":
            clauses.append(q.day(col) == (q.day(literal(v))))
        else:
            clauses.append({"=": col == v, "!=": or_(col != v, col.is_(None)), ">": col > v, ">=": col >= v, "<": col < v, "<=": col <= v}[op])
    return clauses


def _measures(obj: Obj, names: list[str] | None) -> list[Measure]:
    names = list(names or [next(iter(obj.measures))])
    for m in names:
        if m not in obj.measures:
            raise QueryError(f"{obj.label.capitalize()} have no measure '{m}'. Measures: {', '.join(obj.measures)}")
    return [obj.measures[m] for m in names]


def _round(m: Measure, v):
    if v is None:
        return None
    v = float(v)
    if m.unit == "count":
        return int(round(v))
    return round(v, 2 if m.unit == "usd" else 1)


def _show(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date().isoformat() if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat(timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, str) and "_" in v and not any(ch.isdigit() for ch in v):
        return v.replace("_", " ")
    return v


# --- running a request -------------------------------------------------------------
def breakdown(s: Session, asker: Persona, obj: Obj, *, group_by: list[str] | None = None, measures: list[str] | None = None,
              filters: list[dict] | None = None, sort_by: str | None = None, descending: bool = True, limit: int = MAX_ROWS) -> dict:
    """Measures over the matching records, optionally split by up to two fields, each row with example ids."""
    group_by = list(group_by or [])
    if len(group_by) > 2:
        raise QueryError("Group by at most two fields")
    for g in group_by:
        d = obj.dims.get(g)
        if d is None or not d.group:
            raise QueryError(f"Cannot group {obj.label} by '{g}'. Groupable: {', '.join(k for k, x in obj.dims.items() if x.group)}")
    ms = _measures(obj, measures)
    if sort_by is not None and sort_by not in [m.id for m in ms] and sort_by not in group_by:
        raise QueryError("sort_by must be one of the measures or group_by fields in this request")
    scope = Scope(s, asker)
    q = Q(obj, s, scope)
    conds = [obj.scope(scope, q), *where(obj, q, filters)]
    keys = [obj.dims[g].expr(q) for g in group_by]
    agg = [m.expr(q) for m in ms]
    stmt = q.statement(*[k.label(f"k{i}") for i, k in enumerate(keys)], *[a.label(f"m{i}") for i, a in enumerate(agg)],
                       func.count().label("n"), where=conds)
    if keys:
        stmt = stmt.group_by(*keys)
    rows = s.execute(stmt).all()
    total_q = Q(obj, s, scope)
    total = s.scalar(total_q.statement(func.count(), where=[obj.scope(scope, total_q), *where(obj, total_q, filters)])) or 0

    # Example records per group, chosen in one statement: the newest few in each group.
    ex_q = Q(obj, s, scope)
    ex_keys = [obj.dims[g].expr(ex_q) for g in group_by]
    order = obj.dims[obj.default_sort].expr(ex_q)
    rn = func.row_number().over(partition_by=ex_keys or None, order_by=order.desc()).label("rn")
    inner = ex_q.statement(obj.dims[obj.id_dim].expr(ex_q).label("rid"), *[k.label(f"k{i}") for i, k in enumerate(ex_keys)], rn,
                           where=[obj.scope(scope, ex_q), *where(obj, ex_q, filters)]).subquery()
    examples: dict[tuple, list[str]] = {}
    for r in s.execute(select(inner).where(inner.c.rn <= EXAMPLES)).all():
        examples.setdefault(tuple(r[1:1 + len(group_by)]), []).append(str(r[0]))

    table = []
    for r in rows:
        key = tuple(r[:len(group_by)])
        if not group_by and r[-1] == 0:
            values = [_round(m, 0 if m.unit == "count" else None) for m in ms]
        else:
            values = [_round(m, r[len(group_by) + i]) for i, m in enumerate(ms)]
        table.append({"key": [_show(k) if k is not None else "none" for k in key], "values": values, "records": r[-1],
                      "ids": examples.get(key, [])})
    by = sort_by or ms[0].id
    if by in [m.id for m in ms]:
        i = [m.id for m in ms].index(by)
        table.sort(key=lambda t: (t["values"][i] is None, -(t["values"][i] or 0) if descending else (t["values"][i] or 0), [str(k) for k in t["key"]]))
    else:
        i = group_by.index(by)
        table.sort(key=lambda t: str(t["key"][i]), reverse=descending)
    limit = max(1, min(int(limit), MAX_ROWS))
    return {"object": obj.id, "record_kind": obj.kind, "columns": [obj.dims[g].label for g in group_by] + [m.label for m in ms],
            "group_by": group_by, "measures": [m.id for m in ms], "units": [m.unit for m in ms], "rows": table[:limit],
            "groups": len(table), "truncated": len(table) > limit, "matching": total}


def list_records(s: Session, asker: Persona, obj: Obj, *, filters: list[dict] | None = None, sort_by: str | None = None,
                 descending: bool = True, limit: int = 10, columns: list[str] | None = None) -> dict:
    sort_by = sort_by or obj.default_sort
    if sort_by not in obj.dims:
        raise QueryError(f"Cannot sort {obj.label} by '{sort_by}'. Fields: {', '.join(obj.dims)}")
    cols = list(columns or obj.list_columns)
    unknown = [c for c in cols if c not in obj.dims]
    if unknown:
        raise QueryError(f"{obj.label.capitalize()} have no field(s) {', '.join(unknown)}")
    if obj.id_dim not in cols:
        cols.insert(0, obj.id_dim)
    if sort_by not in cols:
        cols.append(sort_by)
    for link in (obj.customer_dim, obj.case_dim):
        if link and link in obj.dims and link not in cols:
            cols.append(link)
    scope = Scope(s, asker)
    q = Q(obj, s, scope)
    conds = [obj.scope(scope, q), *where(obj, q, filters)]
    exprs = [obj.dims[c].expr(q) for c in cols]
    key = obj.dims[sort_by].expr(q)
    nulls_last = case((key.is_(None), 1), else_=0)
    limit = max(1, min(int(limit), MAX_LIST))
    stmt = q.statement(*[e.label(c) for c, e in zip(cols, exprs)], where=conds) \
        .order_by(nulls_last, key.desc() if descending else key.asc(), obj.dims[obj.id_dim].expr(q)).limit(limit)
    rows = [{c: _show(v) for c, v in zip(cols, r)} for r in s.execute(stmt).all()]
    count_q = Q(obj, s, scope)
    total = s.scalar(count_q.statement(func.count(), where=[obj.scope(scope, count_q), *where(obj, count_q, filters)])) or 0
    return {"object": obj.id, "record_kind": obj.kind, "columns": cols, "records": rows, "matching": total, "truncated": total > len(rows)}


def get_one(s: Session, asker: Persona, obj: Obj, record_id: str) -> dict | None:
    """One record by id with every field, if the asker may see it."""
    scope = Scope(s, asker)
    q = Q(obj, s, scope)
    if obj.dims[obj.id_dim].kind == "number":
        try:
            record_id = int(record_id)
        except (TypeError, ValueError):
            return None
    cols = list(obj.dims)
    exprs = [obj.dims[c].expr(q) for c in cols]
    stmt = q.statement(*[e.label(c) for c, e in zip(cols, exprs)], where=[obj.scope(scope, q), obj.dims[obj.id_dim].expr(q) == record_id]).limit(1)
    r = s.execute(stmt).first()
    return None if r is None else {c: _show(v) for c, v in zip(cols, r)}
