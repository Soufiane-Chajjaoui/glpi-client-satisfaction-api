#!/bin/bash
# Daily GLPI Light pipeline — runs Bronze → Silver → Gold → Critical → Alerts
# Schedule via cron: 0 2 * * * /path/to/glpi-light/cron/pipeline_daily.sh

cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/pipeline_$(date +\%Y\%m\%d_\%H\%M\%S).log"

echo "=== GLPI Light Pipeline $(date) ===" >> "$LOG_FILE"

python pipeline.py --all >> "$LOG_FILE" 2>&1

echo "=== Done $(date) ===" >> "$LOG_FILE"
