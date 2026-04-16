#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Insider Monitor — SEC Form 4 discovery + portfolio analyse in één script.

Doelen:
  1. Vind sterke insider buys (C-suite, open market ≥$100K, vers ≤5d)
  2. Monitor portefeuille-posities op exit-signalen (270d lookback)
  3. Scoor alles 1-10 op basis van literatuur (Lakonishok & Lee 2001,
     Seyhun 1998, Cohen et al. 2012)
  4. Stuur Telegram met scores, advies en systeem-status

Gebruik:
  python3 scripts/monitor.py --portfolio BH NKE IPX SBSW --telegram
  python3 scripts/monitor.py --portfolio BH NKE IPX SBSW          # console only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

# ── Configuratie ──────────────────────────────────────────────────────────────

UA        = os.getenv("SEC_USER_AGENT", "InsiderMonitor/2.0 (contact: you@example.com)")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

DISCOVERY_DAYS   = 5          # Lookback discovery: 5d vangt weekenden + SEC-vertraging
ANALYSIS_DAYS    = 270        # Lookback voor signaalanalyse (Lakonishok & Lee: 6-9 mnd)
MIN_BUY_USD      = 100_000    # Minimaal aankoopbedrag voor discovery
MAX_BUY_USD      = 200_000_000 # Sanity cap: erboven = ADS-structuurfout (SVRE-bug)
MIN_BUY_ANALYSIS = 50_000     # Minimaal aankoopbedrag voor 270d analyse
IPO_MIN_DAYS     = 365        # Bedrijf minimaal 1 jaar genoteerd
DECAY_HALFLIFE   = 90         # Half-life tijdsdecay in dagen (Lakonishok & Lee 2001)
MAX_WORKERS      = 3          # Parallelle threads voor analyse
REQUEST_DELAY    = 0.35       # Seconden pauze per HTTP-request
HTTP_TIMEOUT     = 15         # Timeout per request in seconden
HTTP_RETRIES     = 4          # Aantal retries bij fout
TOP_N            = 3          # Kandidaten in Telegram

# C-suite keywords — Seyhun (1998): CEO/CFO/COO buys zijn het meest predictief
CSUITE = {"ceo", "chief executive", "president", "cfo", "chief financial",
          "coo", "chief operating", "cto", "chief technology", "clo", "chief legal",
          "evp", "executive vice", "svp", "senior vice"}

# Sell-codes die géén bewuste marktkeuze zijn → negeren als sell-signaal
IGNORE_SELL_CODES = {"F", "A", "M", "G", "W", "J", "C", "D", "L", "Z"}

# ── HTTP ──────────────────────────────────────────────────────────────────────

_req_lock = threading.Lock()
_rate_limited = threading.Event()


def _fetch(url: str) -> str:
    """GET met retry, rate limiting en backoff. Thread-safe."""
    if _rate_limited.is_set():
        return ""
    with _req_lock:
        time.sleep(REQUEST_DELAY)
    for attempt in range(HTTP_RETRIES):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 429:
                wait = 20 * (attempt + 1)
                print(f"[warn] 429 rate limit — wacht {wait}s", file=sys.stderr)
                time.sleep(wait)
            elif attempt < HTTP_RETRIES - 1:
                time.sleep(min(10, 1.5 ** attempt))
    return ""


def _fetch_json(url: str) -> dict | list:
    text = _fetch(url)
    return json.loads(text) if text else {}


# ── CIK lookup ────────────────────────────────────────────────────────────────

def load_cik_map() -> dict[str, str]:
    """Laad ticker→CIK van SEC. Returns {} bij fout (non-fataal)."""
    try:
        data = _fetch_json("https://www.sec.gov/files/company_tickers.json")
        return {
            str(v.get("ticker", "")).upper(): str(v.get("cik_str", "")).zfill(10)
            for v in data.values()
            if v.get("ticker") and v.get("cik_str")
        }
    except Exception as e:
        print(f"[warn] CIK map ophalen mislukt: {e}", file=sys.stderr)
        return {}


# ── EFTS discovery ────────────────────────────────────────────────────────────

def discover_recent_buys(days: int = DISCOVERY_DAYS) -> list[dict]:
    """
    Haal recente Form 4 open-market aankopen op via SEC EFTS API.

    Geeft per filing terug:
      ticker, cik, issuer, insider, role, is_csuite, date, amount
    """
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()
    end   = today.isoformat()

    filings = []
    offset, page_size = 0, 100

    while True:
        url = (
            f"https://efts.sec.gov/LATEST/search-index?forms=4"
            f"&dateRange=custom&startdt={start}&enddt={end}"
            f"&from={offset}"
        )
        data  = _fetch_json(url)
        hits  = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        if not hits:
            break

        for h in hits:
            src  = h.get("_source", {})
            adsh = src.get("adsh") or h.get("_id", "").split(":")[0]
            # XML-bestandsnaam zit in _id na ":"
            raw_id   = h.get("_id", "")
            xml_file = raw_id.split(":")[-1] if ":" in raw_id else "form4.xml"
            # CIK: gebruik ciks[0] (= reporting owner CIK, niet filing agent)
            ciks = src.get("ciks", [])
            cik  = str(int(ciks[0])) if ciks else None
            if cik:
                filings.append({
                    "adsh": adsh, "xml_file": xml_file,
                    "file_date": src.get("file_date", ""), "cik": cik,
                })

        offset += len(hits)
        if offset >= total:
            break

    print(f"[discovery] {len(filings)} Form 4 filings gevonden ({start} → {end})", file=sys.stderr)

    results = []
    seen    = set()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_parse_filing, f): f for f in filings}
        for future in as_completed(futures):
            for row in (future.result() or []):
                key = (row["ticker"], row["insider"], row["date"])
                if key not in seen:
                    seen.add(key)
                    results.append(row)

    results.sort(key=lambda x: -x["amount"])
    print(f"[discovery] {len(results)} kandidaten na filtering", file=sys.stderr)
    return results


def _parse_filing(filing: dict) -> list[dict]:
    """Haal XML op voor één filing en filter op open-market buys ≥ MIN_BUY_USD."""
    cik      = filing["cik"]
    adsh     = filing["adsh"]
    xml_file = filing["xml_file"]
    acc_no   = adsh.replace("-", "")

    xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no}/{xml_file}"
    xml = _fetch(xml_url)
    if not xml or "ownershipDocument" not in xml:
        # Fallback bestandsnamen
        for alt in ["form4.xml", "primarydocument.xml"]:
            if alt == xml_file:
                continue
            xml = _fetch(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no}/{alt}")
            if xml and "ownershipDocument" in xml:
                break
        else:
            return []

    meta = _parse_meta(xml)
    ticker = meta.get("ticker", "")
    if not ticker:
        return []

    # IPO-filter: bedrijf minimaal 1 jaar genoteerd
    if _is_recent_ipo(cik):
        return []

    # Puur 10%-eigenaar zonder officer/director-rol → lagere informatiewaarde, skip
    if (meta.get("is_ten_pct") == "1"
            and meta.get("is_officer") != "1"
            and meta.get("is_director") != "1"
            and not meta.get("role")):
        return []

    found = []
    for tx in _parse_transactions(xml):
        if tx["code"] != "P":
            continue
        amount = tx["amount"]
        if amount < MIN_BUY_USD:
            continue
        if amount > MAX_BUY_USD:
            print(f"[skip] {ticker} ${amount/1e6:.0f}M > sanity cap — ADS-structuur?", file=sys.stderr)
            continue
        role = meta.get("role", "")
        found.append({
            "ticker":    ticker,
            "cik":       cik,
            "issuer":    meta.get("issuer", ""),
            "insider":   meta.get("owner", ""),
            "role":      role,
            "is_csuite": _is_csuite(role),
            "date":      filing["file_date"],
            "amount":    amount,
        })
    return found


# ── IPO filter ────────────────────────────────────────────────────────────────

_ipo_cache: dict[str, bool] = {}
_ipo_lock  = threading.Lock()


def _is_recent_ipo(cik: str) -> bool:
    """True als bedrijf < IPO_MIN_DAYS geleden genoteerd."""
    with _ipo_lock:
        if cik in _ipo_cache:
            return _ipo_cache[cik]
    try:
        cik_p = cik.zfill(10)
        data  = _fetch_json(f"https://data.sec.gov/submissions/CIK{cik_p}.json")
        dates = data.get("filings", {}).get("recent", {}).get("filingDate", [])
        result = (min(dates) and
                  (date.today() - date.fromisoformat(min(dates))).days < IPO_MIN_DAYS
                  ) if dates else True
    except Exception:
        result = False
    with _ipo_lock:
        _ipo_cache[cik] = result
    return result


# ── XML parsing ───────────────────────────────────────────────────────────────

def _g(xml: str, tag: str) -> str:
    m = re.search(fr"<{tag}>(.*?)</{tag}>", xml)
    return m.group(1).strip() if m else ""


def _parse_meta(xml: str) -> dict:
    role = _g(xml, "officerTitle")
    if not role:
        parts = []
        if _g(xml, "isDirector") == "1": parts.append("Director")
        if _g(xml, "isOfficer")  == "1": parts.append("Officer")
        role = ", ".join(parts)
    return {
        "ticker":     _g(xml, "issuerTradingSymbol"),
        "issuer":     _g(xml, "issuerName"),
        "owner":      _g(xml, "rptOwnerName"),
        "role":       role,
        "is_officer": _g(xml, "isOfficer"),
        "is_director":_g(xml, "isDirector"),
        "is_ten_pct": _g(xml, "isTenPercentOwner"),
    }


def _parse_transactions(xml: str) -> list[dict]:
    txs = []
    for block in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml, re.S):
        code_m   = re.search(r"<transactionCode>(.*?)</transactionCode>", block)
        shares_m = re.search(r"<transactionShares>.*?<value>(.*?)</value>", block, re.S)
        price_m  = re.search(r"<transactionPricePerShare>.*?<value>(.*?)</value>", block, re.S)
        date_m   = re.search(r"<transactionDate>.*?<value>(.*?)</value>", block, re.S)
        if not code_m:
            continue
        code = code_m.group(1).strip().upper()
        try:
            shares = float(shares_m.group(1)) if shares_m else 0.0
            price  = float(price_m.group(1))  if price_m  else 0.0
            tx_date = date_m.group(1).strip()[:10] if date_m else ""
        except (ValueError, TypeError):
            continue
        txs.append({"code": code, "amount": shares * price, "date": tx_date})
    return txs


def _is_csuite(role: str) -> bool:
    r = role.lower()
    return any(k in r for k in CSUITE)


# ── 270d ticker analyse ───────────────────────────────────────────────────────

def analyse_ticker(ticker: str, cik: str, days: int = ANALYSIS_DAYS) -> dict:
    """
    Haal 270d Form 4-history op via SEC submissions API en bereken signaal.

    Geeft dict terug met alle velden die nodig zijn voor scoring en Telegram.
    """
    ticker = ticker.upper()
    cik_p  = cik.zfill(10)
    cutoff = date.today() - timedelta(days=days)

    # Submissions JSON bevat lijst van recente filings + verwijzingen naar archiefpagina's
    subs = _fetch_json(f"https://data.sec.gov/submissions/CIK{cik_p}.json")
    if not subs:
        return _empty(ticker, "SEC submissions niet bereikbaar")

    recent = subs.get("filings", {}).get("recent", {})

    buys:  list[dict] = []
    sells: list[dict] = []

    acc_numbers   = recent.get("accessionNumber", [])
    filing_dates  = recent.get("filingDate", [])
    form_types    = recent.get("form", [])
    primary_docs  = recent.get("primaryDocument", [])

    for i, acc in enumerate(acc_numbers):
        if form_types[i] not in ("4", "4/A"):
            continue
        try:
            filing_date = date.fromisoformat(filing_dates[i])
        except (ValueError, IndexError):
            continue
        if filing_date < cutoff:
            continue

        # Bouw XML-URL direct vanuit primaryDocument (geen index-pagina nodig)
        acc_clean = acc.replace("-", "")
        prim_doc  = primary_docs[i] if i < len(primary_docs) else ""
        xml = ""

        if prim_doc:
            xml = _fetch(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{prim_doc}")
        if not xml or "ownershipDocument" not in xml:
            # Fallbacks
            for alt in [f"{acc_clean}.xml", "form4.xml", "primarydocument.xml"]:
                xml = _fetch(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{alt}")
                if xml and "ownershipDocument" in xml:
                    break

        if not xml:
            continue

        meta = _parse_meta(xml)
        owner = meta.get("owner", "Unknown")
        role  = meta.get("role", "")

        for tx in _parse_transactions(xml):
            code   = tx["code"]
            amount = tx["amount"]
            tx_date = filing_date  # gebruik filing date als transactiedatum ontbreekt
            if tx.get("date"):
                try:
                    tx_date = date.fromisoformat(tx["date"])
                except ValueError:
                    pass

            if code == "P" and amount >= MIN_BUY_ANALYSIS:
                if amount > MAX_BUY_USD:
                    continue  # ADS-sanity cap
                buys.append({"insider": owner, "role": role, "amount": amount, "date": tx_date})
            elif code == "S" and code not in IGNORE_SELL_CODES and amount > 0:
                sells.append({"insider": owner, "role": role, "amount": amount, "date": tx_date})

    return _build_result(ticker, buys, sells)


def _empty(ticker: str, reason: str) -> dict:
    return {
        "ticker": ticker, "signal": "UNKNOWN", "reasons": [reason],
        "total_buy": 0, "total_sell": 0, "net_flow": 0,
        "days_since_buy": 999, "unique_buyers": 0,
        "csuite_buyers": [], "csuite_sellers": [],
        "buys_detail": [], "sells_detail": [],
        "discovery": None,
    }


def _build_result(ticker: str, buys: list, sells: list) -> dict:
    """Bereken signaal en verzamel alle velden voor scoring + Telegram."""
    today = date.today()

    def decay(d: date) -> float:
        """Exponentiële tijdsdecay, half-life = DECAY_HALFLIFE dagen."""
        return math.exp(-(today - d).days * math.log(2) / DECAY_HALFLIFE)

    total_buy  = sum(b["amount"] for b in buys)
    total_sell = sum(s["amount"] for s in sells)
    net_flow   = total_buy - total_sell

    w_buy  = sum(b["amount"] * decay(b["date"]) * _role_weight(b["role"]) for b in buys)
    w_sell = sum(s["amount"] * decay(s["date"]) * _role_weight(s["role"]) for s in sells)
    w_net  = w_buy - w_sell

    last_buy = max((b["date"] for b in buys), default=None)
    days_since = (today - last_buy).days if last_buy else 999

    unique_buyers  = {b["insider"] for b in buys}
    unique_sellers = {s["insider"] for s in sells}

    csuite_buyers  = {b["insider"] for b in buys  if _is_csuite(b["role"])}
    csuite_sellers = {s["insider"] for s in sells if _is_csuite(s["role"])}

    recent_buyers = {b["insider"] for b in buys if (today - b["date"]).days <= 14}

    # ── Signaalclassificatie (5 niveaus) ────────────────────────────────────
    # Bron: Lakonishok & Lee (2001), Cohen et al. (2012), Seyhun (1998)
    reasons = []

    if days_since <= 30:
        signal = "POSITIEF SIGNAAL"
        reasons.append(f"Vers buy signaal ({days_since}d geleden)")
    elif days_since <= 90:
        signal = "POSITIEF SIGNAAL"
        reasons.append(f"Buy signaal actief ({days_since}d geleden)")
    else:
        signal = "SIGNAAL UITGEWERKT"
        reasons.append(f"Geen insider buy in {days_since}d")

    # Upgrade: C-suite koopt vers + netto positief
    if csuite_buyers and days_since <= 30 and not csuite_sellers and net_flow > 0:
        signal = "STERKE OVERTUIGING"
        reasons.append(f"C-suite koper: {', '.join(list(csuite_buyers)[:2])}")

    # Upgrade: cluster (≥3 unieke insiders binnen 14d)
    if len(recent_buyers) >= 3 and days_since <= 30:
        signal = "STERKE OVERTUIGING"
        reasons.append(f"Cluster: {len(recent_buyers)} insiders kochten in 14d")

    # Downgrade: netto sell ondanks verse buys
    if net_flow < 0 and days_since <= 30:
        signal = "GEMENGD SIGNAAL"
        reasons.append(f"Netto sell ondanks verse buys: ${net_flow:,.0f}")

    # Downgrade: C-suite verkoopt open market, geen C-suite kopers
    if csuite_sellers and not csuite_buyers and days_since > 30:
        signal = "NEGATIEF SIGNAAL"
        reasons.append(f"C-suite verkoopt: {', '.join(list(csuite_sellers)[:2])}")

    # Downgrade: gewogen netto negatief na vers-venster
    if w_net < 0 and days_since > 30:
        signal = "NEGATIEF SIGNAAL"
        reasons.append("Gewogen netto negatief (sells zwaarder dan buys)")

    # Downgrade naar uitgewerkt: netto sell buiten vers venster
    if net_flow < 0 and days_since > 30:
        signal = "SIGNAAL UITGEWERKT"
        reasons.append(f"Netto sell: ${net_flow:,.0f}")

    # Advies
    advies = {
        "STERKE OVERTUIGING": "AANHOUDEN",
        "POSITIEF SIGNAAL":   "AANHOUDEN",
        "GEMENGD SIGNAAL":    "MONITOREN",
        "NEGATIEF SIGNAAL":   "VERKOPEN",
        "SIGNAAL UITGEWERKT": "VERKOPEN",
    }.get(signal, "MONITOREN")

    def detail(txs, n=8):
        return [{"insider": t["insider"], "role": t["role"],
                 "amount": t["amount"], "date": t["date"].isoformat()}
                for t in sorted(txs, key=lambda x: x["date"], reverse=True)[:n]]

    return {
        "ticker":        ticker,
        "signal":        signal,
        "advies":        advies,
        "reasons":       reasons,
        "total_buy":     total_buy,
        "total_sell":    total_sell,
        "net_flow":      net_flow,
        "days_since_buy": days_since,
        "unique_buyers": len(unique_buyers),
        "csuite_buyers": list(csuite_buyers),
        "csuite_sellers": list(csuite_sellers),
        "buys_detail":   detail(buys),
        "sells_detail":  detail(sells),
        "discovery":     None,  # wordt ingevuld door main()
    }


def _role_weight(role: str) -> float:
    """Gewicht op basis van rol voor tijdsgecorrigeerde score."""
    r = role.lower()
    if any(k in r for k in {"ceo", "chief executive", "president"}): return 5.0
    if any(k in r for k in {"cfo", "chief financial", "coo", "chief operating"}): return 4.0
    if any(k in r for k in {"cto", "evp", "svp"}): return 3.0
    if "vp" in r or "vice" in r: return 2.0
    if "director" in r: return 2.0
    return 1.0


# ── Scoring ───────────────────────────────────────────────────────────────────

def score(r: dict) -> int:
    """
    Uniformele score 0-10 voor zowel portefeuille als kandidaten.

    Formule (max ruwe score = 12, genormaliseerd):
      Signaalsterkte   : STERKE=3, POSITIEF=2, GEMENGD=1, anders=0
      C-suite koper    : +2 (of -3 bij alleen C-suite sell)
      Versheid         : ≤7d=+3, ≤14d=+2, ≤30d=+1
      Cluster ≥3/14d   : +1
      Koopomvang       : ≥$5M=+2, ≥$1M=+1
      Netto positief   : +1

    Literatuur: Seyhun (1998) — C-suite meest predictief
                Lakonishok & Lee (2001) — cluster + recency sterkste combo
                Cohen et al. (2012) — routine vs. opportunistische insider
    """
    raw = 0
    sig       = r.get("signal", "UNKNOWN")
    days      = r.get("days_since_buy", 999)
    c_buyers  = r.get("csuite_buyers", [])
    c_sellers = r.get("csuite_sellers", [])
    n_buyers  = r.get("unique_buyers", 0)
    total_buy = r.get("total_buy", 0)
    net_flow  = r.get("net_flow", 0)

    raw += {"STERKE OVERTUIGING": 3, "POSITIEF SIGNAAL": 2, "GEMENGD SIGNAAL": 1}.get(sig, 0)

    if c_buyers and not c_sellers:   raw += 2
    elif c_sellers and not c_buyers: raw -= 3

    if days <= 7:    raw += 3
    elif days <= 14: raw += 2
    elif days <= 30: raw += 1

    if n_buyers >= 3 and days <= 30: raw += 1

    if total_buy >= 5_000_000:   raw += 2
    elif total_buy >= 1_000_000: raw += 1

    if net_flow > 0 and not c_sellers: raw += 1

    return max(0, min(10, round(max(0, raw) / 12 * 10)))


def score_emoji(s: int) -> str:
    if s >= 9: return "🟢🟢🟢"
    if s >= 8: return "🟢🟢"
    if s >= 6: return "🟢"
    if s >= 4: return "🟡"
    if s >= 2: return "🟠"
    return "🔴"


# ── Telegram ─────────────────────────────────────────────────────────────────

ADVIES_EMOJI = {"AANHOUDEN": "✅", "MONITOREN": "👁️", "VERKOPEN": "❌"}


def _fmt_amount(n: float) -> str:
    """Toon bedrag als $1.2M of $250K. Sanity check op ADS-fouten."""
    if abs(n) > 500_000_000:
        return "⚠️ data-fout"
    if abs(n) >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    return f"${n/1_000:.0f}K"


def build_telegram(
    portfolio: list[dict],
    candidates: list[dict],
    health: list[str],
    alerts: list[str],
) -> str:
    msg = f"<b>📊 Insider Monitor</b> — {datetime.now().strftime('%d %b %H:%M')}\n"
    msg += "─" * 28 + "\n\n"

    # Kritieke alerts bovenaan
    for a in alerts:
        msg += f"{a}\n\n"
    if alerts:
        msg += "─" * 28 + "\n\n"

    # ── Portefeuille ─────────────────────────────────────────────────────────
    msg += "<b>💼 PORTEFEUILLE</b>\n"
    for r in portfolio:
        s    = score(r)
        emo  = score_emoji(s)
        adv  = ADVIES_EMOJI.get(r.get("advies", ""), "")
        disc = r.get("discovery")
        msg += f"{emo} <b>{r['ticker']}</b> — {r['signal']} [{s}/10]\n"
        if disc:
            msg += f"  Trigger: {_fmt_amount(disc['amount'])} ({disc['days']}d geleden)"
            msg += (" ✅ C-suite\n" if disc["is_csuite"] else " ⚠️ Director\n")
        else:
            msg += f"  Netto: {_fmt_amount(r['net_flow'])} | {r['days_since_buy']}d geleden\n"
        if r.get("reasons"):
            msg += f"  ↳ {r['reasons'][0]}\n"
        msg += f"  {adv} <b>{r.get('advies', '?')}</b>\n\n"

    exits = [r for r in portfolio if r.get("advies") in ("VERKOPEN",)]
    if exits:
        msg += f"⚠️ <b>ACTIE:</b> overweeg verkoop {', '.join(r['ticker'] for r in exits)}\n\n"
    else:
        msg += "✅ Alle posities stabiel\n\n"

    # ── Top kandidaten ────────────────────────────────────────────────────────
    msg += "─" * 28 + "\n"
    msg += "<b>🔍 TOP KANDIDATEN</b>\n"
    if candidates:
        for r in candidates:
            s   = score(r)
            emo = score_emoji(s)
            disc = r.get("discovery")
            msg += f"{emo} <b>{r['ticker']}</b> — {r['signal']} [{s}/10]\n"
            if disc:
                msg += f"  Trigger: {_fmt_amount(disc['amount'])} ({disc['days']}d geleden)"
                msg += (" ✅ C-suite\n" if disc["is_csuite"] else " ⚠️ Alleen directors\n")
            else:
                msg += f"  Netto 270d: {_fmt_amount(r['net_flow'])} | {r['days_since_buy']}d geleden\n"
            if r.get("reasons"):
                msg += f"  ↳ {r['reasons'][0]}\n"
            if s >= 6:
                msg += "  💡 Onderzoeken als instapkandidaat\n"
            msg += "\n"
    else:
        msg += "  Geen kandidaten vandaag\n\n"

    # ── Systeem status ────────────────────────────────────────────────────────
    msg += "─" * 28 + "\n"
    msg += "<b>⚙️ SYSTEEM STATUS</b>\n"
    for line in health:
        msg += f"  {line}\n"

    return msg


def send_telegram(msg: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        data = urlencode({"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        req  = Request(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data,
                       headers={"User-Agent": UA})
        with urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"[warn] Telegram fout: {e}", file=sys.stderr)
        return False


# ── Health check ──────────────────────────────────────────────────────────────

def health_check(
    output_dir: Path,
    n_discovery: int,
    n_unknown: int,
    total_tickers: int,
) -> tuple[list[str], list[str]]:
    """
    Controleer systeemgezondheid en detecteer anomalieën.
    Geeft (health_lines, alert_lines) terug.
    """
    lines, alerts = [], []
    today_str = date.today().isoformat()

    # Persistente discovery-log (30 dagen)
    log_path = output_dir / "health_log.json"
    try:
        log = json.loads(log_path.read_text()) if log_path.exists() else []
    except Exception:
        log = []

    log = [e for e in log if e.get("date") != today_str]
    log.append({"date": today_str, "discovery": n_discovery})
    log = sorted(log, key=lambda x: x["date"])[-30:]
    try:
        log_path.write_text(json.dumps(log, indent=2))
    except Exception:
        pass

    # Streak van nul-discovery-dagen
    zero_streak = 0
    for e in reversed(log):
        if e.get("discovery", 0) == 0:
            zero_streak += 1
        else:
            break

    if n_discovery == 0:
        lines.append("🟡 Discovery: 0 buys vandaag")
    else:
        lines.append(f"🟢 Discovery: {n_discovery} buys gevonden")

    if zero_streak >= 3:
        alerts.append(
            f"🚨 <b>ANOMALIE:</b> {zero_streak} opeenvolgende dagen 0 discovery "
            f"— mogelijke bug of SEC-blokkade"
        )
    elif zero_streak >= 2:
        lines.append(f"⚠️ Al {zero_streak} dagen 0 discovery buys")

    if n_unknown > 0:
        lines.append(f"🟡 {n_unknown}/{total_tickers} tickers niet gevonden in SEC")
    else:
        lines.append(f"🟢 Alle {total_tickers} tickers geanalyseerd")

    return lines, alerts


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Insider Monitor v2")
    parser.add_argument("--portfolio", nargs="+", default=[],
                        help="Portfolio tickers (bijv. BH NKE IPX SBSW)")
    parser.add_argument("--days", type=int, default=ANALYSIS_DAYS,
                        help=f"Analyseperiode in dagen (default {ANALYSIS_DAYS})")
    parser.add_argument("--discovery-days", type=int, default=DISCOVERY_DAYS,
                        help=f"Discovery lookback (default {DISCOVERY_DAYS})")
    parser.add_argument("--output-dir", default="data/reports",
                        help="Output directory voor JSON en health log")
    parser.add_argument("--telegram", action="store_true",
                        help="Stuur resultaat via Telegram")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    portfolio_set = {t.upper() for t in args.portfolio}

    print(f"[monitor] Portefeuille: {', '.join(sorted(portfolio_set)) or '(geen)'}", file=sys.stderr)

    # Stap 1: Discovery — vind recente Form 4 open-market aankopen
    print(f"[monitor] Stap 1: discovery ({args.discovery_days}d lookback)...", file=sys.stderr)
    discoveries = discover_recent_buys(args.discovery_days)

    # Groepeer per ticker: totaal bedrag + C-suite aanwezig?
    disc_by_ticker: dict[str, dict] = {}
    today = date.today()
    for row in discoveries:
        t = row["ticker"].upper()
        if t not in disc_by_ticker:
            disc_by_ticker[t] = {"amount": 0.0, "is_csuite": False, "days": 999, "cik": row["cik"]}
        disc_by_ticker[t]["amount"]    += row["amount"]
        disc_by_ticker[t]["is_csuite"] |= row["is_csuite"]
        try:
            d = (today - date.fromisoformat(row["date"][:10])).days
            disc_by_ticker[t]["days"] = min(disc_by_ticker[t]["days"], d)
        except Exception:
            pass

    # Stap 2: CIK lookup voor portfolio tickers (die niet al in discovery zitten)
    print("[monitor] Stap 2: CIK lookup voor portefeuille-tickers...", file=sys.stderr)
    portfolio_without_cik = portfolio_set - disc_by_ticker.keys()
    cik_map: dict[str, str] = {}
    if portfolio_without_cik:
        cik_map = load_cik_map()

    # Bouw volledige ticker → CIK map
    all_tickers_cik: dict[str, str] = {}
    for t, info in disc_by_ticker.items():
        all_tickers_cik[t] = info["cik"]
    for t in portfolio_set:
        if t not in all_tickers_cik:
            if t in cik_map:
                all_tickers_cik[t] = cik_map[t]
            else:
                print(f"[warn] {t}: CIK niet gevonden — overgeslagen", file=sys.stderr)

    # Stap 3: 270d analyse voor alle tickers (portfolio + discovery)
    print(f"[monitor] Stap 3: 270d analyse van {len(all_tickers_cik)} tickers...", file=sys.stderr)
    results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(analyse_ticker, ticker, cik, args.days): ticker
            for ticker, cik in all_tickers_cik.items()
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                r = future.result()
                # Koppel discovery-info aan het resultaat
                if ticker in disc_by_ticker:
                    r["discovery"] = disc_by_ticker[ticker]
                r["in_portfolio"] = ticker in portfolio_set
                results[ticker] = r
                s = score(r)
                print(f"[monitor] {ticker}: {r['signal']} [{s}/10] — {r['reasons'][0] if r['reasons'] else ''}", file=sys.stderr)
            except Exception as e:
                print(f"[warn] {ticker}: analyse mislukt — {e}", file=sys.stderr)

    # Stap 4: Sorteer en splits
    portfolio_results = [r for r in results.values() if r.get("in_portfolio")]
    portfolio_results.sort(key=lambda x: x["ticker"])

    candidate_results = [
        r for r in results.values()
        if not r.get("in_portfolio") and r.get("signal") != "UNKNOWN"
    ]
    # Sorteer kandidaten: score desc, dan discovery-bedrag desc
    candidate_results.sort(key=lambda r: (
        -score(r),
        -(r.get("discovery") or {}).get("amount", r.get("total_buy", 0))
    ))
    top_candidates = candidate_results[:TOP_N]

    # Stap 5: Health check
    n_unknown = sum(1 for r in results.values() if r.get("signal") == "UNKNOWN")
    health_lines, alerts = health_check(
        output_dir, len(discoveries), n_unknown, len(results)
    )

    # Console output
    print("\n" + "=" * 60)
    print(f"INSIDER MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    print("\n--- PORTEFEUILLE ---")
    for r in portfolio_results:
        s = score(r)
        print(f"{score_emoji(s)} {r['ticker']:<6} {r['signal']:<22} [{s}/10]  {r.get('advies','?')}")
        for reason in r["reasons"][:2]:
            print(f"   ↳ {reason}")

    print("\n--- TOP KANDIDATEN ---")
    for r in top_candidates:
        s = score(r)
        disc = r.get("discovery")
        print(f"{score_emoji(s)} {r['ticker']:<6} {r['signal']:<22} [{s}/10]")
        if disc:
            csuite_tag = "C-suite" if disc["is_csuite"] else "Director"
            print(f"   Trigger: {_fmt_amount(disc['amount'])} ({disc['days']}d, {csuite_tag})")

    # Stap 6: JSON opslaan
    all_results = list(results.values())
    out_path = output_dir / "monitor.json"
    out_path.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"\n[monitor] JSON → {out_path}", file=sys.stderr)

    # Stap 7: Telegram
    if args.telegram:
        msg = build_telegram(portfolio_results, top_candidates, health_lines, alerts)
        ok  = send_telegram(msg)
        print(f"[monitor] Telegram: {'verstuurd ✓' if ok else 'MISLUKT ✗'}", file=sys.stderr)


if __name__ == "__main__":
    main()
