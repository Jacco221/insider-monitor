#!/usr/bin/env python3
from __future__ import annotations
import json, sys, random, datetime as dt
from pathlib import Path
from typing import Dict, Any

SIG_DIR = Path("data/signals")
OUT_DIR = Path("data/news")
LOG_DIR = Path("data/logs")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

def latest_signals_path() -> Path | None:
    if not SIG_DIR.exists(): return None
    c = sorted(SIG_DIR.glob("*.json"))
    return c[-1] if c else None

def load_json(p: Path) -> Dict[str, Any]: return json.loads(p.read_text(encoding="utf-8"))

def fake_headlines(ticker: str) -> list[str]:
    bank = [f"{ticker}: analist verhoogt koersdoel", f"{ticker}: sectorbericht – mogelijke vraagimpuls", f"{ticker}: management geeft positieve vooruitblik", f"{ticker}: volumepiek in handelsdata"]
    k = random.choice([0,1,2]); random.shuffle(bank); return bank[:k]

def main() -> int:
    src = latest_signals_path()
    if not src: print("GEEN signals bestand; sla news over."); return 0
    signals = load_json(src).get("signals", [])
    news_map = { (s.get("ticker") or "").upper(): fake_headlines((s.get("ticker") or "").upper()) for s in signals if s.get("ticker") }
    payload = {"news_at": dt.datetime.utcnow().isoformat(timespec="seconds")+"Z", "tickers": news_map, "source": str(src)}
    out_path = OUT_DIR / f"{dt.date.today().isoformat()}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (LOG_DIR / "fetch_news.log").write_text(f"[{payload['news_at']}] headlines for {len(news_map)} tickers -> {out_path}\n", encoding="utf-8")
    print(f"OK fetch_news: {len(news_map)} tickers -> {out_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
