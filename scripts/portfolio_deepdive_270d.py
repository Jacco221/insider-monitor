#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from urllib.request import Request, urlopen

UA = os.getenv("SEC_USER_AGENT", "").strip() or "InsiderMonitor/1.0 (contact: you@example.com)"
TIMEOUT = 30
RETRIES = 6
SLEEP = 0.35

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

OPEN_MARKET_CODES = {"P", "S"}

# ---------- HTTP ----------

def fetch(url: str, timeout: int = TIMEOUT) -> str:
    last_err = None
    for i in range(RETRIES):
        try:
            time.sleep(SLEEP)
            req = Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://www.sec.gov/",
                    "Connection": "close",
                },
            )
            with urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last_err = e
            time.sleep(min(12, 0.8 * (2 ** i)))
    raise RuntimeError(f"Fetch failed for {url}: {last_err}")

def fetch_json(url: str):
    return json.loads(fetch(url))

# ---------- Helpers ----------

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", required=True)
    p.add_argument("--days", type=int, default=270)
    p.add_argument("--count", type=int, default=120, help="max filings per ticker to inspect after date/form filtering")
    p.add_argument("--progress", action="store_true")
    p.add_argument("--audit", action="store_true", help="print filing-level audit section before transaction table")
    p.add_argument("--output-dir", default="", help="write JSON output to this directory (e.g. data/reports)")
    return p.parse_args()

def cutoff_date(days: int):
    return (datetime.now(timezone.utc) - timedelta(days=days)).date()

def parse_date(s: str):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def parse_float(x: str) -> float:
    x = (x or "").strip()
    if not x:
        return 0.0
    try:
        return float(x.replace(",", "").replace("$", ""))
    except Exception:
        return 0.0

def money0(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 0.5 else "0"

# ---------- XML parsing ----------

def g(xml: str, tag: str) -> str:
    m = re.search(fr"<(?:\w+:)?{tag}\b[^>]*>(.*?)</(?:\w+:)?{tag}>", xml, flags=re.S | re.I)
    return html.unescape((m.group(1) if m else "").strip())

def gv(xml: str, tag: str) -> str:
    m = re.search(
        fr"<(?:\w+:)?{tag}\b[^>]*>\s*(?:<(?:\w+:)?value[^>]*>)?\s*([^<]+)",
        xml,
        flags=re.S | re.I,
    )
    return html.unescape((m.group(1) if m else "").strip())

def tx_blocks(xml: str):
    return (
        re.findall(r"<(?:\w+:)?nonDerivativeTransaction\b[^>]*>(.+?)</(?:\w+:)?nonDerivativeTransaction>", xml, flags=re.S | re.I)
        + re.findall(r"<(?:\w+:)?derivativeTransaction\b[^>]*>(.+?)</(?:\w+:)?derivativeTransaction>", xml, flags=re.S | re.I)
    )

def extract_tx_date_from_block(blk: str):
    m = re.search(
        r"<(?:\w+:)?transactionDate\b[^>]*>\s*(?:<(?:\w+:)?value\b[^>]*>)?\s*([^<\s]+)",
        blk,
        flags=re.S | re.I,
    )
    return parse_date(m.group(1)) if m else None

def best_role(xml: str) -> str:
    officer_title = g(xml, "officerTitle")
    if officer_title:
        return officer_title.strip()

    is_director = g(xml, "isDirector")
    is_officer = g(xml, "isOfficer")
    is_tenpct = g(xml, "isTenPercentOwner")

    parts = []
    if is_director == "1":
        parts.append("Director")
    if is_officer == "1":
        parts.append("Officer")
    if is_tenpct == "1":
        parts.append("10% Owner")
    return ", ".join(parts) if parts else ""

def extract_10b5_1(xml: str) -> str:
    low = xml.lower()
    return "YES" if ("10b5-1" in low or "10b5 1" in low) else "NO"

def parse_form4_open_market_rows(xml: str, filing_date):
    issuer = clean_text(g(xml, "issuerName"))
    xml_ticker = clean_text(g(xml, "issuerTradingSymbol")).upper()
    issuer_cik = clean_text(g(xml, "issuerCik"))

    owner = clean_text(g(xml, "rptOwnerName")) or "Unknown"
    role = best_role(xml)
    tenb5 = extract_10b5_1(xml)

    rows = []
    codes_found = []

    for blk in tx_blocks(xml):
        code = g(blk, "transactionCode").upper()
        if code:
            codes_found.append(code)

        if code not in OPEN_MARKET_CODES:
            continue

        tx_date = extract_tx_date_from_block(blk) or filing_date
        shares = parse_float(gv(blk, "transactionShares"))
        price = parse_float(gv(blk, "transactionPricePerShare"))
        total_value = parse_float(gv(blk, "transactionTotalValue"))

        val = shares * price if (shares > 0 and price > 0) else total_value
        buy = val if code == "P" else 0.0
        sell = val if code == "S" else 0.0

        rows.append({
            "xml_ticker": xml_ticker,
            "issuer": issuer,
            "issuer_cik": issuer_cik,
            "date": tx_date,
            "insider": owner,
            "role": role,
            "code": code,
            "BUY": buy,
            "SELL": sell,
            "10b5-1": tenb5,
        })

    return rows, sorted(set(codes_found))

# ---------- SEC navigation ----------

def load_ticker_map():
    data = fetch_json(TICKERS_URL)
    out = {}
    for _, v in data.items():
        t = str(v.get("ticker", "")).upper()
        if not t:
            continue
        out[t] = {
            "ticker": t,
            "title": v.get("title", ""),
            "cik_str": str(v.get("cik_str", "")).zfill(10),
        }
    return out

def get_all_filings_for_cik(cik_str: str):
    data = fetch_json(SUBMISSIONS_URL.format(cik=cik_str))
    filings = []

    recent = data.get("filings", {}).get("recent", {})
    n = len(recent.get("accessionNumber", []))
    for i in range(n):
        filings.append({
            "accessionNumber": recent["accessionNumber"][i],
            "filingDate": recent["filingDate"][i],
            "form": recent["form"][i],
            "primaryDocument": recent.get("primaryDocument", [""] * n)[i],
        })

    for f in data.get("filings", {}).get("files", []):
        name = f.get("name")
        if not name:
            continue
        older = fetch_json(urljoin("https://data.sec.gov/submissions/", name))
        m = len(older.get("accessionNumber", []))
        for i in range(m):
            filings.append({
                "accessionNumber": older["accessionNumber"][i],
                "filingDate": older["filingDate"][i],
                "form": older["form"][i],
                "primaryDocument": older.get("primaryDocument", [""] * m)[i] if "primaryDocument" in older else "",
            })

    ded = {}
    for f in filings:
        ded[f["accessionNumber"]] = f
    return list(ded.values())

def accession_nodashes(acc: str) -> str:
    return acc.replace("-", "")

def filing_index_url(cik_num: str, accession: str) -> str:
    cik_plain = str(int(cik_num))
    acc_no = accession_nodashes(accession)
    return f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{acc_no}/{accession}-index.htm"

def find_best_xml_from_index(index_url: str):
    page = fetch(index_url)
    cands = []
    for m in re.finditer(r'href="([^"]+\.xml)"', page, flags=re.I):
        href = html.unescape(m.group(1))
        url = href if href.startswith("http") else urljoin("https://www.sec.gov", href)
        name = url.lower()
        score = 0
        if "ownership" in name:
            score += 5
        if "form4" in name:
            score += 4
        if "primary" in name:
            score += 3
        if "xml" in name:
            score += 1
        cands.append((url, score))

    cands.sort(key=lambda x: x[1], reverse=True)
    for url, _ in cands:
        xml = fetch(url)
        if xml and re.search(r"<(?:\w+:)?ownershipDocument\b", xml, flags=re.I):
            return url, xml
    return "", ""

# ---------- Post-processing ----------

def dedupe_rows(rows):
    out = []
    seen = set()
    for r in rows:
        key = (
            r["ticker"],
            r["date"].isoformat() if r["date"] else "",
            r["insider"],
            r["role"],
            r["code"],
            round(r["BUY"], 2),
            round(r["SELL"], 2),
            r["10b5-1"],
            r["xml"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

def summarize(rows):
    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)

    summary = []
    for tkr, rs in sorted(by_ticker.items()):
        rs_sorted = sorted(rs, key=lambda x: (x["date"], x["insider"], x["code"], x["BUY"], x["SELL"]))
        p_buy = sum(r["BUY"] for r in rs_sorted)
        s_sell = sum(r["SELL"] for r in rs_sorted)
        net = p_buy - s_sell

        p_dates = [r["date"] for r in rs_sorted if r["BUY"] > 0 and r["date"]]
        first_p = min(p_dates).isoformat() + "T00:00:00+00:00" if p_dates else ""
        last_p_date = max(p_dates) if p_dates else None
        last_p = last_p_date.isoformat() + "T00:00:00+00:00" if last_p_date else ""

        net_since_lastP = 0.0
        if last_p_date:
            for r in rs_sorted:
                if r["date"] and r["date"] >= last_p_date:
                    net_since_lastP += (r["BUY"] - r["SELL"])

        summary.append({
            "ticker": tkr,
            "rows": len(rs_sorted),
            "P_BUY": p_buy,
            "S_SELL": s_sell,
            "NET": net,
            "first_P": first_p,
            "last_P": last_p,
            "net_since_lastP": net_since_lastP,
            "early_stop": "YES" if len(rs_sorted) < 120 else "NO",
        })
    return summary

# ---------- Main ----------

def main():
    args = parse_args()
    cutoff = cutoff_date(args.days)
    ticker_map = load_ticker_map()

    all_rows = []
    audit = []

    for requested_ticker in [t.upper() for t in args.tickers]:
        if requested_ticker not in ticker_map:
            print(f"[warn] ticker niet gevonden in SEC ticker map: {requested_ticker}", file=sys.stderr)
            continue

        cik = ticker_map[requested_ticker]["cik_str"]
        filings = get_all_filings_for_cik(cik)

        cand = []
        for f in filings:
            fdate = parse_date(f.get("filingDate", ""))
            if not fdate or fdate < cutoff:
                continue
            if f.get("form") not in {"4", "4/A"}:
                continue
            cand.append(f)

        cand.sort(key=lambda x: x["filingDate"], reverse=True)
        cand = cand[:args.count]

        if args.progress:
            for i, _ in enumerate(cand, 1):
                if i % 10 == 0:
                    print(f"[{requested_ticker}] processed {i} filings…", file=sys.stderr)

        for f in cand:
            accession = f["accessionNumber"]
            filing_date = parse_date(f["filingDate"])
            index_url = filing_index_url(cik, accession)

            xml_url = ""
            xml = ""
            xml_found = "NO"
            codes_found = []
            open_rows = []

            try:
                xml_url, xml = find_best_xml_from_index(index_url)
            except Exception:
                xml = ""

            if xml:
                xml_found = "YES"
                try:
                    open_rows, codes_found = parse_form4_open_market_rows(xml, filing_date)
                except Exception:
                    open_rows = []
                    codes_found = []

            audit.append({
                "ticker": requested_ticker,
                "filingDate": f["filingDate"],
                "form": f["form"],
                "accession": accession,
                "xml_found": xml_found,
                "codes_found": ",".join(codes_found) if codes_found else "",
                "xml_url": xml_url or index_url,
            })

            # IMPORTANT:
            # We trust ticker context from the requested company CIK.
            # We do NOT drop rows just because xml_ticker is blank/mismatched.
            for r in open_rows:
                all_rows.append({
                    "ticker": requested_ticker,
                    "date": r["date"],
                    "insider": r["insider"],
                    "role": r["role"] or "Director" if "Director" in (r["role"] or "") else r["role"],
                    "code": r["code"],
                    "BUY": r["BUY"],
                    "SELL": r["SELL"],
                    "10b5-1": r["10b5-1"],
                    "xml": xml_url or index_url,
                })

    all_rows = dedupe_rows(all_rows)
    all_rows.sort(key=lambda x: (x["ticker"], x["date"], x["insider"], x["code"], x["BUY"], x["SELL"]))

    if args.audit:
        print("\n=== AUDIT (FORM 4 / 4A FILINGS INSPECTED) ===")
        print("ticker\tfilingDate\tform\taccession\txml_found\tcodes_found\txml")
        for a in audit:
            print(
                f"{a['ticker']}\t{a['filingDate']}\t{a['form']}\t{a['accession']}\t"
                f"{a['xml_found']}\t{a['codes_found']}\t{a['xml_url']}"
            )

    print("\n=== FULL OPEN-MARKET (P/S) TRANSACTIONS ===")
    print("ticker\tdate\tinsider\trole\tcode\tBUY\tSELL\t10b5-1\txml")
    for r in all_rows:
        print(
            f"{r['ticker']}\t"
            f"{r['date'].isoformat() if r['date'] else ''}\t"
            f"{r['insider']}\t"
            f"{r['role']}\t"
            f"{r['code']}\t"
            f"{money0(r['BUY'])}\t"
            f"{money0(r['SELL'])}\t"
            f"{r['10b5-1']}\t"
            f"{r['xml']}"
        )

    summary = summarize(all_rows)
    print("\n=== SUMMARY (per ticker) ===")
    print("ticker\trows\tP_BUY\tS_SELL\tNET\tfirst_P\tlast_P\tnet_since_lastP\tearly_stop")
    for s in summary:
        print(
            f"{s['ticker']}\t{s['rows']}\t"
            f"${money0(s['P_BUY'])}\t"
            f"${money0(s['S_SELL'])}\t"
            f"${money0(s['NET'])}\t"
            f"{s['first_P']}\t{s['last_P']}\t"
            f"${money0(s['net_since_lastP'])}\t{s['early_stop']}"
        )

    # Structured JSON output
    if args.output_dir:
        from pathlib import Path
        outdir = Path(args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        tickers_label = "_".join(t.upper() for t in args.tickers[:5])
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        json_path = outdir / f"deepdive_{tickers_label}_{today}.json"

        def serialize_row(r):
            row = dict(r)
            if row.get("date"):
                row["date"] = row["date"].isoformat()
            return row

        output = {
            "tickers": [t.upper() for t in args.tickers],
            "days": args.days,
            "generated": today,
            "transactions": [serialize_row(r) for r in all_rows],
            "summary": summary,
            "audit": audit,
        }
        json_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
        print(f"\n[info] JSON geschreven naar {json_path}", file=sys.stderr)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f"FOUT: {e}", file=sys.stderr)
        raise SystemExit(1)
