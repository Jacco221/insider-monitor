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

set -eo pipefail
# Stap-fouten loggen maar niet de hele pipeline stoppen
trap 'echo "[ERROR] Stap mislukt op regel $LINENO — pipeline gaat door" >&2' ERR
set +e

cd /Users/jaccoalbers/insider-monitor

# Laad credentials uit beveiligd .env bestand
source /Users/jaccoalbers/insider-monitor/.env 2>/dev/null || true

# Gebruik Python 3.12 venv
source .venv/bin/activate

export SEC_USER_AGENT="${SEC_USER_AGENT:-Jacco Albers (contact: jaccoalbers@hotmail.com)}"

LOGDIR=data/reports
mkdir -p "$LOGDIR"

echo "=== Insider Monitor Run: $(date) ==="

# === Pre-run zelftest: SEC Archive bereikbaar? ===
SEC_STATUS=$(python3 -c "
import urllib.request, os
UA = os.getenv('SEC_USER_AGENT', 'InsiderMonitor/1.0')
try:
    req = urllib.request.Request(
        'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&dateb=&owner=include&count=1&search_text=',
        headers={'User-Agent': UA}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        print('OK' if r.status == 200 else f'HTTP_{r.status}')
except Exception as e:
    code = getattr(getattr(e, 'code', None), '__str__', lambda: str(e))()
    print(f'FAIL_{code}')
" 2>/dev/null)

echo "[self-test] SEC status: $SEC_STATUS"
if [[ "$SEC_STATUS" != "OK" ]]; then
    python3 -c "
import os, urllib.request, urllib.parse
token = os.getenv('TELEGRAM_BOT_TOKEN','')
chat  = os.getenv('TELEGRAM_CHAT_ID','')
if token and chat:
    msg = '⚠️ <b>Insider Monitor zelftest mislukt</b>\nSEC niet bereikbaar: $SEC_STATUS\nDiscovery overgeslagen — pipeline draait wel door.'
    data = urllib.parse.urlencode({'chat_id': chat, 'text': msg, 'parse_mode': 'HTML'}).encode()
    urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data), timeout=10)
" 2>/dev/null
fi

# === Stap 1: Discovery (nieuwe Form 4 filings) ===
# Alleen draaien om 09:00 — 18:00 run hergebruikt cache om SEC rate limit te sparen
CURRENT_HOUR=$(date +%H)
echo "[1/4] Discovery scan..."
if [ "$CURRENT_HOUR" -lt 12 ]; then
    # Ochtendrun: verse discovery
    ( python3 scripts/discovery_3bd_openmarket_ps100k.py --output-dir "$LOGDIR" 2>&1 | tail -5 ) &
    DISC_PID=$!
    ( sleep 1500 && kill $DISC_PID 2>/dev/null && echo "[1/4] Discovery timeout — doorgaan zonder nieuwe kandidaten" ) &
    TIMEOUT_PID=$!
    wait $DISC_PID 2>/dev/null
    kill $TIMEOUT_PID 2>/dev/null || true
else
    # Avondrun: gebruik cache van ochtend
    echo "[1/4] Avondrun — discovery cache van ochtend hergebruikt (SEC rate limit sparen)"
fi

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
PORTFOLIO="BH BORR IPX SBSW"

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
# Timeout na 5 minuten — deep dive mag portfolio monitor niet blokkeren
( python3 scripts/portfolio_deepdive_270d.py \
  --tickers $ALL_TICKERS \
  --days 270 --count 80 --output-dir "$LOGDIR" 2>&1 | tail -20 ) &
DIVE_PID=$!
( sleep 300 && kill $DIVE_PID 2>/dev/null && echo "[2/4] Deep dive timeout — doorgaan" ) &
TIMEOUT2_PID=$!
wait $DIVE_PID 2>/dev/null
kill $TIMEOUT2_PID 2>/dev/null || true

# === Stap 3: Portfolio monitor met exit-signalen ===
echo "[3/4] Portfolio monitor..."
python3 scripts/portfolio_monitor.py \
  --tickers $ALL_TICKERS \
  --portfolio $PORTFOLIO \
  --days 270 \
  --output-dir "$LOGDIR" \
  --telegram 2>&1

# === Stap 3b: Kandidaat research op top signalen ===
echo "[3b/4] Kandidaat research op STERKE OVERTUIGING signalen..."
python3 scripts/candidate_research.py \
  --monitor-json "$LOGDIR/portfolio_monitor.json" \
  --portfolio $PORTFOLIO \
  --telegram 2>&1

# === Stap 3c: Prijsalerts checken ===
echo "[3c/4] Prijsalerts checken..."
python3 scripts/price_alert.py check --telegram 2>&1 || echo "[3c] Prijsalert fout — doorgaan"

# === Stap 4: Trade advies (dry-run, wacht op Telegram bevestiging) ===
echo "[4/4] Trade advies genereren..."
python3 scripts/auto_trade.py --dry-run --max-order 500 --max-daily 2000 2>&1

echo ""
echo "=== Done: $(date) ==="
echo "Tickers geanalyseerd: $ALL_TICKERS"
