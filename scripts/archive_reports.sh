#!/usr/bin/env bash
set -euo pipefail

STAMP=$(date +%F)               # YYYY-MM-DD
OUTDIR="data/reports/history"
mkdir -p "$OUTDIR"

copy_if_exists () {
  local src="data/reports/$1"
  local dest="$2"
  if [ -f "$src" ]; then
    cp "$src" "$dest"
    echo "→ $src  =>  $dest"
  fi
}

# "latest" -> gedateerde kopieën
copy_if_exists "scores_latest.csv"   "$OUTDIR/scores_${STAMP}.csv"
copy_if_exists "scores_latest.json"  "$OUTDIR/scores_${STAMP}.json"
copy_if_exists "top5_latest.md"      "$OUTDIR/top5_${STAMP}.md"

# optioneel: oude bestandsnamen meenemen als je die nog hebt
copy_if_exists "latest.csv"          "$OUTDIR/${STAMP}_latest.csv"
copy_if_exists "latest.json"         "$OUTDIR/${STAMP}_latest.json"

echo "Archived to: $OUTDIR"
