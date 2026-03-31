#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")
HEADERS = {"User-Agent": UA}

ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&count=200&output=atom"


def fetch_atom():
    r = requests.get(ATOM_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_atom(xml):
    soup = BeautifulSoup(xml, "xml")
    entries = soup.find_all("entry")
    out = []

    for e in entries:
        link = e.find("link")["href"]
        updated = e.find("updated").text[:10]
        out.append((link, updated))

    return out


def fetch_xml(index_url):
    try:
        r = requests.get(index_url, headers=HEADERS, timeout=30)
        html = r.text

        matches = re.findall(r'href="([^"]+\.xml)"', html)
        for m in matches:
            if "form4" in m.lower() or "ownership" in m.lower() or "primary" in m.lower():
                xml_url = m if m.startswith("http") else "https://www.sec.gov" + m
                xr = requests.get(xml_url, headers=HEADERS, timeout=30)
                return xr.text
    except requests.RequestException as e:
        print(f"[warn] fetch_xml fout voor {index_url}: {e}", file=sys.stderr)
        return None

    return None


def extract_transactions(xml):
    txs = []

    blocks = re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml, re.S)

    for b in blocks:
        code = re.search(r"<transactionCode>(.*?)</transactionCode>", b)
        shares = re.search(r"<transactionShares>.*?<value>(.*?)</value>", b, re.S)
        price = re.search(r"<transactionPricePerShare>.*?<value>(.*?)</value>", b, re.S)

        if not code:
            continue

        code = code.group(1).strip()

        try:
            shares = float(shares.group(1)) if shares else 0
            price = float(price.group(1)) if price else 0
        except (ValueError, TypeError):
            continue

        total = shares * price

        txs.append({
            "code": code,
            "total": total
        })

    return txs


def extract_meta(xml):
    def g(tag):
        m = re.search(fr"<{tag}>(.*?)</{tag}>", xml)
        return m.group(1).strip() if m else ""

    return {
        "ticker": g("issuerTradingSymbol"),
        "issuer": g("issuerName"),
        "owner": g("rptOwnerName"),
        "role": g("officerTitle"),
        "isDirector": g("isDirector"),
        "isOfficer": g("isOfficer"),
        "isTenPercentOwner": g("isTenPercentOwner"),
    }


# IPO-detectie: bekende recente IPOs worden overgeslagen
# We houden een simpele cache bij per ticker via SEC EDGAR
IPO_CACHE = {}
IPO_MIN_LISTING_DAYS = 365


def is_recent_ipo(ticker: str) -> bool:
    """Check of ticker een recente IPO is (<1 jaar genoteerd) via SEC EDGAR."""
    if not ticker:
        return False
    if ticker in IPO_CACHE:
        return IPO_CACHE[ticker]

    try:
        # Zoek eerste filing datum via SEC full-text search
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2000-01-01&enddt=2020-01-01&forms=4"
        r = requests.get(url, headers=HEADERS, timeout=10)
        # Als er oude Form 4s zijn (voor 2020), is het geen recente IPO
        has_old_filings = r.status_code == 200 and '"total"' in r.text and '"hits"' in r.text
        IPO_CACHE[ticker] = not has_old_filings
        return not has_old_filings
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="SEC Form 4 open market discovery (P > $100k)")
    parser.add_argument("--output-dir", default="data/reports", help="directory voor JSON/CSV output")
    args = parser.parse_args()

    atom = fetch_atom()
    entries = parse_atom(atom)

    results = []

    for link, date in entries:
        xml = fetch_xml(link)
        if not xml:
            continue

        meta = extract_meta(xml)
        txs = extract_transactions(xml)

        for t in txs:
            if t["code"] == "P" and t["total"] >= 100000:
                ticker = meta["ticker"]

                # Filter: skip recente IPOs (< 1 jaar genoteerd)
                if is_recent_ipo(ticker):
                    print(f"[skip] {ticker} — recente IPO, geen informatief signaal", file=sys.stderr)
                    continue

                # Filter: skip pure 10% owners zonder officer/director rol
                if (meta.get("isTenPercentOwner") == "1"
                    and meta.get("isDirector") != "1"
                    and meta.get("isOfficer") != "1"
                    and not meta["role"]):
                    print(f"[skip] {ticker} — alleen 10% owner ({meta['owner']}), lagere informatiewaarde", file=sys.stderr)
                    continue

                results.append({
                    "ticker": ticker,
                    "issuer": meta["issuer"],
                    "date": date,
                    "insider": meta["owner"],
                    "role": meta["role"],
                    "code": t["code"],
                    "notional": t["total"]
                })

        time.sleep(0.2)

    # Stdout output (backward compatible)
    print(f"{'ticker':<8} {'issuer':<30} {'date':<12} {'insider':<30} {'role':<25} {'code':<5} {'notional':<10}")
    print("-"*120)

    for r in results:
        print(f"{r['ticker']:<8} {r['issuer'][:28]:<30} {r['date']:<12} {r['insider'][:28]:<30} {r['role'][:23]:<25} {r['code']:<5} ${r['notional']/1000:.0f}k")

    # Structured file output
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    json_path = outdir / "discovery_openmarket.json"
    json_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    csv_path = outdir / "discovery_openmarket.csv"
    if results:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)

    print(f"\n[info] {len(results)} resultaten geschreven naar {json_path} en {csv_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
