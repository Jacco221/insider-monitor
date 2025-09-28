#!/usr/bin/env python3
"""
Fetch nieuwsartikelen (dummy versie).
Voor demo: we maken een fake nieuwsbestand.
"""

import datetime as dt
import json
from pathlib import Path

BASE = Path("data") / "insider-monitor"

def ensure_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)

def main() -> int:
    stamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out_dir = BASE / stamp
    ensure_dir(out_dir)

    news = [
        {"headline": "Insider trading investigation launched", "ticker": "AAPL"},
        {"headline": "Executive buys company stock", "ticker": "TSLA"},
    ]

    payload = {
        "meta": {
            "source": "dummy-news",
            "generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "count": len(news),
        },
        "news": news,
    }

    out_file = out_dir / "news.json"
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"OK: {len(news)} news items → {out_file}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
