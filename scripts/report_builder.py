#!/usr/bin/env python3
"""
Report Builder: combineert insider signalen met nieuws en bouwt een samenvatting.
"""

import json
import datetime as dt
from pathlib import Path

BASE = Path("data") / "insider-monitor"
REP_DIR = BASE / "reports"

def ensure_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)

def latest(subdir: Path, fname: str) -> Path | None:
    """Vind laatste bestand in subdir."""
    if not subdir.exists():
        return None
    files = sorted(subdir.glob(fname), reverse=True)
    return files[0] if files else None

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def build_summary(signals: list[dict], news: list[dict]) -> str:
    lines = []
    for s in signals[:5]:  # max 5 signalen
        who = s.get("reporting_owner", "Onbekend")
        score = s.get("score", "?")
        ref = s.get("ref_date", "-")
        lines.append(f"Insider: {who}, Score: {score}, Ref: {ref}")
    if not signals:
        lines.append("Geen insider signalen gevonden.")

    if news:
        lines.append("\nRecent nieuws:")
        for n in news[:3]:  # max 3 nieuwsberichten
            lines.append(f"- {n.get('headline')} ({n.get('ticker', '-')})")
    else:
        lines.append("\nGeen nieuws gevonden.")

    return "\n".join(lines)

def main() -> int:
    # paden
    sig_file = latest(BASE, "*/scored.json")
    news_file = latest(BASE, "*/news.json")

    signals = load_json(sig_file).get("signals", []) if sig_file else []
    news = load_json(news_file).get("news", []) if news_file else []

    summary = build_summary(signals, news)

    # opslaan
    ensure_dir(REP_DIR)
    ts = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out_file = REP_DIR / f"summary_{ts}.txt"
    out_file.write_text(summary, encoding="utf-8")

    print("RAPPORT GEBOUWD:\n", summary)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
