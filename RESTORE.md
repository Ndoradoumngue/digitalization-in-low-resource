# Restoring from backup

This file covers two kinds of backup:

- **Quick local backups** made with `./backup.sh` (database dump + images archive in `./backups/`,
  7 days) - sections "Restore database" to "Verify restore" below.
- **Off-site backups** made every night by the `backup` service with restic (encrypted, 30 daily +
  12 monthly snapshots, on a separate SFTP server and optionally a NAS) - from "Off-site backups"
  onward. In a hybrid local + cloud deployment, these are what you rebuild a node from.

The local/cloud sync is **not** a backup: a deletion or a wrong edit is synchronised too.

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

No restart required - the API reads files on demand.

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

---

## Off-site backups (restic)

Each node backs itself up every night at `BACKUP_TIME` (default 02:30): a `pg_dump` of the database,
`/data` (uploads, page images, versions) and `documents/`. Settings in `.env`:

| Variable | Meaning |
|---|---|
| `BACKUP_SFTP_TARGET` | restic repository on a separate server, e.g. `sftp:backup@backup.example.net:/sdai` - ideally at another provider than the cloud node |
| `RESTIC_PASSWORD` | encryption key. **Without it the backups cannot be read.** Keep a copy in a safe place that is not one of the servers |
| `BACKUP_NAS_PATH` | optional: a NAS directory mounted on the host, receives a second repository |
| `BACKUP_TIME` | local time of the nightly run |

SFTP access: put the private key and `known_hosts` for the backup server in `./backup/ssh/`
(`id_ed25519`, `known_hosts`, mode 600). The `backup/` directory is git-ignored.

```bash
docker compose run --rm backup now            # one backup right away (also checks the setup)
docker compose run --rm backup restore-test   # restore drill, see below
```

Every run is recorded in the node's `sdai_backup_runs` table and shown on the admin
**Synchronisation** page, which raises an alert when the last run failed or no target is configured.

List the snapshots of a node:

```bash
docker compose run --rm --entrypoint sh backup -c \
  'restic -r "${BACKUP_SFTP_TARGET:-/nas/sdai-restic}" snapshots --host "$NODE_ID"'
```

### Destroyed documents and backups

An approved destruction (or the automatic trash purge) deletes the document's files on both
servers immediately, but **the restic snapshots taken before it still contain them** until those
snapshots expire under the retention policy (30 daily, 12 monthly - so up to about a year). The
procès-verbal d'élimination says so. Nothing is restored from a snapshot automatically:

- Restoring a whole node from a snapshot older than a destruction brings the destroyed files and rows
  back on that node. In a hybrid deployment the sync then receives the tombstones and the list of
  destroyed files (`sdai_destroyed_files`) from the peer and deletes the files again; within
  `DESTROYED_FILES_SWEEP_SECONDS` every node also removes version, page and history rows a restored
  node may have sent back for a destroyed document. On a standalone
  server, re-run the destruction: check the **Éliminations** page and the audit log
  (`document_destroyed`) for what was destroyed after the snapshot date.
- If a destruction must also reach the backups sooner (legal obligation), the only way is to forget the
  older snapshots with `restic forget` and `restic prune` - a deliberate, recorded operation by the
  backup administrator, outside the application.
- The restore drill accounts for version rows removed by destructions when it compares row counts.

### Restore drill

`docker compose run --rm backup restore-test` restores the latest snapshot into a scratch directory
and a throwaway database, then checks: the main tables restore with plausible row counts, every audit
log row still matches its hash, and a sample of 50 files is byte-identical to the live ones. It
prints `PASSED` / `FAILED` and leaves nothing behind. Run it once a month and after any change to
the backup setup (`RESTORE_FROM=nas` to test the NAS repository).

---

## Restore a node from restic on a clean VM

Use this when a server is lost (disk failure, deleted VM) and you rebuild it from its own backup.

1. Install Docker on the new machine, copy the project directory (or clone it) and restore the
   node's `.env` - **same** `NODE_ID`, `NODE_ROLE`, `AUTH_SECRET_KEY`, `SYNC_NODE_TOKEN`,
   `POSTGRES_*`, `RESTIC_PASSWORD`, `BACKUP_SFTP_TARGET`. Put the SSH key back in `./backup/ssh/`.
   On the cloud node, use `-f docker-compose.yml -f docker-compose.cloud.yml` in every command below.
2. Fetch the latest snapshot to `./restore` on the host:

   ```bash
   docker compose build backup
   docker compose run --rm --no-deps -v "$PWD/restore:/restore" --entrypoint sh backup -c \
     'restic -r "$BACKUP_SFTP_TARGET" restore latest --host "$NODE_ID" --tag sdai --target /restore'
   ```

3. Database - start only PostgreSQL and load the dump **before** the API ever starts:

   ```bash
   docker compose up -d db
   docker compose exec -T db pg_restore --no-owner -U sdai -d sdai < restore/backup-staging/sdai.dump
   ```

4. Files:

   ```bash
   docker compose run --rm --no-deps -v "$PWD/restore:/restore:ro" --entrypoint sh api \
     -c 'cp -a /restore/data/. /data/'
   cp -a restore/app/documents/. documents/
   ```

5. Start everything: `docker compose up -d`, then `rm -rf restore`.

**What sync does next (hybrid deployment):** the restored node is older than its peer. The sync
notices it on its own - the two nodes disagree about how much of each other's changelog they hold -
and both resend a full snapshot, merged field by field on the original timestamps: the work done on
either side since the backup comes back (documents filed after the backup included), nothing newer is
overwritten, and record numbering continues after the highest number in use. Watch the
**Synchronisation** page until both sides show nothing pending, and check the conflict list.

---

## Rebuild the local server from the cloud

Fastest when the local server is lost and the cloud node is up to date (no need for the backup).

1. Install the stack on the new local server with its `.env` (`NODE_ROLE=local`, a `NODE_ID` - the
   old one is fine, `SYNC_PEER_URL=https://<cloud domain>`, same `SYNC_NODE_TOKEN` and
   `AUTH_SECRET_KEY` as the cloud).
2. Before the first start, make the empty database ready to receive the deployment:

   ```bash
   docker compose run --rm api python -m sync.join
   docker compose up -d
   ```

3. The sync worker pulls every tenant, user, document, version and audit entry from the cloud, then
   downloads the files. Large archives take a while (see "pending files" on the Synchronisation page).
   Documents uploaded on the cloud meanwhile are processed as soon as their files are here.

## Rebuild the cloud node from the local server

1. Create the new VM, install the stack with the cloud `.env` (`NODE_ROLE=cloud`, empty
   `SYNC_PEER_URL`, same `SYNC_NODE_TOKEN` and `AUTH_SECRET_KEY` as the local server).
2. Before the first start:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.cloud.yml run --rm api python -m sync.join
   docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d
   ```

3. Point DNS at the new VM. The local server's next sync cycle sends the whole deployment, then the
   files. Remote users can log in as soon as the users have arrived (same accounts, same passwords).

`python -m sync.join` refuses to run on a database that already holds users or documents - it is only
for an empty, freshly installed node.
