# Restoring from backup

## When to use this

- Accidental deletion of documents or database records
- Server migration
- Disaster recovery

---

## Prerequisites

- The stack must be running: `docker compose up -d`
- You need a backup file from `./backups/`

List available backups:

```bash
ls -lh backups/
```

---

## Restore database

> **Warning:** This overwrites the current database. All data ingested after the backup date will be lost. Stop the API first to prevent conflicting writes during the restore.

```bash
# 1. Stop the API to prevent writes during restore
docker compose stop api

# 2. Drop and recreate the database
docker compose exec -T db psql -U sdai -c "DROP DATABASE IF EXISTS sdai;"
docker compose exec -T db psql -U sdai -c "CREATE DATABASE sdai;"

# 3. Restore from the compressed dump (adjust filename to your chosen backup)
zcat backups/db_2025-06-01_02-00-00.sql.gz \
  | docker compose exec -T db psql -U sdai sdai

# 4. Restart the API
docker compose start api
```

Expected output from step 3: a stream of `CREATE TABLE`, `INSERT`, `ALTER TABLE` statements with no errors. Warnings about pre-existing roles or extensions are harmless.

---

## Restore images

```bash
# Clear the current data directory and restore from archive
docker compose exec -T api sh -c "rm -rf /data && mkdir -p /data"
cat backups/images_2025-06-01_02-00-00.tar.gz \
  | docker compose exec -T api tar xzf - -C /data
```

No restart required — the API reads files on demand.

---

## Verify restore

```bash
# Document counts per ingested table
docker compose exec db psql -U sdai sdai \
  -c "SELECT relname AS table_name, n_live_tup AS row_count
      FROM pg_stat_user_tables
      WHERE schemaname = 'public'
      ORDER BY table_name;"

# User accounts
docker compose exec db psql -U sdai sdai \
  -c "SELECT email, role, is_active FROM sdai_users;"

# Audit log record count
docker compose exec db psql -U sdai sdai \
  -c "SELECT COUNT(*) FROM sdai_audit_log;"
```

Open the dashboard in your browser and confirm documents, review queue, and audit log are intact.
