#!/bin/bash
# Setup local cron for auto-triage. Run: bash setup_cron.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/venv/bin/python"
CRON_TRIAGE="*/15 * * * * cd \"$DIR\" && \"$PY\" triage.py >> triage.cron.log 2>&1"
CRON_DIGEST="0 8 * * * cd \"$DIR\" && \"$PY\" digest.py --html >> digest.cron.log 2>&1"

# Install if not already present
(crontab -l 2>/dev/null | grep -q "triage.py") && echo "triage cron already installed" || (crontab -l 2>/dev/null; echo "$CRON_TRIAGE") | crontab - && echo "Added triage every 15m"
(crontab -l 2>/dev/null | grep -q "digest.py") && echo "digest cron already installed" || (crontab -l 2>/dev/null; echo "$CRON_DIGEST") | crontab - && echo "Added digest daily 8am"

echo "Current crontab:"
crontab -l | grep -E "triage|digest"
echo ""
echo "Logs: tail -f $DIR/triage.cron.log  |  tail -f $DIR/triage.log.jsonl"
echo "Undo: $PY triage.py --undo --hours 24  |  Dry-run: $PY triage.py --dry-run"
