#!/bin/bash
# Insider Monitor — Dagelijkse portfolio scan + discovery + deep dive
# Draai 2x per dag: 09:00 en 18:00 (crontab)
#
# Flow:
#   1. Discovery: scan SEC voor nieuwe insider buys (>$100k, afgelopen 3 dagen)
#   2. Deep dive: 270 dagen analyse op portfolio + watchlist + nieuwe discovery kandidaten
#   3. Monitor: EXIT / HOLD / STRONG_HOLD signalen genereren
#   4. Telegram: advies versturen, wacht op bevestiging
#
# Installatie:
#   crontab -e
#   0 9,18 * * 1-5 /Users/jaccoalbers/insider-monitor/scripts/run_monitor.sh

set -e

cd /Users/jaccoalbers/insider-monitor

# Laad credentials uit beveiligd .env bestand
source /Users/jaccoalbers/insider-monitor/.env 2>/dev/null || true

# Gebruik Python 3.12 venv
source .venv/bin/activate

export SEC_USER_AGENT="${SEC_USER_AGENT:-Jacco Albers (contact: jaccoalbers@hotmail.com)}"

LOGDIR=data/reports
mkdir -p "$LOGDIR"

echo "=== Insider Monitor Run: $(date) ==="

# === Stap 1: Discovery (nieuwe Form 4 filings) ===
echo "[1/4] Discovery scan..."
python3 scripts/discovery_3bd_openmarket_ps100k.py --output-dir "$LOGDIR" 2>&1 | tail -5

# Extraheer nieuwe tickers uit discovery resultaten
DISCOVERY_TICKERS=""
if [ -f "$LOGDIR/discovery_openmarket.json" ]; then
    DISCOVERY_TICKERS=$(python3 -c "
import json
data = json.load(open('$LOGDIR/discovery_openmarket.json'))
tickers = sorted(set(r['ticker'] for r in data if r.get('ticker')))
print(' '.join(tickers))
" 2>/dev/null)
fi

# === Portfolio en watchlist tickers ===
PORTFOLIO="BH BORR GO HTGC LOAR"

# Combineer: portfolio + discovery kandidaten (zonder duplicaten)
ALL_TICKERS=$(python3 -c "
portfolio = '$PORTFOLIO'.split()
discovery = '$DISCOVERY_TICKERS'.split()
combined = sorted(set(portfolio + [t for t in discovery if t]))
print(' '.join(combined))
" 2>/dev/null)

echo "Portfolio: $PORTFOLIO"
echo "Discovery: ${DISCOVERY_TICKERS:-geen nieuwe kandidaten}"
echo "Totaal analyse: $ALL_TICKERS"
echo ""

# === Stap 2: Deep dive 270 dagen op ALLES ===
echo "[2/4] Deep dive 270d op $ALL_TICKERS..."
python3 scripts/portfolio_deepdive_270d.py \
  --tickers $ALL_TICKERS \
  --days 270 --count 80 --output-dir "$LOGDIR" 2>&1 | tail -20

# === Stap 3: Portfolio monitor met exit-signalen ===
echo "[3/4] Portfolio monitor..."
python3 scripts/portfolio_monitor.py \
  --tickers $ALL_TICKERS \
  --days 270 \
  --output-dir "$LOGDIR" \
  --telegram 2>&1

# === Stap 4: Trade advies (dry-run, wacht op Telegram bevestiging) ===
echo "[4/4] Trade advies genereren..."
python3 scripts/auto_trade.py --dry-run --max-order 500 --max-daily 2000 2>&1

echo ""
echo "=== Done: $(date) ==="
echo "Tickers geanalyseerd: $ALL_TICKERS"
