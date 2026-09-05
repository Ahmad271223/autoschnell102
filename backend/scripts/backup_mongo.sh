#!/usr/bin/env bash
# Tägliches MongoDB-Backup für den späteren Linux-Server (Gegenstück zum
# Windows-Taskplaner-Job). Einrichtung dort:
#   crontab -e
#   0 3 * * * /pfad/zu/backend/scripts/backup_mongo.sh >> /var/backups/autoschnell/backup.log 2>&1
#
# Nutzt dieselbe Python-Logik wie unter Windows — funktioniert überall, wo
# pymongo installiert ist. Aufbewahrung: 14 Backups (siehe backup_mongo.py).
set -euo pipefail
DIR="${1:-/var/backups/autoschnell}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$DIR"
exec python3 -X utf8 "$SCRIPT_DIR/backup_mongo.py" --dir "$DIR"
