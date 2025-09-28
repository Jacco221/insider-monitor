#!/usr/bin/env python3
from __future__ import annotations
import json, sys, datetime as dt
from pathlib import Path
from typing import Dict, Any, List

INS_DIR = Path("data/insiders")
OUT_DIR = Path("data/signals")
LOG_DIR = Path("data/logs")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

def latest_insiders_path() -> Path | None:
    if not INS_DIR.exists(): return None
    c = sorted(INS_DIR.glob("*.json"))
    return c[-1] if c else None

def load_json(p: Path) -> Dict[str, Any]: return json.loads(p.read_text(encoding="utf-8"))

def score_record(rec: Dict[str, Any]) -> float:
    if rec.get("type") == "BUY":
        try:
            from math import sqrt
            return round(sqrt(max(0, float(rec.get("shares", 0)) / 1000.0)), 2)
        except Exception: return 0.0
    return 0.0

def main() -> int:
    src = latest_insiders_path()
    if not src: print("GEEN insiders bronbestand gevonden."); return 0
    data = load_json(src)
    recs: List[Dict[str, Any]] = data.get("records", [])
    signals = [{"ticker": r.get("ticker"), "company": r.get("company"), "insider": r.get("insider"), "type": r.get("type"), "score": score_record(r), "ref_date": r.get("date")} for r in recs if score_record(r) > 0]
    out = {"scored_at": dt.datetime.utcnow().isoformat(timespec="seconds")+"Z", "count": len(signals), "signals": signals, "source": str(src)}
    out_path = OUT_DIR / f"{dt.date.today().isoformat()}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (LOG_DIR / "score_signals.log").write_text(f"[{out['scored_at']}] scored {out['count']} -> {out_path}\n", encoding="utf-8")
    print(f"OK score_signals: {out['count']} -> {out_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
