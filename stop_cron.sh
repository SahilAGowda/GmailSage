#!/bin/bash
# Stop auto-triage cron. Run: bash stop_cron.sh [--all]
set -e
MODE="${1:---triage}"  # --triage (default) or --all

if [[ "$MODE" == "--all" ]]; then
  crontab -l 2>/dev/null | grep -vE "triage\.py|digest\.py" | crontab - && echo "Stopped triage + digest cron"
else
  crontab -l 2>/dev/null | grep -v "triage\.py" | crontab - && echo "Stopped triage cron (digest still active)"
fi

echo "Remaining crontab:"
crontab -l 2>/dev/null | grep -E "triage|digest" || echo "(none - cron stopped)"
echo ""
echo "Restart: bash \"$(cd "$(dirname "$0")" && pwd)/setup_cron.sh\""
