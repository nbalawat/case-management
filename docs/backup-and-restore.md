# Backup and restore

The application writes its own backups. It does not use a database tool, so one backup restores into Postgres or SQLite, into the same deployment or into another stamp, on any cloud. A backup holds either the whole platform or one client. Backups go to `BACKUP_URL`, which can be any object store the platform already speaks (`file://`, `s3://`, `abfs://`, `gs://`).

```
make backup                      # the whole platform; make backup CLIENT=meridian for one client
make backup-drill                # back up, restore into a scratch database, compare every table
cd backend && uv run python -m app.backup list
uv run python -m app.backup verify 20261121T180000Z-platform
uv run python -m app.backup restore 20261121T180000Z-meridian --into postgresql+psycopg://.../stamp2 --documents s3://stamp2-evidence
```

Platform staff can also back up, verify and run a drill from the operator console, in the **Backups and restore drills** card. Restoring is deliberately only a command, because it writes a whole database.

## What a backup is

```
<id>/db/<table>/part-00000.jsonl.gz     every row, typed by its column, 50,000 rows to a part
<id>/evidence/<client>/...              customer documents
<id>/manifest.json                      written last: schema revision, rows and SHA-256 of every part, a digest of each table
```

- **One moment in time.** Every table is read inside one transaction (`REPEATABLE READ` on Postgres), so a backup taken while people work is still consistent.
- **Complete or absent.** The manifest is written last. A backup without one is never listed or restored.
- **Checked before use.** `verify` reads every part back against its checksum and row count, and `restore` runs `verify` first. Nothing is written from a damaged backup.
- **Never overwrites.** A whole-platform restore goes only into an empty database. A one-client restore is refused if that client already exists in the target. Documents that already exist are left alone.
- **Same schema.** The target must be at the backup's migration revision. An empty target is migrated to it first. A backup from another revision is refused with an explanation.
- **Moving a client.** A one-client restore lets the target database number that client's audit-trail and version rows afresh, because another database may already use those numbers. Nothing refers to those numbers, and every other id is kept. This is how a client moves from the shared platform to a dedicated stamp.

## The drill

`drill` backs up the platform, checks every file, restores into a scratch database (a temporary SQLite file, or `--scratch URL`) and compares a digest of every table's content with the original. The digest is order-independent and leaves out the database-assigned numbers. The result is written to `drills/` beside the backups and shown in the operator console. The Helm chart schedules a drill every week (`backup.drillSchedule`).

## What is not in a backup

- **The lake.** Its Parquet files are immutable on object storage: keep that bucket versioned, and every table can be filled again from Kafka or by `python -m app.lake.backfill`.
- **Kafka topics.** They carry events in flight. The database and its outbox are the record.
- **Secrets.** These include the database password, API keys and identity provider settings, and stay in the deployment's secret store.

Point-in-time recovery between nightly backups belongs to the database service: Postgres WAL archiving, or a managed database's PITR. Application backups are for portability, client moves, and proving restores work.

## Measured

On 2026-11-21 a cloud container (4 vCPUs) ran a drill from Postgres 17 into a scratch Postgres database, after the load tests. It covered 525,045 rows in 44 tables, mostly transactions:

| Step | Time |
|---|---:|
| Back up (13 MB compressed) | 19.4 s |
| Verify every part | 0.5 s |
| Restore | 24.3 s |

Every table was identical after the restore. The restored database's sequences continued from the restored rows.
