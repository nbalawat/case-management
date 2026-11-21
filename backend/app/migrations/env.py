"""Alembic's entry point. Always handed a connection by app.migrations; there is no alembic.ini."""
from alembic import context

from app.db import Base
from app.domain import models  # noqa: F401  register every table

conn = context.config.attributes.get("connection")
if conn is None:
    raise RuntimeError("Run migrations with `python -m app.migrate`, which supplies the database connection.")
# render_as_batch: SQLite cannot alter a column in place, so changes are written as copy-and-swap, which Postgres
# runs as ordinary ALTERs.
context.configure(connection=conn, target_metadata=Base.metadata, compare_type=True, render_as_batch=True,
                  include_object=lambda obj, name, kind, reflected, compare_to: not (kind == "table" and name in ("schema_version", "iceberg_tables", "iceberg_namespace_properties")))
with context.begin_transaction():
    context.run_migrations()
