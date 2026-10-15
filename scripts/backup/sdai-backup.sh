#!/bin/bash
# One backup run of this node: database dump + DATA_DIR + documents/, with
# restic, to the off-site SFTP repository (BACKUP_SFTP_TARGET) and, if set,
# a second repository on a NAS (BACKUP_NAS_PATH, mounted at /nas).
# Sync between nodes is NOT a backup - a deletion syncs too; these
# snapshots are what you go back to.
#
# Environment (docker-compose `backup` service):
#   PGHOST PGUSER PGPASSWORD PGDATABASE   the node's database
#   RESTIC_PASSWORD                       repository encryption key (keep a copy OFF the servers)
#   BACKUP_SFTP_TARGET                    e.g. sftp:backup@backup.example.net:/sdai
#   BACKUP_NAS_PATH                       optional, a mounted NAS directory
#   NODE_ID                               snapshot host name
# The result of every run goes to the node's sdai_backup_runs table (shown
# on the admin Synchronisation page, alert on failure).
set -uo pipefail

NODE="${NODE_ID:-local}"
STAGING=/backup-staging
SOURCES=("$STAGING" /data /app/documents)
STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)

record() { # status target snapshot bytes message
  psql -v ON_ERROR_STOP=1 -q -c "INSERT INTO sdai_backup_runs
      (node_id, started_at, finished_at, status, target, snapshot_id, bytes_added, message)
      VALUES ('$NODE', '$STARTED', now(), '$1', NULLIF('$2', ''), NULLIF('$3', ''),
              NULLIF('$4', '')::bigint, NULLIF(\$msg\$$5\$msg\$, ''))" \
    || echo "[backup] could not record the run in the database"
}

run_repo() { # label repository
  local label="$1" repo="$2" out snap bytes
  export RESTIC_REPOSITORY="$repo"
  local probe
  if ! probe=$(restic cat config 2>&1); then
    if echo "$probe" | grep -qiE "wrong password|no key found"; then
      record failed "$label" "" "" "RESTIC_PASSWORD does not open this repository"
      return 1
    fi
    echo "[backup] initialising $label repository"
    if ! probe=$(restic init 2>&1); then
      record failed "$label" "" "" "repository unreachable or not initialisable: $(echo "$probe" | tail -n 2 | tr '\n' ' ')"
      return 1
    fi
  fi
  if ! out=$(restic backup --host "$NODE" --tag sdai --json "${SOURCES[@]}" 2>&1); then
    # restic's own reasons (unreadable files, unreachable repository, ...)
    local why
    why=$(echo "$out" | grep -v '"message_type":"status"' | grep -v '"message_type":"summary"' \
      | sed -n 's/.*"message":"\([^"]*\)".*/\1/p; /^[^{]/p' | head -n 5 | tr '\n' ' ')
    record failed "$label" "" "" "${why:-restic backup failed}"
    return 1
  fi
  snap=$(echo "$out" | grep '"message_type":"summary"' | sed -n 's/.*"snapshot_id":"\([0-9a-f]*\)".*/\1/p')
  bytes=$(echo "$out" | grep '"message_type":"summary"' | sed -n 's/.*"data_added":\([0-9]*\).*/\1/p')
  if ! restic forget --host "$NODE" --tag sdai --keep-daily 30 --keep-monthly 12 --prune >/dev/null 2>&1; then
    record failed "$label" "$snap" "$bytes" "backup ok, retention (forget/prune) failed"
    return 1
  fi
  record success "$label" "$snap" "$bytes" ""
  echo "[backup] $label ok: snapshot $snap, $bytes bytes added"
}

if [ -z "${BACKUP_SFTP_TARGET:-}" ] && [ -z "${BACKUP_NAS_PATH:-}" ]; then
  record not_configured "" "" "" "Set BACKUP_SFTP_TARGET (and RESTIC_PASSWORD) to enable off-site backups"
  echo "[backup] no target configured"
  exit 1
fi
if [ -z "${RESTIC_PASSWORD:-}" ]; then
  record failed "" "" "" "RESTIC_PASSWORD is not set"
  exit 1
fi

mkdir -p "$STAGING"
rm -f "$STAGING"/sdai.dump
if ! pg_dump -Fc -f "$STAGING/sdai.dump"; then
  record failed "" "" "" "pg_dump failed"
  exit 1
fi

status=0
if [ -n "${BACKUP_SFTP_TARGET:-}" ]; then
  run_repo sftp "$BACKUP_SFTP_TARGET" || status=1
fi
if [ -n "${BACKUP_NAS_PATH:-}" ]; then
  run_repo nas /nas/sdai-restic || status=1
fi
rm -f "$STAGING"/sdai.dump
exit $status
