#!/usr/bin/env python3
from __future__ import annotations
import json, sys, datetime as dt
from pathlib import Path
from typing import Dict, Any, List

SIG_DIR = Path("data/signals")
NEWS_DIR = Path("data/news")
REP_DIR = Path("data/reports")
LOG_DIR = Path("data/logs")
REP_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

def latest(path: Path) -> Path | None:
    if not path.exists(): return None
    c = sorted(path.glob("*.json"))
    return c[-1] if c else None

def load_json(path: Path | None) -> Dict[str, Any]:
    if not path or not path.exists(): return {}
    return json.loads(path.read_text(encoding="utf-8"))

def build_summary(signals: List[Dict[str, Any]], news_map: Dict[str, List[str]]) -> str:
    if not signals: return "Geen nieuwe insider-signalen."
    lines = ["📣 Insider Monitor – nieuwe signalen"]
    for s in signals[:6]:
        t = (s.get("ticker") or "").upper(); score = s.get("score"); who = s.get("insider") or "insider"; typ = s.get("type") or "BUY"; ref = s.get("ref_date") or "—"
        lines.append(f"• {t} ({typ}) score {score} – {who}, {ref}")
        heads = news_map.get(t) or []
        if heads: lines.append(f"    📰 {heads[0]}")
    if len(signals) > 6: lines.append(f"… en {len(signals) - 6} meer.")
    lines.append("—"); lines.append("⚠️ Demo-tekst – vervang met jouw format/regles.")
    return "\n".join(lines)

def main() -> int:
    sig_path = latest(SIG_DIR); news_path = latest(NEWS_DIR)
    signals = load_json(sig_path).get("signals", []); news_map = (load_json(news_path).get("tickers", {}) if news_path else {})
    summary = build_summary(signals, news_map)
    out_path = REP_DIR / "summary.txt"; out_path.write_text(summary, encoding="utf-8")
    ts = dt.datetime.utcnow().isoformat(timespec="seconds")+"Z"
    (LOG_DIR / "report_builder.log").write_text(f"[{ts}] wrote summary ({len(summary)} chars) -> {out_path}\n", encoding="utf-8")
    print("SUMMARY:" + summary.replace("\n", "\\n"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
