#!/bin/bash
# Usage: ./backup.sh
# Creates timestamped backups of the database and images.
# Keeps the last 7 daily backups and deletes older ones.
# Safe to run while the stack is live.

set -e

BACKUP_DIR="./backups"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

echo "=== SDAI Backup — $TIMESTAMP ==="

# 1. Database dump
echo "Backing up PostgreSQL database..."
docker compose exec -T db pg_dump \
  -U sdai sdai \
  | gzip > "$BACKUP_DIR/db_$TIMESTAMP.sql.gz"
echo "Database backup: $BACKUP_DIR/db_$TIMESTAMP.sql.gz"

# 2. Images backup
# DATA_DIR is a Docker named volume (/data inside the api container).
# Stream the archive out via exec so no host mount is required.
echo "Backing up document images..."
docker compose exec -T api tar czf - -C /data . \
  | cat > "$BACKUP_DIR/images_$TIMESTAMP.tar.gz"
echo "Images backup: $BACKUP_DIR/images_$TIMESTAMP.tar.gz"

# 3. Delete backups older than KEEP_DAYS
echo "Cleaning up backups older than $KEEP_DAYS days..."
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +$KEEP_DAYS -delete
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +$KEEP_DAYS -delete

echo "=== Backup complete ==="
