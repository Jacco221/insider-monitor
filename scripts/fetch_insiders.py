#!/usr/bin/env python3
from __future__ import annotations
import json, os, sys, datetime as dt
from pathlib import Path
from typing import List, Dict, Any

DATA_DIR = Path("data/insiders")
LOG_DIR = Path("data/logs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

def now_utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def sample_insiders() -> List[Dict[str, Any]]:
    today = dt.date.today().isoformat()
    return [
        {"date": today, "ticker": "ACME", "company": "Acme Corp", "insider": "Jane Doe (CEO)", "type": "BUY", "shares": 12000, "price": 18.45, "source": "demo"},
        {"date": today, "ticker": "MOON", "company": "Moonshot Ltd", "insider": "John Smith (CFO)", "type": "BUY", "shares": 3500, "price": 6.10, "source": "demo"},
    ]

def main() -> int:
    payload = {"generated_at": now_utc_iso(), "records": sample_insiders(), "note": "SKELETON – vervang door echte dataverzameling"}
    out_path = DATA_DIR / f"{dt.date.today().isoformat()}.json"
    with out_path.open("w", encoding="utf-8") as f: json.dump(payload, f, ensure_ascii=False, indent=2)
    (LOG_DIR / "fetch_insiders.log").write_text(f"[{now_utc_iso()}] fetched {len(payload['records'])} records -> {out_path}\n", encoding="utf-8")
    print(f"OK fetch_insiders: {len(payload['records'])} -> {out_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
