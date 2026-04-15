#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SEC Form 4 open market discovery — EFTS API versie
Gebruikt efts.sec.gov (aanbevolen SEC API) i.p.v. full-index download.
Voordelen:
  - Geen rate-limit problemen op full-index endpoint
  - Direct XML URL uit response — geen index-pagina fetch nodig
  - 50% minder HTTP requests per filing
  - Parallel verwerking met threading
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

UA = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

NON_SIGNAL_CODES = {"M", "C", "A", "D", "G", "L", "W", "Z", "J", "K"}
MIN_BUY_USD  = 100_000
MAX_WORKERS  = 3    # 3 workers × ~0.4s per request = ~7.5 req/sec, ruim binnen SEC limiet van 10/sec
REQUEST_DELAY = 0.4   # seconden tussen requests per worker


# ── EFTS filings ophalen ──────────────────────────────────────────────────────

def get_filings_efts(days_back: int = 3) -> list[dict]:
    """Haal Form 4 filings op via SEC EFTS search API.

    Returns lijst van dicts met: adsh, xml_filename, file_date, cik
    """
    today = date.today()
    start = (today - timedelta(days=days_back)).isoformat()
    end   = today.isoformat()

    filings = []
    page_size = 100
    offset    = 0

    while True:
        url = (
            f"https://efts.sec.gov/LATEST/search-index?forms=4"
            f"&dateRange=custom&startdt={start}&enddt={end}"
            f"&from={offset}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"[warn] EFTS status {r.status_code} — stop paginering", file=sys.stderr)
                break
            data  = r.json()
            hits  = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)

            if not hits:
                break

            for h in hits:
                src  = h.get("_source", {})
                adsh = src.get("adsh") or h.get("_id", "").split(":")[0]
                # XML bestandsnaam zit in _id na ":"
                raw_id   = h.get("_id", "")
                xml_file = raw_id.split(":")[-1] if ":" in raw_id else "form4.xml"
                # CIK: gebruik ciks[0] uit _source — dit is de reporting owner CIK
                # (adsh-prefix is de filing agent CIK, niet de owner — levert 404 op)
                ciks_list = src.get("ciks", [])
                filer_cik = str(int(ciks_list[0])) if ciks_list else (adsh.replace("-", "")[:10].lstrip("0") or "0")
                filings.append({
                    "adsh":     adsh,
                    "xml_file": xml_file,
                    "file_date": src.get("file_date", ""),
                    "cik":      filer_cik,
                })

            offset += len(hits)
            if offset >= total:
                break

        except Exception as e:
            print(f"[warn] EFTS fout: {e}", file=sys.stderr)
            break

    print(f"[info] {len(filings)} Form 4 filings gevonden via EFTS ({start} → {end})", file=sys.stderr)
    return filings


# ── XML ophalen ───────────────────────────────────────────────────────────────

RATE_LIMITED      = threading.Event()
RATE_LIMIT_COUNT  = 0
RATE_LIMIT_LOCK   = threading.Lock()
RATE_LIMIT_THRESHOLD = 20  # Na 20 × 429 → circuit breaker aan (harde stop)
_REQUEST_SEMAPHORE = threading.Semaphore(MAX_WORKERS)
PAUSE_ON_429 = 15  # Seconden pauze bij 429 voordat we doorgaan


def fetch_with_retry(url: str, retries: int = 3, backoff: float = 3.0) -> requests.Response | None:
    """GET met rate limiting + retry bij 429 + circuit breaker.
    Bij 429: pauze en retry (geen harde stop tenzij threshold bereikt).
    """
    global RATE_LIMIT_COUNT
    if RATE_LIMITED.is_set():
        return None

    time.sleep(REQUEST_DELAY)  # Rate limiter: max ~7.5 req/sec totaal

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                # Succesvolle request: reset teller gedeeltelijk
                with RATE_LIMIT_LOCK:
                    if RATE_LIMIT_COUNT > 0:
                        RATE_LIMIT_COUNT = max(0, RATE_LIMIT_COUNT - 1)
                return r
            if r.status_code == 429:
                with RATE_LIMIT_LOCK:
                    RATE_LIMIT_COUNT += 1
                    count = RATE_LIMIT_COUNT
                if count >= RATE_LIMIT_THRESHOLD:
                    RATE_LIMITED.set()
                    print("[warn] Circuit breaker: SEC rate limit actief — discovery gestopt", file=sys.stderr)
                    return None
                # Pauze en daarna retry
                wait = PAUSE_ON_429 * (attempt + 1)
                print(f"[warn] 429 rate limit ({count}/{RATE_LIMIT_THRESHOLD}) — wacht {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            return None
        except requests.RequestException:
            time.sleep(backoff)
    return None


def fetch_xml(filing: dict) -> str | None:
    """Haal Form 4 XML op — URL rechtstreeks van EFTS, geen index-pagina nodig."""
    adsh     = filing["adsh"]
    cik      = filing["cik"]
    xml_file = filing["xml_file"]
    acc_no   = adsh.replace("-", "")

    xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no}/{xml_file}"
    r = fetch_with_retry(xml_url)
    if r and ("ownershipDocument" in r.text or "transactionCode" in r.text):
        return r.text

    # Fallback: probeer alternatieve bestandsnamen
    for alt in ["form4.xml", "primarydocument.xml"]:
        if alt == xml_file:
            continue
        r2 = fetch_with_retry(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no}/{alt}")
        if r2 and "ownershipDocument" in r2.text:
            return r2.text
    return None


# ── XML parsen ────────────────────────────────────────────────────────────────

def extract_transactions(xml: str) -> list[dict]:
    txs = []
    for block in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml, re.S):
        code_m   = re.search(r"<transactionCode>(.*?)</transactionCode>", block)
        shares_m = re.search(r"<transactionShares>.*?<value>(.*?)</value>", block, re.S)
        price_m  = re.search(r"<transactionPricePerShare>.*?<value>(.*?)</value>", block, re.S)
        if not code_m:
            continue
        code = code_m.group(1).strip().upper()
        try:
            shares = float(shares_m.group(1)) if shares_m else 0
            price  = float(price_m.group(1))  if price_m  else 0
        except (ValueError, TypeError):
            continue
        txs.append({"code": code, "total": shares * price})
    return txs


def extract_meta(xml: str) -> dict:
    def g(tag):
        m = re.search(fr"<{tag}>(.*?)</{tag}>", xml)
        return m.group(1).strip() if m else ""
    return {
        "ticker":           g("issuerTradingSymbol"),
        "issuer":           g("issuerName"),
        "owner":            g("rptOwnerName"),
        "role":             g("officerTitle"),
        "isDirector":       g("isDirector"),
        "isOfficer":        g("isOfficer"),
        "isTenPercentOwner": g("isTenPercentOwner"),
    }


# ── IPO filter ────────────────────────────────────────────────────────────────

IPO_CACHE: dict[str, bool] = {}
IPO_CACHE_LOCK = threading.Lock()


def is_recent_ipo(ticker: str) -> bool:
    """True als ticker minder dan 1 jaar geleden genoteerd (recente IPO)."""
    if not ticker:
        return False
    with IPO_CACHE_LOCK:
        if ticker in IPO_CACHE:
            return IPO_CACHE[ticker]
    try:
        url = (f"https://efts.sec.gov/LATEST/search-index?forms=4"
               f"&dateRange=custom&startdt=2000-01-01&enddt=2020-01-01"
               f"&q=%22{ticker}%22&hits.hits._source.size=1")
        r = requests.get(url, headers=HEADERS, timeout=10)
        has_old = r.status_code == 200 and r.json().get("hits", {}).get("total", {}).get("value", 0) > 0
        result  = not has_old
    except Exception:
        result = False
    with IPO_CACHE_LOCK:
        IPO_CACHE[ticker] = result
    return result


# ── Verwerking per filing ─────────────────────────────────────────────────────

def process_filing(filing: dict) -> list[dict]:
    xml = fetch_xml(filing)
    if not xml:
        return []

    meta = extract_meta(xml)
    txs  = extract_transactions(xml)
    found = []

    for t in txs:
        if t["code"] != "P" or t["total"] < MIN_BUY_USD:
            continue

        ticker = meta["ticker"]

        if is_recent_ipo(ticker):
            print(f"[skip] {ticker} — recente IPO, geen informatief signaal", file=sys.stderr)
            continue

        if (meta.get("isTenPercentOwner") == "1"
                and meta.get("isDirector") != "1"
                and meta.get("isOfficer") != "1"
                and not meta["role"]):
            print(f"[skip] {ticker} — alleen 10% owner ({meta['owner']}), lagere informatiewaarde", file=sys.stderr)
            continue

        found.append({
            "ticker":   ticker,
            "issuer":   meta["issuer"],
            "date":     filing["file_date"],
            "insider":  meta["owner"],
            "role":     meta["role"],
            "code":     t["code"],
            "notional": round(t["total"], 0),
        })

    return found


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SEC Form 4 open market discovery via EFTS API")
    parser.add_argument("--output-dir", default="data/reports", help="Directory voor JSON/CSV output")
    parser.add_argument("--days", type=int, default=3, help="Terugkijkperiode in dagen (default: 3)")
    parser.add_argument("--workers", type=int, default=0, help="Aantal parallelle workers (0 = gebruik default)")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay per request in seconden (0 = gebruik default)")
    args = parser.parse_args()

    # Overschrijf globale instellingen op basis van CLI args
    global MAX_WORKERS, REQUEST_DELAY
    if args.workers > 0:
        MAX_WORKERS = args.workers
    if args.delay > 0.0:
        REQUEST_DELAY = args.delay

    # Reset circuit breaker voor elke run
    global RATE_LIMIT_COUNT
    RATE_LIMIT_COUNT = 0
    RATE_LIMITED.clear()

    filings = get_filings_efts(days_back=args.days)

    results      = []
    results_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_filing, f): f for f in filings}
        for future in as_completed(futures):
            try:
                found = future.result()
                if found:
                    with results_lock:
                        results.extend(found)
            except Exception:
                pass

    # Dedupliceer op ticker+datum+insider
    seen = set()
    deduped = []
    for r in results:
        key = (r["ticker"], r["date"], r["insider"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    results = sorted(deduped, key=lambda x: (-x["notional"], x["ticker"]))

    # Stdout output
    print(f"{'ticker':<8} {'issuer':<30} {'date':<12} {'insider':<30} {'role':<25} {'code':<5} {'notional':<10}")
    print("-" * 120)
    for r in results:
        print(f"{r['ticker']:<8} {r['issuer'][:28]:<30} {r['date']:<12} "
              f"{r['insider'][:28]:<30} {r['role'][:23]:<25} {r['code']:<5} ${r['notional']/1000:.0f}k")

    # File output
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
