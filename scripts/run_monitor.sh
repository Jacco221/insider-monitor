#!/bin/bash
# Insider Monitor — dagelijkse pipeline
# Draait 2× per dag via GitHub Actions (09:00 en 18:00 CEST)
#
# Stap 1: monitor.py  — discovery + analyse + Telegram hoofdbericht
# Stap 2: candidate_research.py — diepgaande analyse op top-kandidaten

set -euo pipefail

cd "$(dirname "$0")/.."

# Laad credentials (.env lokaal, GitHub Secrets in Actions)
source .env 2>/dev/null || true

# Python venv (lokaal); in GitHub Actions is Python direct beschikbaar
[ -f .venv/bin/activate ] && source .venv/bin/activate || true

export SEC_USER_AGENT="${SEC_USER_AGENT:-InsiderMonitor/2.0 (contact: you@example.com)}"

LOGDIR="data/reports"
mkdir -p "$LOGDIR"

PORTFOLIO="BH NKE IPX SBSW"

echo "=== Insider Monitor: $(date) ==="
echo "Portefeuille: $PORTFOLIO"

# ── Stap 1: Discovery + analyse + Telegram ───────────────────────────────────
echo "[1/2] Monitor..."
python3 scripts/monitor.py \
  --portfolio $PORTFOLIO \
  --output-dir "$LOGDIR" \
  --telegram

# ── Stap 2: Diepgaande kandidaat research ────────────────────────────────────
echo "[2/2] Kandidaat research..."
python3 scripts/candidate_research.py \
  --monitor-json "$LOGDIR/monitor.json" \
  --portfolio $PORTFOLIO \
  --telegram

echo "=== Klaar: $(date) ==="
