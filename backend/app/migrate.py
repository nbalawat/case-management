"""Run migrations against the configured database. See app/migrations for what each command does.

    python -m app.migrate                       upgrade to the newest schema
    python -m app.migrate current               print the revision the database holds
    python -m app.migrate check                 exit 1 if the models differ from the newest migration
    python -m app.migrate revision "message"    write the next migration from the difference
"""
from __future__ import annotations

import sys

from alembic import command

from . import migrations
from .db import SchemaMismatch, ensure_schema, make_engine
from .config import settings


def _scratch():
    """A throwaway database at the newest migration, to compare the models against."""
    eng = make_engine("sqlite://")
    migrations.upgrade(eng)
    return eng


def main(argv: list[str]) -> int:
    what = argv[0] if argv else "upgrade"
    if what == "upgrade":
        object.__setattr__(settings, "migrate_on_start", True)
        try:
            ensure_schema()
        except SchemaMismatch as e:
            print(str(e), file=sys.stderr)
            return 1
        from .db import engine

        with engine.connect() as conn:
            print(f"At migration {migrations.current(conn)}.")
        return 0
    if what == "current":
        from .db import engine

        with engine.connect() as conn:
            print(migrations.current(conn) or "none (migrations have never run on this database)")
        return 0
    if what == "check":
        diffs = migrations.drift(_scratch())
        for d in diffs:
            print(d)
        print("The models match the newest migration." if not diffs else f"{len(diffs)} difference(s): write a migration with `python -m app.migrate revision \"what changed\"`.")
        return 1 if diffs else 0
    if what == "revision" and len(argv) == 2 and argv[1].strip():
        eng = _scratch()
        if not migrations.drift(eng):
            print("The models match the newest migration. Nothing to write.")
            return 0
        number = f"{int(migrations.head()[:4]) + 1:04d}"
        with eng.begin() as conn:
            command.revision(migrations.config(conn), message=argv[1].strip(), autogenerate=True, rev_id=number)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
