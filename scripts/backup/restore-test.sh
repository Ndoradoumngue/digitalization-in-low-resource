#!/bin/bash
# Restore test: restores the latest snapshot of this node into a scratch
# directory and a throwaway database, then compares it with the live node
# (row counts of the main tables, a sample of files by sha256). Leaves
# nothing behind. Run monthly, and after any change to the backup setup:
#
#   docker compose run --rm backup restore-test
#
# Uses the SFTP repository, or the NAS one with RESTORE_FROM=nas.
set -euo pipefail

NODE="${NODE_ID:-local}"
if [ "${RESTORE_FROM:-sftp}" = "nas" ]; then
  export RESTIC_REPOSITORY=/nas/sdai-restic
else
  export RESTIC_REPOSITORY="${BACKUP_SFTP_TARGET:?BACKUP_SFTP_TARGET not set}"
fi
WORK=$(mktemp -d /tmp/sdai-restore-XXXX)
TESTDB="sdai_restore_test_$$"
cleanup() {
  rm -rf "$WORK"
  psql -q -d postgres -c "DROP DATABASE IF EXISTS $TESTDB" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[restore-test] restoring latest snapshot of $NODE from $RESTIC_REPOSITORY"
restic restore latest --host "$NODE" --tag sdai --target "$WORK" >/dev/null
test -s "$WORK/backup-staging/sdai.dump" || { echo "FAIL: no database dump in snapshot"; exit 1; }

psql -q -d postgres -c "CREATE DATABASE $TESTDB" >/dev/null
pg_restore --no-owner -d "$TESTDB" "$WORK/backup-staging/sdai.dump"

fail=0
for table in sdai_tenants sdai_users sdai_audit_log sdai_document_versions batch_documents; do
  live=$(psql -At -c "SELECT count(*) FROM $table")
  restored=$(psql -At -d "$TESTDB" -c "SELECT count(*) FROM $table")
  if [ "$table" = sdai_document_versions ]; then
    # Destructions (GED Phase 6) delete the version rows of destroyed
    # documents - the snapshot may hold those.
    destroyed=$(psql -At -c "SELECT COALESCE(sum(jsonb_array_length(COALESCE(d->'versions', '[]'))), 0)
      FROM sdai_destruction_records r, jsonb_array_elements(r.documents) d WHERE r.executed_at IS NOT NULL" 2>/dev/null || echo 0)
    live=$((live + ${destroyed:-0}))
  fi
  # The snapshot is older than the live database: it may hold fewer rows, never more.
  if [ "$restored" -gt "$live" ] || [ "$restored" -eq 0 -a "$live" -gt 0 ]; then
    echo "FAIL: $table restored=$restored live=$live"; fail=1
  else
    echo "ok   $table restored=$restored live=$live"
  fi
done

# Audit chain of the restored copy: every row still hashes to its row_hash.
broken=$(psql -At -d "$TESTDB" -c "SELECT count(*) FROM sdai_audit_log WHERE chain_seq IS NOT NULL AND row_hash <>
  sdai_audit_row_hash(id, origin_node, chain_seq, created_at, user_id, user_email, action, table_name,
                      document_id, details, ip_address, prev_hash)")
[ "$broken" = "0" ] && echo "ok   audit chain hashes" || { echo "FAIL: $broken audit rows altered"; fail=1; }

checked=0
while IFS= read -r -d '' f; do
  rel="${f#"$WORK"}"
  if [ -f "$rel" ]; then
    [ "$(sha256sum < "$f")" = "$(sha256sum < "$rel")" ] || { echo "FAIL: $rel differs"; fail=1; }
    checked=$((checked + 1))
  fi
done < <(find "$WORK/data" -type f -print0 2>/dev/null | shuf -z -n 50)
if [ "$checked" -eq 0 ] && [ -n "$(find /data -type f -print -quit 2>/dev/null)" ]; then
  echo "FAIL: the snapshot holds none of the live files"; fail=1
else
  echo "ok   $checked sampled files identical to the live ones"
fi

[ $fail -eq 0 ] && echo "[restore-test] PASSED" || echo "[restore-test] FAILED"
exit $fail
