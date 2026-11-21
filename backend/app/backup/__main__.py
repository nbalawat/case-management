"""python -m app.backup create|list|verify|restore|drill. See the package docstring."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .manifest import BackupError


def main(argv: list[str] | None = None) -> int:
    from . import service

    ap = argparse.ArgumentParser(prog="python -m app.backup")
    ap.add_argument("--to", default=None, help="backup location (default BACKUP_URL)")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="back up the whole platform, or one client")
    c.add_argument("--client", default=None)
    c.add_argument("--no-documents", action="store_true")
    sub.add_parser("list", help="complete backups, newest first, and recent drills")
    v = sub.add_parser("verify", help="read a backup back and check every checksum")
    v.add_argument("id")
    r = sub.add_parser("restore", help="restore into a database that does not hold this data yet")
    r.add_argument("id")
    r.add_argument("--into", required=True, help="database URL to restore into (created and migrated if empty)")
    r.add_argument("--documents", default=None, help="evidence location to restore documents into (default: not restored)")
    d = sub.add_parser("drill", help="back up, restore into a scratch database, compare every table")
    d.add_argument("--scratch", default=None, help="scratch database URL (default: a temporary SQLite file)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        store = service.target(a.to)
        if a.command == "create":
            out = service.create(scope=a.client or "platform", store=store, with_documents=not a.no_documents).summary()
        elif a.command == "list":
            out = {"backups": [m.summary() for m in service.list_backups(store)], "drills": service.drills(store)}
        elif a.command == "verify":
            out = service.verify(a.id, store)
        elif a.command == "restore":
            from .. import migrations
            from ..db import make_engine
            from ..storage import open_store

            eng = make_engine(a.into)
            m = service.load(store, a.id)
            with eng.connect() as conn:
                empty = not migrations.has_tables(conn)
            if empty:
                migrations.upgrade(eng, m.schema or "head")
            out = service.restore(a.id, engine=eng, store=store, evidence=open_store(a.documents) if a.documents else None)
        else:
            out = service.drill(store=store, scratch_url=a.scratch)
    except BackupError as e:
        print(f"Backup: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
