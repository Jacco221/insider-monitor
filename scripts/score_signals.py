#!/usr/bin/env python3
"""
Score signals afkomstig uit fetch_insiders.py
Voor demo: insiders krijgen score 10.
"""

from __future__ import annotations
import os, json, datetime as dt
from pathlib import Path

BASE = Path("data") / "insider-monitor"

def latest_signals() -> Path:
    """Zoek de meest recente signals.json"""
    dirs = sorted(BASE.glob("*"), reverse=True)
    for d in dirs:
        f = d / "signals.json"
        if f.exists():
            return f
    raise FileNotFoundError("Geen signals.json gevonden. Run eerst fetch_insiders.py")

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def score(signals: list[dict]) -> list[dict]:
    for s in signals:
        if s.get("type") == "insider":
            s["score"] = 10
    return signals

def main() -> int:
    in_file = latest_signals()
    data = load_json(in_file)
    signals = data.get("signals", [])
    signals = score(signals)

    out = {
        "meta": {
            "source": "score_signals",
            "generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "count": len(signals),
        },
        "signals": signals,
        "tickers": data.get("tickers", {})
    }

    out_file = in_file.parent / "scored.json"
    save_json(out_file, out)
    print(f"OK: {len(signals)} signals scored → {out_file}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
