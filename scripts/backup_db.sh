#!/usr/bin/env bash
# Daily compressed database backup on the server, 14 days kept.
# Run from the repository folder on the server, or from cron:
#   15 3 * * * bash /home/deploy/beansbeats/scripts/backup_db.sh >> /home/deploy/backups/backup.log 2>&1
# Copy the newest backup to the laptop (run on the laptop):
#   scp "deploy@<server>:backups/*.sql.gz" ./bb_backups/
# Restore into the running database (destructive; see README section 6, Backups):
#   gunzip -c FILE.sql.gz | docker compose -f docker-compose.prod.yml exec -T db psql -U beansbeats -d beansbeats_db
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DB_USER="${DB_USER:-beansbeats}"
DB_NAME="${DB_NAME:-beansbeats_db}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
file="$BACKUP_DIR/beansbeats_$(date +%Y-%m-%d_%H%M).sql.gz"
# --clean --if-exists lets the dump replace the tables when restored. No password is needed inside the db container.
docker compose -f "$COMPOSE_FILE" exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists | gzip > "$file.part"
mv "$file.part" "$file"
chmod 600 "$file"
find "$BACKUP_DIR" -name 'beansbeats_*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Iseconds) backup written: $file ($(du -h "$file" | cut -f1))"
