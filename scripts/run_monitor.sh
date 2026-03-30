#!/bin/bash
# Insider Monitor — Dagelijkse portfolio scan + discovery
# Draai 2x per dag: 09:00 en 18:00 (crontab)
#
# Installatie:
#   crontab -e
#   0 9,18 * * 1-5 /Users/jaccoalbers/insider-monitor/scripts/run_monitor.sh
#
# Vereiste env vars (zet in ~/.zshrc of ~/.bash_profile):
#   export SEC_USER_AGENT="Jacco Albers (contact: jaccoalbers@hotmail.com)"
#   export TELEGRAM_BOT_TOKEN="123456:ABC..."
#   export TELEGRAM_CHAT_ID="987654321"

set -e

cd /Users/jaccoalbers/insider-monitor

# Laad env vars
source ~/.zshrc 2>/dev/null || source ~/.bash_profile 2>/dev/null || true

export SEC_USER_AGENT="${SEC_USER_AGENT:-Jacco Albers (contact: jaccoalbers@hotmail.com)}"

PYTHON=python3
LOGDIR=data/reports
mkdir -p "$LOGDIR"

echo "=== Insider Monitor Run: $(date) ==="

# Stap 1: Discovery (nieuwe Form 4 filings)
echo "[1/3] Discovery scan..."
$PYTHON scripts/discovery_3bd_openmarket_ps100k.py --output-dir "$LOGDIR" 2>&1 | tail -5

# Stap 2: Deep dive op portfolio + watchlist
echo "[2/3] Deep dive portfolio tickers..."
PORTFOLIO="ALMS BH BORR GO"
WATCHLIST="HTGC LOAR"

$PYTHON scripts/portfolio_deepdive_270d.py \
  --tickers $PORTFOLIO $WATCHLIST \
  --days 270 --count 80 --output-dir "$LOGDIR" 2>&1 | tail -20

# Stap 3: Portfolio monitor met exit-signalen
echo "[3/3] Portfolio monitor..."
$PYTHON scripts/portfolio_monitor.py \
  --tickers $PORTFOLIO $WATCHLIST \
  --days 270 \
  --output-dir "$LOGDIR" \
  --telegram 2>&1

echo "=== Done: $(date) ==="
