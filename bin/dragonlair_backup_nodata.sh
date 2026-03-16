#!/bin/bash
# dragonlair_backup_nodata.sh
# Backup configs, scripts, and model lists (not model data) to remote server

set -euo pipefail

BACKUP_SRC=(
  "/opt/ai-stack/"
  "/home/daravenrk/dragonlair/"
)
BACKUP_DEST="daravenrk@192.168.86.34:/backups/dragonlair"
LOGFILE="/tmp/dragonlair_backup_nodata.log"

# Exclude model data directories
EXCLUDES=(
  "--exclude=ai/ollama-amd/**"
  "--exclude=ai/ollama-nvidia/**"
  "--exclude=dragonlair/model-store/**"
  "--exclude=dragonlair/model-cache/**"
  "--exclude=dragonlair/model-data/**"
)

for SRC in "${BACKUP_SRC[@]}"; do
  echo "Backing up $SRC ..."
  rsync -avz --delete "${EXCLUDES[@]}" "$SRC" "$BACKUP_DEST" | tee -a "$LOGFILE"
done

echo "Backup complete. Log: $LOGFILE"
