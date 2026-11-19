#!/usr/bin/env bash
# Refresh the Retraction Watch data and rebuild the integrity store.
# Cron example (06:30 every day):
#   30 6 * * * /path/to/Rag_Project/scripts/daily_sync.sh >> /path/to/Rag_Project/data/sync.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
echo "[$(date -Iseconds)] sync start"
uv run --quiet tainttrace sync
uv run --quiet tainttrace info
echo "[$(date -Iseconds)] sync done"
