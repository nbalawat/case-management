"""Backup and restore: the operational database and customer documents, whole or one client at a time.

A backup is a set of files in any object store (BACKUP_URL: a directory on a laptop, a bucket or container in a
deployment), written by the application itself rather than a database tool, so the same backup restores into
Postgres or SQLite, into the same deployment or another stamp, on any cloud:

    <id>/db/<table>/part-00000.jsonl.gz    every row, typed, read in one consistent snapshot
    <id>/evidence/<client>/...             the customer documents
    <id>/manifest.json                     written last: rows, files and SHA-256 of each, schema revision

A backup without its manifest is incomplete and is never restored. Restoring refuses a database that already holds
data (whole backups) or the client (one-client backups), and a schema revision other than the backup's: it never
overwrites anything. A drill backs up, restores into a scratch database, and compares every table row for row.

    python -m app.backup create [--client ID]     python -m app.backup list
    python -m app.backup verify ID                python -m app.backup restore ID --into URL
    python -m app.backup drill

The lake is not copied: its files are immutable on object storage (keep that bucket versioned) and every table can
be filled again from the bus or by `app.lake.backfill`.
"""
from .manifest import BackupError, Manifest
from .service import create, drill, list_backups, restore, verify

__all__ = ["BackupError", "Manifest", "create", "drill", "list_backups", "restore", "verify"]
