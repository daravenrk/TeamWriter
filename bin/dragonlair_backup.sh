#!/bin/bash
# dragonlair_backup.sh
# Backup critical system files, configs, models, and scripts to remote server

set -euo pipefail

BACKUP_SRC=(
  "/opt/ai-stack/"
  "/home/daravenrk/dragonlair/"
  "/ai/ollama-amd/"
  "/ai/ollama-nvidia/"
)
BACKUP_DEST="daravenrk@192.168.86.34:/backups/dragonlair"
LOGFILE="/tmp/dragonlair_backup.log"

# Create a compressed archive and send via rsync over SSH
for SRC in "${BACKUP_SRC[@]}"; do
  echo "Backing up $SRC ..."
  rsync -avz --delete "$SRC" "$BACKUP_DEST" | tee -a "$LOGFILE"
done

echo "Backup complete. Log: $LOGFILE"
