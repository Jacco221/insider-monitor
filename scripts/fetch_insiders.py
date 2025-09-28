#!/usr/bin/env python3
"""
Fetch recente Form 4 (insider) filings vanaf:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=100&owner=only
We pakken per rij: company, CIK, datum/tijd en de filing-URL.
Schrijven naar: data/insider-monitor/<timestamp>/signals.json
"""

from __future__ import annotations
import os, json, datetime as dt
from pathlib import Path
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

BASE = Path("data") / "insider-monitor"
SEC_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
HEADERS = {
    "User-Agent": os.getenv("SEC_USER_AGENT", "insider-monitor/1.0 (contact: you@example.com)")
}

def ts() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def fetch_current_form4() -> List[Dict[str, Any]]:
    params = {
        "action": "getcurrent",
        "type": "4",
        "count": "100",
        "owner": "only"
    }
    r = requests.get(SEC_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.select_one("table.tableFile2")
    if not table:
        return []

    rows = table.find_all("tr")
    out: List[Dict[str, Any]] = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        form_txt = tds[0].get_text(strip=True)
        if not form_txt.startswith("4"):
            continue

        a = tr.find("a", href=True)
        filing_url = ("https://www.sec.gov" + a["href"]) if a else None

        out.append({
            "type": "insider",
            "form": form_txt,
            "company": tds[1].get_text(" ", strip=True),
            "cik": tds[2].get_text(" ", strip=True),
            "ref_date": tds[3].get_text(" ", strip=True),
            "filing_url": filing_url,
            "who": "Form 4 filer",
            "ticker": None,
            "score": 1
        })
    return out

def main() -> int:
    signals = fetch_current_form4()
    stamp = ts()
    out_dir = BASE / stamp
    ensure_dir(out_dir)

    payload = {
        "meta": {
            "source": "sec-current-form4",
            "generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "count": len(signals),
        },
        "signals": signals,
        "tickers": {}
    }

    (out_dir / "signals.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"OK: {len(signals)} Form 4 items → {out_dir/'signals.json'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
