#!/bin/bash
# Runs sdai-backup.sh every night at BACKUP_TIME (HH:MM, container clock,
# default 02:30). `docker compose run --rm backup now` runs it once.
set -u
if [ "${1:-}" = "now" ]; then
  exec /usr/local/bin/sdai-backup.sh
fi
if [ "${1:-}" = "restore-test" ]; then
  exec /usr/local/bin/restore-test.sh
fi
AT="${BACKUP_TIME:-02:30}"
echo "[backup] nightly at $AT (node ${NODE_ID:-local})"
while true; do
  now=$(date +%s)
  next=$(date -d "today $AT" +%s)
  [ "$next" -le "$now" ] && next=$(date -d "tomorrow $AT" +%s)
  sleep $((next - now))
  /usr/local/bin/sdai-backup.sh || echo "[backup] run failed (recorded in sdai_backup_runs)"
done
