#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Haal recente Form 4 (insider transactions) uit de SEC Atom feed en schrijf
insider 'signals' naar data/insider-monitor/<timestamp>/signals.json
"""

import os, re, json, datetime as dt
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE = Path("data/insider-monitor")
BASE.mkdir(parents=True, exist_ok=True)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def ts() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")

def user_agent() -> str:
    return os.getenv("SEC_USER_AGENT", "insider-monitor (contact: example@example.com)")

def fetch_atom(max_count: int = 100) -> str:
    url = ("https://www.sec.gov/cgi-bin/browse-edgar"
           "?action=getcurrent&type=4&owner=only&count=%d&output=atom") % max_count
    headers = {
        "User-Agent": user_agent(),
        "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text

def parse_atom(xml_text: str):
    soup = BeautifulSoup(xml_text, "xml")
    entries = soup.find_all("entry")
    signals = []

    for e in entries:
        updated = (e.find("updated").text or "").strip() if e.find("updated") else ""
        link = e.find("link")
        href = link["href"].strip() if link and link.has_attr("href") else ""
        title = (e.find("title").text or "").strip() if e.find("title") else ""
        raw = e.find("content").text if e.find("content") else ""

        def rx(tag):
            m = re.search(fr"<{tag}>(.*?)</{tag}>", raw or "", re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else ""

        filing_href = rx("filing-href") or href
        company_name = rx("company-name") or title
        reporting_owner = rx("reporting-owner")
        filing_date = rx("filing-date") or updated[:10]

        s = {
            "type": "insider",
            "who": reporting_owner or "Unknown",
            "company": company_name or "Unknown",
            "ref_date": filing_date,
            "ref": filing_href or href,
            "raw_title": title,
            "score": 1,
        }
        signals.append(s)

    return signals

def main() -> int:
    xml = fetch_atom(max_count=100)
    signals = parse_atom(xml)

    out_dir = BASE / ts()
    ensure_dir(out_dir)
    payload = {
        "meta": {
            "source": "sec-atom-form4",
            "generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "count": len(signals),
        },
        "signals": signals,
        "tickers": {},
    }
    out = out_dir / "signals.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"OK: {len(signals)} Form 4 items -> {out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
