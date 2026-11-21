"""Pipelines: bring new files from a client's object storage into the canonical store, safely and repeatably.

One run of one source goes: discover new files -> read -> map -> validate against the source's quality rules ->
load -> reconcile -> detect. Each file is handled on its own: a file that fails its rules is quarantined whole
and nothing from it is loaded, while the other files carry on. A file is identified by its key and content
fingerprint, so running again loads nothing twice and a corrected file counts as new.
"""
from __future__ import annotations

import fnmatch
import json
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.models import DataSource, IngestedObject, PipelineRun, new_id, now
from ..storage import ObjectInfo, ObjectStore, StorageError, safe_key
from ..tenancy import tenant_of
from . import locations
from .datasets import DATASETS, in_dependency_order
from .formats import FORMATS, FormatError, detect, read_records
from .mapping import HeuristicProposer, proposals_as_dicts
from ..visibility import system_work

RULES = {  # name -> (default, description)
    "max_reject_rate": (0.05, "A file with a larger share of unusable records than this is quarantined whole"),
    "min_rows": (1, "A file with fewer records than this is quarantined"),
    "max_age_hours": (None, "The source is flagged stale when its newest file is older than this"),
    "unknown_customer": ("reject", "Records for a customer not yet loaded: 'reject' them or 'create' a placeholder customer"),
}
MAX_REJECTS_KEPT = 1000
SAMPLE_REJECTS = 20
NAME = re.compile(r"^[\w][\w .&()/-]{2,119}$")


class PipelineError(ValueError):
    """A source definition or run request is not acceptable. The message says why."""


# --- source definitions -----------------------------------------------------------
def clean_rules(rules: dict | None) -> dict:
    rules = dict(rules or {})
    unknown = sorted(set(rules) - set(RULES))
    if unknown:
        raise PipelineError(f"Unknown quality rule(s): {', '.join(unknown)}. Rules: {', '.join(RULES)}")
    out = {k: default for k, (default, _) in RULES.items()} | rules
    try:
        out["max_reject_rate"] = float(out["max_reject_rate"])
        out["min_rows"] = int(out["min_rows"])
        out["max_age_hours"] = None if out["max_age_hours"] in (None, "") else float(out["max_age_hours"])
    except (TypeError, ValueError):
        raise PipelineError("Quality rule values must be numbers") from None
    if not 0 <= out["max_reject_rate"] <= 1:
        raise PipelineError("max_reject_rate is a share between 0 and 1")
    if out["min_rows"] < 0 or (out["max_age_hours"] is not None and out["max_age_hours"] <= 0):
        raise PipelineError("min_rows cannot be negative and max_age_hours must be positive")
    if out["unknown_customer"] not in ("reject", "create"):
        raise PipelineError("unknown_customer must be 'reject' or 'create'")
    return out


def clean_source(tenant_id: str, data: dict) -> dict:
    """Validate everything about a source definition before it is saved."""
    name = str(data.get("name", "")).strip()
    if not NAME.match(name):
        raise PipelineError("Give the source a name of 3 to 120 characters")
    dataset = data.get("dataset")
    if dataset not in DATASETS:
        raise PipelineError(f"Dataset must be one of: {', '.join(DATASETS)}")
    try:
        locations.root_of(tenant_id, str(data.get("root", "")))
        prefix = safe_key(str(data.get("prefix", "")))
    except StorageError as e:
        raise PipelineError(str(e)) from None
    pattern = str(data.get("pattern") or "**").strip()
    if ".." in pattern or pattern.startswith("/") or len(pattern) > 200:
        raise PipelineError("The file pattern must be a relative pattern such as **/*.csv")
    fmt = data.get("format") or "auto"
    if fmt not in ("auto", *FORMATS):
        raise PipelineError(f"Format must be auto or one of: {', '.join(FORMATS)}")
    options = dict(data.get("options") or {})
    if set(options) - {"delimiter", "encoding"} or len(str(options.get("delimiter", ","))) != 1:
        raise PipelineError("Options may set a one-character delimiter and an encoding")
    fields = DATASETS[dataset].fields
    mapping = {k: (v or None) for k, v in dict(data.get("mapping") or {}).items()}
    unknown = sorted(set(mapping) - set(fields))
    if unknown:
        raise PipelineError(f"The mapping names fields this dataset does not have: {', '.join(unknown)}")
    missing = [k for k, meta in fields.items() if meta["required"] and not mapping.get(k)]
    if missing:
        raise PipelineError(f"Map the required field(s): {', '.join(missing)}")
    schedule = data.get("schedule_minutes")
    if schedule is not None:
        if not isinstance(schedule, int) or isinstance(schedule, bool) or not 5 <= schedule <= 10080:
            raise PipelineError("A schedule is a whole number of minutes between 5 and 10080 (a week)")
    return {"name": name, "dataset": dataset, "root": data["root"], "prefix": prefix, "pattern": pattern, "format": fmt, "options": options,
            "mapping": mapping, "rules": clean_rules(data.get("rules")), "schedule_minutes": schedule,
            "enabled": bool(data.get("enabled", True)), "columns": [str(c) for c in data.get("columns") or []]}


def save_source(s: Session, data: dict, by: str, source: DataSource | None = None) -> DataSource:
    tenant = tenant_of(s)
    cleaned = clean_source(tenant, data)
    clash = s.scalars(select(DataSource).where(DataSource.name == cleaned["name"])).first()
    if clash is not None and (source is None or clash.id != source.id):
        raise PipelineError(f"A source named '{cleaned['name']}' already exists")
    if source is None:
        source = DataSource(id=new_id("src"), created_by=by, **cleaned)
        s.add(source)
    else:
        if source.dataset != cleaned["dataset"]:
            raise PipelineError("A source's dataset cannot change. Create a new source instead")
        if cleaned["mapping"] != source.mapping:
            source.mapping_version += 1
        for k, v in cleaned.items():
            setattr(source, k, v)
    s.flush()
    return source


# --- looking at storage -----------------------------------------------------------
def _matches(source_prefix: str, pattern: str, key: str) -> bool:
    rel = key[len(source_prefix):].lstrip("/") if source_prefix else key
    if any(part.startswith(("_", ".")) for part in rel.split("/")):
        return False  # markers and working folders such as _SUCCESS or _tmp/ are never data
    return pattern == "**" or fnmatch.fnmatch(rel, pattern) or (pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:]))


def matching_objects(store: ObjectStore, prefix: str, pattern: str) -> list[ObjectInfo]:
    return [o for o in store.list(prefix) if _matches(prefix, pattern, o.key)]


def _format_of(fmt: str, key: str) -> tuple[str, bool]:
    detected, gz = detect(key)
    chosen = detected if fmt == "auto" else fmt
    if chosen is None:
        raise FormatError("the file name does not say what format it is; set the format on the source")
    return chosen, gz


def _records(store: ObjectStore, key: str, fmt: str, options: dict):
    chosen, gz = _format_of(fmt, key)
    with store.open(key) as f:
        yield from read_records(f, chosen, gz=gz, delimiter=options.get("delimiter", ","), encoding=options.get("encoding", "utf-8"))


def preview(tenant_id: str, data: dict, sample: int = 200) -> dict:
    """Look at a location before saving a source: which files match, what is in the newest one, and a proposed mapping."""
    dataset = data.get("dataset")
    if dataset not in DATASETS:
        raise PipelineError(f"Dataset must be one of: {', '.join(DATASETS)}")
    try:
        store = locations.store_for(tenant_id, str(data.get("root", "")))
        prefix = safe_key(str(data.get("prefix", "")))
        objects = matching_objects(store, prefix, str(data.get("pattern") or "**"))
    except StorageError as e:
        raise PipelineError(str(e)) from None
    out = {"location": store.url + prefix, "matching": len(objects), "bytes": sum(o.size for o in objects),
           "objects": [_object(o) for o in objects[-25:]], "columns": [], "proposals": [], "preview": [], "sampled": None, "problem": None}
    if not objects:
        return out
    newest = max(objects, key=lambda o: (o.modified or now(), o.key))
    out["sampled"] = newest.key
    rows = []
    try:
        for i, r in enumerate(_records(store, newest.key, data.get("format") or "auto", dict(data.get("options") or {}))):
            if i >= sample:
                break
            rows.append(r)
    except (FormatError, StorageError) as e:
        out["problem"] = f"{newest.key}: {e}"
        return out
    cols = list(rows[0]) if rows else []
    for c in cols:
        vals = [r.get(c, "") for r in rows]
        out["columns"].append({"column": c, "null_rate": round(sum(1 for v in vals if v == "") / max(len(vals), 1), 3),
                               "distinct": len(set(vals)), "examples": list(dict.fromkeys(v for v in vals if v))[:3]})
    fields = DATASETS[dataset].fields
    out["proposals"] = proposals_as_dicts(HeuristicProposer().propose(cols, rows, fields), fields)
    out["preview"] = rows[:5]
    return out


def _object(o: ObjectInfo) -> dict:
    return {"key": o.key, "size": o.size, "modified": o.modified.isoformat() if o.modified else None, "fingerprint": o.fingerprint}


def discover(s: Session, source: DataSource, store: ObjectStore) -> tuple[list[ObjectInfo], list[ObjectInfo]]:
    """(every matching file, the ones not yet handled in their current content)."""
    objects = matching_objects(store, source.prefix, source.pattern)
    done = {(o.key, o.fingerprint) for o in s.scalars(select(IngestedObject).where(IngestedObject.source_id == source.id))}
    return objects, [o for o in objects if (o.key, o.fingerprint) not in done]


def health(source: DataSource) -> dict:
    """How a source looks right now, from what its last run observed."""
    newest = source.state.get("newest_object_at")
    limit = (source.rules or {}).get("max_age_hours")
    stale = bool(newest and limit and now() - _dt(newest) > timedelta(hours=limit))
    due = bool(source.enabled and source.schedule_minutes and
               (source.last_run_at is None or source.last_run_at + timedelta(minutes=source.schedule_minutes) <= now()))
    return {"stale": stale, "due": due, "newest_object_at": newest, "last_status": source.state.get("last_status")}


def _dt(iso: str):
    from datetime import datetime

    return datetime.fromisoformat(iso)


# --- running ----------------------------------------------------------------------
def _step(run: PipelineRun, name: str, status: str, detail: str, **counts) -> None:
    run.steps = [*run.steps, {"step": name, "status": status, "detail": detail, "counts": counts, "at": now().isoformat()}]


def _process_object(s: Session, source: DataSource, run: PipelineRun, store: ObjectStore, obj: ObjectInfo, rules: dict) -> tuple[IngestedObject, set[str]]:
    """Read, map, validate and load one file. Returns its record and the customers whose activity changed."""
    dataset = DATASETS[source.dataset]
    record = IngestedObject(id=new_id("obj"), source_id=source.id, run_id=run.id, key=obj.key, fingerprint=obj.fingerprint, size=obj.size,
                            status="loaded", stats={})
    good: list[tuple[int, dict]] = []
    rejects: list[dict] = []
    read = 0
    source_amount = 0.0
    warnings: list[str] = []

    def quarantine(reason: str) -> tuple[IngestedObject, set[str]]:
        record.status, record.reason = "quarantined", reason
        record.stats = {"read": read, "loaded": 0, "updated": 0, "duplicates": 0, "rejected": len(rejects), "warnings": warnings,
                        "reject_samples": rejects[:SAMPLE_REJECTS], "rejects_at": _write_rejects(s, source, run, obj.key, rejects)}
        s.add(record)
        s.flush()
        return record, set()

    try:
        for n, row in enumerate(_records(store, obj.key, source.format, source.options or {}), start=1):
            read = n
            if n == 1:
                missing = sorted({c for target, c in source.mapping.items() if c and c not in row and dataset.fields[target]["required"]})
                if missing:
                    return quarantine(f"The file's columns have changed: required column(s) {', '.join(missing)} are missing")
                gone = sorted({c for target, c in source.mapping.items() if c and c not in row} - set(missing))
                if gone:
                    warnings.append(f"Optional mapped column(s) missing: {', '.join(gone)}")
                new = sorted(set(row) - set(source.columns)) if source.columns else []
                if new:
                    warnings.append(f"New column(s) not seen when the mapping was confirmed: {', '.join(new)}")
            try:
                values = dataset.transform(row, source.mapping)
            except (ValueError, KeyError) as e:
                if len(rejects) < MAX_REJECTS_KEPT:
                    rejects.append({"row": n, "reason": str(e), "record": row})
                else:
                    rejects.append({})
                continue
            if dataset.amount_field:
                source_amount += values[dataset.amount_field]
            good.append((n, values))
    except (FormatError, StorageError) as e:
        return quarantine(f"The file could not be read: {e}")

    if read < rules["min_rows"]:
        return quarantine(f"The file has {read} record(s); the source expects at least {rules['min_rows']}")
    if read and len(rejects) / read > rules["max_reject_rate"]:
        return quarantine(f"{len(rejects)} of {read} records ({len(rejects) / read:.0%}) are unusable; the limit for this source is "
                          f"{rules['max_reject_rate']:.0%}")

    point = s.begin_nested()  # a file is loaded whole or not at all
    try:
        result = dataset.load(s, good, origin=record.id, rules=rules)
    except Exception:
        point.rollback()
        raise
    by_row = dict(good)
    for n, reason in result.rejects:
        rejects.append({"row": n, "reason": reason, "record": {k: str(v) for k, v in by_row[n].items()}})
    if read and len(rejects) / read > rules["max_reject_rate"]:
        point.rollback()
        return quarantine(f"{len(rejects)} of {read} records ({len(rejects) / read:.0%}) are unusable; the limit for this source is "
                          f"{rules['max_reject_rate']:.0%}")
    point.commit()
    kept = [r for r in rejects if r]
    record.stats = {"read": read, "loaded": result.inserted, "updated": result.updated, "duplicates": result.unchanged,
                    "rejected": len(rejects), "source_amount": round(source_amount, 2), "loaded_amount": round(result.amount, 2),
                    "reconciled": read == result.inserted + result.updated + result.unchanged + len(rejects), "warnings": warnings,
                    "reject_samples": kept[:SAMPLE_REJECTS], "rejects_at": _write_rejects(s, source, run, obj.key, kept)}
    s.add(record)
    s.flush()
    return record, result.parties


def _write_rejects(s: Session, source: DataSource, run: PipelineRun, key: str, rejects: list[dict]) -> str | None:
    rejects = [r for r in rejects if r]
    if not rejects:
        return None
    try:
        store = locations.quarantine_store(tenant_of(s))
        target = f"{source.id}/{run.id}/{key}.rejects.jsonl"
        store.write(target, "\n".join(json.dumps(r, default=str) for r in rejects).encode())
        return store.url + target
    except StorageError:
        return None  # the samples kept on the run still say what was wrong


@system_work
def run_source(s: Session, source: DataSource, by: str, trigger: str = "manual", batch_id: str | None = None) -> PipelineRun:
    """Run one source now. Never raises for a problem with the data or the storage: the run records it."""
    from ..agents.investigator import investigate
    from ..alerts.hub import route_alerts
    from ..detection.engine import run_detection

    tenant = tenant_of(s)
    rules = clean_rules(source.rules)
    run = PipelineRun(id=new_id("run"), source_id=source.id, batch_id=batch_id or new_id("batch"), trigger=trigger, started_by=by,
                      mapping_version=source.mapping_version, steps=[], stats={})
    s.add(run)
    s.flush()
    totals = {"objects_found": 0, "objects_new": 0, "objects_loaded": 0, "objects_quarantined": 0, "read": 0, "loaded": 0, "updated": 0,
              "duplicates": 0, "rejected": 0, "source_amount": 0.0, "loaded_amount": 0.0, "alerts": 0, "cases": 0, "reconciled": True}
    try:
        store = locations.store_for(tenant, source.root)
        objects, fresh = discover(s, source, store)
    except StorageError as e:
        run.status, run.error = "failed", str(e)
        _step(run, "discover", "failed", str(e))
        return _finish(s, source, run, totals)
    totals["objects_found"], totals["objects_new"] = len(objects), len(fresh)
    newest = max((o.modified for o in objects if o.modified), default=None)
    source.state = dict(source.state or {}) | {"newest_object_at": newest.isoformat() if newest else None, "objects": len(objects)}
    _step(run, "discover", "done", f"{len(fresh)} new of {len(objects)} matching file(s) in {store.url}{source.prefix}", found=len(objects), new=len(fresh))
    if not fresh:
        run.status = "nothing_new"
        return _finish(s, source, run, totals)

    touched: set[str] = set()
    for obj in fresh:
        record, parties = _process_object(s, source, run, store, obj, rules)
        touched |= parties
        st = record.stats
        totals["objects_loaded" if record.status == "loaded" else "objects_quarantined"] += 1
        for k in ("read", "rejected"):
            totals[k] += st.get(k, 0)
        if record.status == "loaded":
            for k in ("loaded", "updated", "duplicates", "source_amount", "loaded_amount"):
                totals[k] += st.get(k, 0)
            totals["reconciled"] = totals["reconciled"] and st["reconciled"]
    _step(run, "validate", "done" if not totals["objects_quarantined"] else "held",
          f"{totals['objects_quarantined']} file(s) quarantined, {totals['rejected']} record(s) rejected" if totals["objects_quarantined"] or totals["rejected"]
          else "Every file passed its quality rules", quarantined=totals["objects_quarantined"], rejected=totals["rejected"])
    _step(run, "load", "done", f"{totals['loaded']} new, {totals['updated']} updated, {totals['duplicates']} already present",
          loaded=totals["loaded"], updated=totals["updated"], duplicates=totals["duplicates"])
    _step(run, "reconcile", "done" if totals["reconciled"] else "failed",
          "Every record read is accounted for: loaded, updated, already present or rejected" if totals["reconciled"]
          else "Records read do not add up to records accounted for", read=totals["read"])
    if DATASETS[source.dataset].triggers_detection and touched:
        alerts = run_detection(s, touched)
        cases = route_alerts(s, alerts)
        for c in cases:
            investigate(s, c)
        totals["alerts"], totals["cases"] = len(alerts), len(cases)
        _step(run, "detect", "done", f"{len(alerts)} alert(s) raised across {len(touched)} customer(s), {len(cases)} case(s) opened or updated",
              alerts=len(alerts), cases=len(cases))
    totals["source_amount"], totals["loaded_amount"] = round(totals["source_amount"], 2), round(totals["loaded_amount"], 2)
    if not totals["objects_loaded"]:
        run.status = "quarantined"
    elif totals["objects_quarantined"]:
        run.status = "partial"
    else:
        run.status = "succeeded_with_rejects" if totals["rejected"] else "succeeded"
    return _finish(s, source, run, totals)


def _finish(s: Session, source: DataSource, run: PipelineRun, totals: dict) -> PipelineRun:
    run.stats, run.finished = totals, now()
    source.last_run_at = run.finished
    source.state = dict(source.state or {}) | {"last_status": run.status, "last_run": run.id}
    s.flush()
    return run


@system_work
def run_many(s: Session, sources: list[DataSource], by: str, trigger: str = "manual") -> list[PipelineRun]:
    """Run several sources as one batch, each dataset after the ones it depends on.

    If a source fails outright, sources of the datasets that depend on it are skipped rather than loaded against
    incomplete reference data.
    """
    batch = new_id("batch")
    order = in_dependency_order(sorted({x.dataset for x in sources}))
    runs: list[PipelineRun] = []
    failed: set[str] = set()
    for dataset in order:
        blocked = sorted(set(DATASETS[dataset].depends_on) & failed)
        for source in sorted((x for x in sources if x.dataset == dataset), key=lambda x: x.name):
            if blocked:
                run = PipelineRun(id=new_id("run"), source_id=source.id, batch_id=batch, trigger=trigger, started_by=by, status="skipped",
                                  mapping_version=source.mapping_version, stats={}, finished=now(),
                                  error=f"Skipped because the {' and '.join(blocked)} load it depends on failed in this batch")
                run.steps = [{"step": "discover", "status": "skipped", "detail": run.error, "counts": {}, "at": now().isoformat()}]
                s.add(run)
                source.state = dict(source.state or {}) | {"last_status": "skipped", "last_run": run.id}
                s.flush()
                failed.add(dataset)
            else:
                run = run_source(s, source, by, trigger, batch)
                if run.status == "failed":
                    failed.add(dataset)
            runs.append(run)
    return runs


@system_work
def run_due(s: Session, by: str = "scheduler") -> list[PipelineRun]:
    due = [x for x in s.scalars(select(DataSource).where(DataSource.enabled.is_(True))) if health(x)["due"]]
    return run_many(s, due, by, "schedule") if due else []


def lineage(s: Session, origin: str) -> dict | None:
    """Where a loaded record came from: the file, the source and the run."""
    obj = s.get(IngestedObject, origin)
    if obj is None:
        return None
    source = s.get(DataSource, obj.source_id)
    try:
        location = locations.root_of(tenant_of(s), source.root)["url"] if source else ""
    except StorageError:
        location = ""
    return {"object": location + obj.key, "key": obj.key, "fingerprint": obj.fingerprint, "loaded_at": obj.at.isoformat(), "run_id": obj.run_id,
            "source_id": obj.source_id, "source": source.name if source else None, "mapping_version": s.get(PipelineRun, obj.run_id).mapping_version}
