#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Portfolio Monitor — Exit-signaal detectie voor insider-gebaseerde posities.

Draait periodiek (2x/dag aanbevolen) en genereert:
- EXIT signaal als insider-effect is uitgewerkt (>90d geen buy)
- EXIT signaal als insiders beginnen te verkopen (netto flow negatief)
- HOLD signaal als insiders nog actief kopen
- ALERT als er nieuwe insider activiteit is

Gebruik:
  python3 scripts/portfolio_monitor.py --tickers ALMS BH BORR GO --days 270
  python3 scripts/portfolio_monitor.py --tickers ALMS BH BORR GO --days 270 --telegram
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

UA = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")
TIMEOUT = 30
RETRIES = 4
SLEEP = 0.35

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# Drempelwaarden
DAYS_STALE = 90          # Dagen zonder insider buy = signaal uitgewerkt
DAYS_FRESH = 30          # Dagen sinds laatste buy = vers signaal
NET_SELL_THRESHOLD = 0   # Netto flow < 0 = verkoopsignaal
MIN_BUY_AMOUNT = 50000   # Minimale koopwaarde om mee te tellen

# Tijdsdecay — gebaseerd op Lakonishok & Lee (2001): insider buy signaal
# is het sterkst in de eerste 0-6 maanden, daarna snel afnemend.
# Half-life van 90 dagen: na 90d weegt transactie nog 37%, na 180d 14%, na 270d 5%.
DECAY_HALFLIFE = 90      # Dagen (half-life exponentiële decay)

# Transactiecodes die GEEN negatief signaal zijn
# F = tax withholding bij RSU vesting (gedwongen, niet bewuste keuze)
# A = compensatie-award (geen markthandel)
# M = optie-uitoefening (neutrale mechanische actie)
NON_SIGNAL_SELL_CODES = {"F", "A", "M", "G", "W", "J"}

# Rol-gewichten (hoger = informatiever)
ROLE_WEIGHTS = {
    "ceo": 5, "chief executive": 5, "president": 4,
    "cfo": 4, "chief financial": 4,
    "coo": 4, "chief operating": 4,
    "cto": 3, "chief technology": 3,
    "clo": 3, "chief legal": 3,
    "evp": 3, "svp": 3,
    "vp": 2,
    "director": 2,
    "10%": 1, "owner": 1,
}


IPO_MIN_DAYS = 365  # Bedrijf moet minimaal 1 jaar genoteerd zijn


def is_recent_ipo(filing_data: dict) -> bool:
    """Check of een bedrijf recent naar de beurs is gegaan (< IPO_MIN_DAYS).

    Kijkt naar de oudste Form 4 filing — als die minder dan 1 jaar oud is,
    is het waarschijnlijk een recente IPO en zijn insider buys minder informatief.
    """
    recent = filing_data.get("filings", {}).get("recent", {})
    dates = recent.get("filingDate", [])
    if not dates:
        return True  # Geen data = voorzichtig, behandel als IPO

    try:
        oldest = min(dates)
        oldest_date = datetime.strptime(oldest, "%Y-%m-%d").date()
        days_listed = (datetime.now(timezone.utc).date() - oldest_date).days
        return days_listed < IPO_MIN_DAYS
    except Exception:
        return False


def fetch(url: str) -> str:
    for i in range(RETRIES):
        try:
            time.sleep(SLEEP)
            req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            time.sleep(min(8, 0.8 * (2 ** i)))
    return ""


def fetch_json(url: str):
    text = fetch(url)
    return json.loads(text) if text else {}


def role_weight(role: str) -> int:
    role_lower = (role or "").lower()
    best = 0
    for keyword, weight in ROLE_WEIGHTS.items():
        if keyword in role_lower:
            best = max(best, weight)
    return best if best > 0 else 1


def role_label(weight: int) -> str:
    if weight >= 5: return "C-SUITE"
    if weight >= 3: return "OFFICER"
    if weight >= 2: return "DIRECTOR"
    return "OTHER"


def analyze_ticker(ticker: str, days: int, ticker_map: dict) -> dict:
    """Analyseer insider activiteit voor één ticker via deep dive JSON output.

    Leest eerst bestaande deep dive data. Als die er niet is, gebruikt het de
    submissions API met index-pagina navigatie (zoals portfolio_deepdive_270d.py).
    """
    ticker = ticker.upper()

    # Probeer eerst bestaande deep dive JSON te laden
    reports_dir = Path("data/reports")
    buys = []
    sells = []

    # Zoek meest recente deep dive JSON die deze ticker bevat
    found_data = False
    for json_file in sorted(reports_dir.glob("deepdive_*.json"), reverse=True):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if ticker in [t.upper() for t in data.get("tickers", [])]:
                for tx in data.get("transactions", []):
                    if tx.get("ticker", "").upper() != ticker:
                        continue

                    tx_date = None
                    if tx.get("date"):
                        try:
                            tx_date = datetime.fromisoformat(str(tx["date"])).date()
                        except Exception:
                            tx_date = datetime.strptime(str(tx["date"])[:10], "%Y-%m-%d").date()

                    owner = tx.get("insider", "Unknown")
                    role_str = tx.get("role", "")
                    code = tx.get("code", "").upper()
                    buy_amt = float(tx.get("BUY", 0))
                    sell_amt = float(tx.get("SELL", 0))

                    entry = {
                        "date": tx_date,
                        "insider": owner,
                        "role": role_str,
                        "role_weight": role_weight(role_str),
                        "code": code,
                        "amount": buy_amt if code == "P" else sell_amt,
                    }

                    if code == "P" and buy_amt >= MIN_BUY_AMOUNT:
                        buys.append(entry)
                    elif code == "S" and sell_amt > 0 and code not in NON_SIGNAL_SELL_CODES:
                        sells.append(entry)

                found_data = True
                break
        except Exception:
            continue

    if not found_data:
        # Fallback: gebruik submissions API direct
        if ticker not in ticker_map:
            return {
            "ticker": ticker, "signal": "UNKNOWN", "reasons": ["Niet gevonden in SEC"],
            "total_buy": 0, "total_sell": 0, "net_flow": 0, "weighted_net": 0,
            "days_since_buy": 999, "unique_buyers": 0, "unique_sellers": 0,
            "csuite_buyers": [], "csuite_sellers": [],
            "last_buy_date": None, "last_sell_date": None,
            "buys_detail": [], "sells_detail": [],
        }

        cik = ticker_map[ticker]["cik_str"]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()

        subs = fetch_json(SUBMISSIONS_URL.format(cik=cik))

        # IPO check
        if is_recent_ipo(subs):
            return {
                "ticker": ticker, "signal": "HOLD", "reasons": [f"Recente IPO (<{IPO_MIN_DAYS}d) — insider buys minder informatief"],
                "total_buy": 0, "total_sell": 0, "net_flow": 0, "weighted_net": 0,
                "days_since_buy": 999, "unique_buyers": 0, "unique_sellers": 0,
                "csuite_buyers": [], "csuite_sellers": [],
                "last_buy_date": None, "last_sell_date": None,
                "buys_detail": [], "sells_detail": [],
                "is_ipo": True,
            }

        recent = subs.get("filings", {}).get("recent", {})
        n = len(recent.get("accessionNumber", []))

        form4_count = 0
        for i in range(n):
            form_type = recent.get("form", [""])[i]
            if form_type not in ("4", "4/A"):
                continue
            filing_date_str = recent.get("filingDate", [""])[i]
            try:
                filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d").date()
            except Exception:
                continue
            if filing_date < cutoff:
                continue
            form4_count += 1
            if form4_count > 60:
                break

            accession = recent["accessionNumber"][i]
            cik_plain = str(int(cik))
            acc_no = accession.replace("-", "")
            index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{acc_no}/{accession}-index.htm"

            page = fetch(index_url)
            if not page:
                continue

            # Zoek XML in index pagina
            xml = ""
            for m in re.finditer(r'href="([^"]+\.xml)"', page, re.I):
                href = m.group(1)
                url = href if href.startswith("http") else f"https://www.sec.gov{href}"
                candidate = fetch(url)
                if candidate and re.search(r"ownershipDocument", candidate, re.I):
                    xml = candidate
                    break

            if not xml:
                continue

            owner_m = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", xml)
            owner = owner_m.group(1).strip() if owner_m else "Unknown"

            role_m = re.search(r"<officerTitle>(.*?)</officerTitle>", xml)
            is_dir = re.search(r"<isDirector>(.*?)</isDirector>", xml)
            is_off = re.search(r"<isOfficer>(.*?)</isOfficer>", xml)
            is_ten = re.search(r"<isTenPercentOwner>(.*?)</isTenPercentOwner>", xml)

            role_str = ""
            if role_m:
                role_str = role_m.group(1).strip()
            else:
                parts = []
                if is_dir and is_dir.group(1).strip() == "1": parts.append("Director")
                if is_off and is_off.group(1).strip() == "1": parts.append("Officer")
                if is_ten and is_ten.group(1).strip() == "1": parts.append("10% Owner")
                role_str = ", ".join(parts)

            blocks = re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml, re.S)
            for b in blocks:
                code_m = re.search(r"<transactionCode>(.*?)</transactionCode>", b)
                shares_m = re.search(r"<transactionShares>.*?<value>(.*?)</value>", b, re.S)
                price_m = re.search(r"<transactionPricePerShare>.*?<value>(.*?)</value>", b, re.S)
                if not code_m:
                    continue
                code = code_m.group(1).strip().upper()
                # Sla compensatie/belasting transacties over — geen marktsignaal
                if code in NON_SIGNAL_SELL_CODES:
                    continue
                if code not in ("P", "S"):
                    continue
                try:
                    shares = float(shares_m.group(1)) if shares_m else 0
                    price = float(price_m.group(1)) if price_m else 0
                except (ValueError, TypeError):
                    continue
                total = shares * price
                entry = {
                    "date": filing_date,
                    "insider": owner,
                    "role": role_str,
                    "role_weight": role_weight(role_str),
                    "code": code,
                    "amount": total,
                }
                if code == "P" and total >= MIN_BUY_AMOUNT:
                    buys.append(entry)
                elif code == "S" and total > 0:
                    sells.append(entry)

    # Bereken signalen
    total_buy = sum(b["amount"] for b in buys)
    total_sell = sum(s["amount"] for s in sells)
    net_flow = total_buy - total_sell

    today = datetime.now(timezone.utc).date()

    def decay(tx_date) -> float:
        """Exponentiële tijdsdecay (Lakonishok & Lee 2001).
        Half-life = DECAY_HALFLIFE dagen: recente transacties wegen zwaarder.
        """
        if tx_date is None:
            return 0.0
        days_old = max(0, (today - tx_date).days)
        return math.exp(-days_old * math.log(2) / DECAY_HALFLIFE)

    # Tijdsgecorrigeerde gewogen score: rol_gewicht × decay
    # Een buy van gisteren (decay≈1.0) overstemt een sell van 9 maanden geleden (decay≈0.05)
    weighted_buy = sum(b["amount"] * b["role_weight"] * decay(b["date"]) for b in buys)
    weighted_sell = sum(s["amount"] * s["role_weight"] * decay(s["date"]) for s in sells)
    weighted_net = weighted_buy - weighted_sell

    # Laatste buy datum
    last_buy_date = max((b["date"] for b in buys), default=None)
    days_since_buy = (today - last_buy_date).days if last_buy_date else 999

    # Laatste sell datum
    last_sell_date = max((s["date"] for s in sells), default=None)

    # Unieke kopers en verkopers
    unique_buyers = set(b["insider"] for b in buys)
    unique_sellers = set(s["insider"] for s in sells)

    # C-suite kopers
    csuite_buyers = set(b["insider"] for b in buys if b["role_weight"] >= 4)
    csuite_sellers = set(s["insider"] for s in sells if s["role_weight"] >= 4)

    # Sells zijn al gefilterd op code F/A/M (compensatie/belasting) bij het inlezen.
    # Geen verdere cluster-heuristiek — dat maskeert mogelijk echte negatieve signalen.
    sells_clean = sells
    total_sell_clean = total_sell
    weighted_sell_clean = sum(s["amount"] * s["role_weight"] * decay(s["date"]) for s in sells_clean)
    weighted_net_clean = weighted_buy - weighted_sell_clean
    rsu_note = ""

    # Gebruik schoongemaakte sell-lijst (excl. compensatie/belasting codes F/A/M)
    net_flow_clean = total_buy - total_sell_clean
    csuite_sellers_clean = set(s["insider"] for s in sells_clean if s["role_weight"] >= 4)
    buyers_are_institutional_only = bool(buys) and all(b["role_weight"] <= 1 for b in buys)
    recent_buys = [b for b in buys if (today - b["date"]).days <= 14]
    recent_unique_buyers = len(set(b["insider"] for b in recent_buys))

    # ── Signaalclassificatie (5 niveaus) ──────────────────────────────────────
    #
    # 1. STERKE OVERTUIGING — C-suite koopt zelf, vers, geen netto sells
    # 2. POSITIEF SIGNAAL   — Vers buy, kleine voorbehouden
    # 3. GEMENGD SIGNAAL    — Conflicterende informatie (Cohen et al. 2012)
    # 4. NEGATIEF SIGNAAL   — C-suite verkoopt open market, netto dalend
    # 5. SIGNAAL UITGEWERKT — Geen recent buy >90d of netto sell domineert
    #
    # Volgorde: bepaal basisniveau op basis van recency, verfijn daarna op kwaliteit

    reasons = []

    # ── Stap 1: Basisniveau op recency ────────────────────────────────────────
    if days_since_buy <= DAYS_FRESH:
        signal = "POSITIEF SIGNAAL"
        reasons.append(f"Vers buy signaal ({days_since_buy}d geleden)")
    elif days_since_buy <= DAYS_STALE:
        signal = "POSITIEF SIGNAAL"
        reasons.append(f"Buy signaal actief ({days_since_buy}d geleden)")
    else:
        signal = "SIGNAAL UITGEWERKT"
        reasons.append(f"Geen insider buy in {days_since_buy}d")

    # ── Stap 2: Upgrade naar STERKE OVERTUIGING ───────────────────────────────
    # C-suite koopt zelf + vers + geen C-suite sells
    if (csuite_buyers and days_since_buy <= DAYS_FRESH
            and not csuite_sellers_clean and net_flow_clean > 0):
        signal = "STERKE OVERTUIGING"
        reasons.append(f"C-suite koopt: {', '.join(csuite_buyers)}")

    # Vers cluster: 3+ verschillende insiders kochten in 14 dagen
    if recent_unique_buyers >= 3 and days_since_buy <= DAYS_FRESH:
        signal = "STERKE OVERTUIGING"
        reasons.append(f"Vers cluster: {recent_unique_buyers} insiders kochten in 14d")

    # ── Stap 3: Downgrade naar GEMENGD SIGNAAL ────────────────────────────────
    # Cohen et al. (2012): institutionele koop + C-suite sell = conflicterend
    if csuite_sellers_clean and buyers_are_institutional_only:
        signal = "GEMENGD SIGNAAL"
        reasons.append(f"Institutioneel koopt, C-suite verkoopt ({', '.join(csuite_sellers_clean)})")

    # Netto sell maar nog verse buy activiteit = gemengd
    if net_flow_clean < NET_SELL_THRESHOLD and days_since_buy <= DAYS_FRESH:
        signal = "GEMENGD SIGNAAL"
        reasons.append(f"Netto sell ondanks verse buys: ${net_flow_clean:,.0f}")

    # ── Stap 4: Downgrade naar NEGATIEF SIGNAAL ───────────────────────────────
    # C-suite verkoopt open market, geen C-suite kopers
    if csuite_sellers_clean and not csuite_buyers and days_since_buy > DAYS_FRESH:
        signal = "NEGATIEF SIGNAAL"
        reasons.append(f"C-suite verkoopt open market: {', '.join(csuite_sellers_clean)}")

    # Gewogen netto negatief buiten vers buy window (Lakonishok & Lee)
    if weighted_net_clean < 0 and days_since_buy > DAYS_FRESH:
        signal = "NEGATIEF SIGNAAL"
        reasons.append("Gewogen netto negatief (sells zwaarder dan buys na decay)")

    # ── Stap 5: Downgrade naar SIGNAAL UITGEWERKT ─────────────────────────────
    # Netto sell en geen vers buy = duidelijk exit
    if net_flow_clean < NET_SELL_THRESHOLD and days_since_buy > DAYS_FRESH:
        signal = "SIGNAAL UITGEWERKT"
        reasons.append(f"Netto sell: ${net_flow_clean:,.0f}")

    advies = SIGNAL_ADVIES.get(signal, "MONITOREN")

    return {
        "ticker": ticker,
        "signal": signal,
        "advies": advies,
        "reasons": reasons,
        "total_buy": total_buy,
        "total_sell": total_sell,
        "total_sell_rsu_excl": total_sell_clean,
        "net_flow": total_buy - total_sell,
        "net_flow_clean": net_flow_clean,
        "weighted_net": weighted_net_clean,
        "days_since_buy": days_since_buy,
        "unique_buyers": len(unique_buyers),
        "unique_sellers": len(unique_sellers),
        "csuite_buyers": list(csuite_buyers),
        "csuite_sellers": list(csuite_sellers),
        "last_buy_date": last_buy_date.isoformat() if last_buy_date else None,
        "last_sell_date": last_sell_date.isoformat() if last_sell_date else None,
        "buys_detail": [{"insider": b["insider"], "role": b["role"], "role_label": role_label(b["role_weight"]), "amount": b["amount"], "date": b["date"].isoformat()} for b in sorted(buys, key=lambda x: x["date"], reverse=True)[:10]],
        "sells_detail": [{"insider": s["insider"], "role": s["role"], "role_label": role_label(s["role_weight"]), "amount": s["amount"], "date": s["date"].isoformat()} for s in sorted(sells, key=lambda x: x["date"], reverse=True)[:10]],
    }


def load_ticker_map() -> dict:
    data = fetch_json(TICKERS_URL)
    out = {}
    for _, v in data.items():
        t = str(v.get("ticker", "")).upper()
        if t:
            out[t] = {"ticker": t, "cik_str": str(v.get("cik_str", "")).zfill(10)}
    return out


SIGNAL_EMOJI = {
    "STERKE OVERTUIGING": "🟢🟢",
    "POSITIEF SIGNAAL":   "🟢",
    "GEMENGD SIGNAAL":    "🟡",
    "NEGATIEF SIGNAAL":   "🟠",
    "SIGNAAL UITGEWERKT": "🔴",
    "UNKNOWN":            "⚪",
}

def score_emoji(score: int) -> str:
    """Emoji op basis van score (1-10) — consistenter dan signaal-emoji."""
    if score >= 9:  return "🟢🟢🟢"
    if score >= 8:  return "🟢🟢"
    if score >= 6:  return "🟢"
    if score >= 4:  return "🟡"
    if score >= 2:  return "🟠"
    return "🔴"

# Advies voor een bestaande portefeuille-positie op basis van het signaal
SIGNAL_ADVIES = {
    "STERKE OVERTUIGING": "AANHOUDEN",
    "POSITIEF SIGNAAL":   "AANHOUDEN",
    "GEMENGD SIGNAAL":    "MONITOREN",
    "NEGATIEF SIGNAAL":   "VERKOPEN",
    "SIGNAAL UITGEWERKT": "VERKOPEN",
    "UNKNOWN":            "MONITOREN",
}

ADVIES_EMOJI = {
    "AANHOUDEN":  "✅",
    "MONITOREN":  "👁️",
    "VERKOPEN":   "❌",
}


def score_position(r: dict) -> int:
    """Score 1-10 voor een portfolio- of kandidaatpositie.

    Schaal is bewust streng zodat 10/10 uitzonderlijk is:
      9-10 : CEO koopt $5M+ binnen 7d, groot cluster, nul sells
      7-8  : Sterke overtuiging, C-suite, vers en cluster
      5-6  : Positief signaal met C-suite maar minder vers/groot
      3-4  : Positief signaal zonder C-suite of verouderd
      1-2  : Gemengd of zwak signaal
      0    : Negatief / uitgewerkt

    Ruwe punten worden genormaliseerd naar 1-10 (max ruwe score = 12).
    """
    raw = 0
    signal         = r.get("signal", "UNKNOWN")
    days           = r.get("days_since_buy", 999)
    csuite_buyers  = r.get("csuite_buyers", [])
    csuite_sellers = r.get("csuite_sellers", [])
    unique_buyers  = r.get("unique_buyers", 0)
    total_buy      = r.get("total_buy", 0)
    net_flow       = r.get("net_flow_clean", r.get("net_flow", 0))

    # ── Signaalsterkte (max 3) ─────────────────────────────────────────────
    raw += {
        "STERKE OVERTUIGING": 3,
        "POSITIEF SIGNAAL":   2,
        "GEMENGD SIGNAAL":    1,
        "NEGATIEF SIGNAAL":   0,
        "SIGNAAL UITGEWERKT": 0,
    }.get(signal, 0)

    # ── C-suite kwaliteit (max 2, min -3) ────────────────────────────────
    if csuite_buyers and not csuite_sellers:
        raw += 2
    elif csuite_buyers and csuite_sellers:
        raw += 0  # C-suite koopt én verkoopt = neutraal
    elif csuite_sellers:
        raw -= 3  # Alleen C-suite open market sell = sterk negatief signaal

    # ── Versheid (max 3) — strenger dan voorheen ──────────────────────────
    if days <= 7:
        raw += 3   # Extreem vers: CEO kocht gisteren
    elif days <= 14:
        raw += 2   # Vers
    elif days <= 30:
        raw += 1   # Actief
    # >30d: geen punten

    # ── Cluster (max 1) ───────────────────────────────────────────────────
    if unique_buyers >= 3 and days <= 30:
        raw += 1

    # ── Koopomvang (max 2) — absoluut bedrag als proxy voor overtuiging ───
    if total_buy >= 5_000_000:
        raw += 2   # $5M+ = uitzonderlijk
    elif total_buy >= 1_000_000:
        raw += 1   # $1M+ = significant

    # ── Netto positief zonder sells (max 1) ───────────────────────────────
    if net_flow > 0 and not csuite_sellers:
        raw += 1

    # ── Normaliseer naar 1-10 (max ruwe score = 12) ───────────────────────
    # score = round(raw / 12 * 10), minimaal 0, maximaal 10
    score = round(max(0, raw) / 12 * 10)
    return max(0, min(10, score))


def format_signal(r: dict) -> str:
    sig_emoji = SIGNAL_EMOJI.get(r["signal"], "⚪")
    adv_emoji = ADVIES_EMOJI.get(r.get("advies", ""), "")
    lines = [
        f"{sig_emoji} {r['ticker']} — {r['signal']}",
        f"  {' | '.join(r['reasons'])}",
        f"  Buy: ${r['total_buy']:,.0f} | Sell: ${r['total_sell']:,.0f} | Netto: ${r['net_flow']:,.0f}",
        f"  Kopers: {r['unique_buyers']} | Verkopers: {r['unique_sellers']}",
        f"  Laatste buy: {r.get('last_buy_date', '?')} ({r['days_since_buy']}d geleden)",
    ]
    if r.get("csuite_buyers"):
        lines.append(f"  C-suite kopers: {', '.join(r['csuite_buyers'])}")
    if r.get("csuite_sellers"):
        lines.append(f"  C-suite verkopers: {', '.join(r['csuite_sellers'])}")
    lines.append(f"  Advies: {adv_emoji} {r.get('advies', '?')}")
    return "\n".join(lines)


def send_telegram(message: str, bot_token: str, chat_id: str):
    """Verstuur bericht via Telegram Bot API."""
    import urllib.parse
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode()
    req = Request(url, data=data, method="POST")
    try:
        with urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"[warn] Telegram send fout: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Portfolio Monitor — Exit-signaal detectie")
    parser.add_argument("--tickers", nargs="+", required=True, help="Alle te analyseren tickers (portfolio + kandidaten)")
    parser.add_argument("--portfolio", nargs="+", default=[], help="Tickers die je daadwerkelijk bezit (subset van --tickers)")
    parser.add_argument("--days", type=int, default=270, help="Lookback periode in dagen")
    parser.add_argument("--telegram", action="store_true", help="Verstuur alerts via Telegram")
    parser.add_argument("--output-dir", default="data/reports", help="Output directory")
    args = parser.parse_args()

    # Als --portfolio niet opgegeven: behandel alle --tickers als portfolio (backward compat)
    portfolio_set = set(t.upper() for t in args.portfolio) if args.portfolio else set(t.upper() for t in args.tickers)

    print(f"[monitor] Start portfolio scan: {', '.join(args.tickers)}", file=sys.stderr)
    ticker_map = load_ticker_map()

    results = []
    for ticker in args.tickers:
        print(f"[monitor] Scan {ticker.upper()}...", file=sys.stderr)
        r = analyze_ticker(ticker, args.days, ticker_map)
        r["in_portfolio"] = ticker.upper() in portfolio_set
        results.append(r)
        print(format_signal(r))
        print()

    portfolio_results   = [r for r in results if r["in_portfolio"]]
    kandidaat_results   = [r for r in results if not r["in_portfolio"]]

    # Signaal volgorde voor sortering
    SIGNAL_ORDER = {
        "STERKE OVERTUIGING": 0, "POSITIEF SIGNAAL": 1,
        "GEMENGD SIGNAAL": 2, "NEGATIEF SIGNAAL": 3,
        "SIGNAAL UITGEWERKT": 4, "UNKNOWN": 5,
    }
    # Top kandidaten: altijd top 3 tonen ongeacht signaalsterkte
    top_kandidaten = sorted(
        kandidaat_results,
        key=lambda x: (SIGNAL_ORDER.get(x["signal"], 5), -x.get("net_flow", 0))
    )[:3]

    # Samenvatting console
    negatief = [r for r in portfolio_results if r["signal"] in ("NEGATIEF SIGNAAL", "SIGNAAL UITGEWERKT")]

    summary = f"\n{'='*50}\nPORTFOLIO MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*50}\n"
    summary += "\n--- PORTEFEUILLE ---\n"
    for r in portfolio_results:
        summary += format_signal(r) + "\n\n"
    if top_kandidaten:
        summary += "\n--- TOP KANDIDATEN (niet in bezit) ---\n"
        for r in top_kandidaten:
            summary += format_signal(r) + "\n\n"

    if negatief:
        summary += f"⚠️ AANDACHT VEREIST: {', '.join(r['ticker'] for r in negatief)}\n"
    else:
        summary += "✅ Portefeuille: geen negatieve signalen.\n"

    print(summary)

    # Schrijf JSON output
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / "portfolio_monitor.json"
    json_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"[monitor] JSON geschreven naar {json_path}", file=sys.stderr)

    # ── System health check ──────────────────────────────────────────────────
    health_lines = []
    health_alerts = []  # Kritieke meldingen die apart bovenaan komen

    # ── Persistente dagelijkse health log ────────────────────────────────────
    health_log_path = Path(args.output_dir) / "health_log.json"
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        health_log = json.loads(health_log_path.read_text(encoding="utf-8")) if health_log_path.exists() else []
    except Exception:
        health_log = []

    # Discovery: check of JSON bestaat en hoe oud het is
    discovery_json = Path(args.output_dir) / "discovery_openmarket.json"
    disc_ran_today = False
    n_disc = 0
    if discovery_json.exists():
        age_minutes = (time.time() - discovery_json.stat().st_mtime) / 60
        try:
            disc_data = json.loads(discovery_json.read_text(encoding="utf-8"))
            n_disc = len(disc_data)
        except Exception:
            n_disc = 0

        disc_ran_today = age_minutes < 120  # Binnen 2 uur = vandaag gedraaid

        if age_minutes < 120:
            if n_disc == 0:
                # 0 buys na verse run = verdacht — check log voor trend
                health_lines.append(f"🟡 Discovery: 0 buys na verse run ({int(age_minutes)}min geleden)")
            else:
                health_lines.append(f"🟢 Discovery: {n_disc} buys gevonden ({int(age_minutes)}min geleden)")
        elif age_minutes < 720:  # 12 uur = avondrun hergebruikt ochtendcache
            health_lines.append(f"🟡 Discovery: cache {int(age_minutes // 60)}u oud ({n_disc} buys)")
        else:
            health_lines.append(f"🔴 Discovery: verouderd ({int(age_minutes // 60)}u) — controleer launchd")
    else:
        health_lines.append("🔴 Discovery: geen output gevonden — run gefaald?")

    # ── Anomaly detection: opeenvolgende nul-dagen ───────────────────────────
    # Schrijf vandaag naar log
    today_entry = {"date": today_str, "discovery_count": n_disc, "ran": disc_ran_today}
    # Vervang entry voor vandaag als die al bestaat, anders voeg toe
    health_log = [e for e in health_log if e.get("date") != today_str]
    health_log.append(today_entry)
    health_log = sorted(health_log, key=lambda x: x["date"])[-30:]  # Bewaar 30 dagen
    try:
        health_log_path.write_text(json.dumps(health_log, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Tel opeenvolgende dagen met 0 discovery resultaten (alleen runs die wel draaiden)
    ran_entries = [e for e in reversed(health_log) if e.get("ran")]
    zero_streak = 0
    for e in ran_entries:
        if e.get("discovery_count", 0) == 0:
            zero_streak += 1
        else:
            break

    if zero_streak >= 3:
        health_alerts.append(
            f"🚨 <b>ANOMALIE:</b> {zero_streak} opeenvolgende dagen 0 discovery resultaten — "
            f"waarschijnlijk een bug of SEC-blokkade. Controleer discovery script!"
        )
    elif zero_streak >= 2:
        health_lines.append(f"⚠️ Discovery: al {zero_streak} dagen 0 buys — let op")

    # SEC: tel tickers waarbij analyse mislukte (UNKNOWN signaal = niet gevonden)
    unknown_tickers = [r["ticker"] for r in results if r.get("signal") == "UNKNOWN"]
    if unknown_tickers:
        health_lines.append(f"🟡 SEC: {len(unknown_tickers)} ticker(s) niet gevonden: {', '.join(unknown_tickers)}")
    else:
        health_lines.append(f"🟢 SEC: alle {len(results)} tickers geanalyseerd")

    # Deep dive: check of er recente deepdive JSON's zijn
    deepdive_files = sorted(Path(args.output_dir).glob("deepdive_*.json"), reverse=True)
    if deepdive_files:
        dd_age = (time.time() - deepdive_files[0].stat().st_mtime) / 3600
        if dd_age < 12:
            health_lines.append(f"🟢 Deep dive: {len(deepdive_files)} bestanden (laatste {int(dd_age*60)}min geleden)")
        else:
            health_lines.append(f"🟡 Deep dive: laatste bestand {int(dd_age)}u oud")
    else:
        health_lines.append("🟡 Deep dive: geen bestanden — fallback naar submissions API")

    # Telegram
    if args.telegram:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            print("[warn] TELEGRAM_BOT_TOKEN en TELEGRAM_CHAT_ID env vars vereist", file=sys.stderr)
        else:
            tg_message = f"<b>📊 Insider Monitor</b> — {datetime.now().strftime('%d %b %H:%M')}\n"
            tg_message += "─" * 28 + "\n\n"

            # ── Kritieke systeemwaarschuwingen bovenaan ───────────────────────
            if health_alerts:
                for alert in health_alerts:
                    tg_message += f"{alert}\n\n"
                tg_message += "─" * 28 + "\n\n"

            # ── Sectie 1: Jouw portefeuille ──────────────────────────────────
            tg_message += "<b>💼 PORTEFEUILLE</b>\n"
            for r in portfolio_results:
                pos_score = score_position(r)
                sig_emoji = score_emoji(pos_score)
                adv_emoji = ADVIES_EMOJI.get(r.get("advies", ""), "")
                tg_message += f"{sig_emoji} <b>{r['ticker']}</b> — {r['signal']} [{pos_score}/10]\n"
                tg_message += f"  Netto: ${r['net_flow']:,.0f} | {r['days_since_buy']}d geleden\n"
                if r.get("reasons"):
                    tg_message += f"  ↳ {r['reasons'][0]}\n"
                tg_message += f"  {adv_emoji} <b>{r.get('advies', '?')}</b>\n\n"

            # Waarschuwing als actie nodig
            if negatief:
                tg_message += f"⚠️ <b>ACTIE VEREIST:</b> {', '.join(r['ticker'] for r in negatief)} — overweeg verkopen\n\n"
            else:
                tg_message += "✅ Alle posities stabiel\n\n"

            # ── Sectie 2: Top 3 kandidaten (altijd tonen) ────────────────────
            tg_message += "─" * 28 + "\n"
            tg_message += "<b>🔍 TOP 3 KANDIDATEN</b>\n"
            if top_kandidaten:
                for r in top_kandidaten:
                    cand_score = score_position(r)
                    sig_emoji = score_emoji(cand_score)
                    tg_message += f"{sig_emoji} <b>{r['ticker']}</b> — {r['signal']} [{cand_score}/10]\n"
                    tg_message += f"  Netto: ${r['net_flow']:,.0f} | {r['days_since_buy']}d geleden\n"
                    if r.get("reasons"):
                        tg_message += f"  ↳ {r['reasons'][0]}\n"
                    if r["signal"] in ("STERKE OVERTUIGING", "POSITIEF SIGNAAL"):
                        tg_message += f"  💡 Onderzoeken als instapkandidaat\n\n"
                    else:
                        tg_message += f"  ⏸ Signaal te zwak voor instap\n\n"
            else:
                tg_message += "  Geen kandidaten gevonden vandaag\n\n"

            # ── Sectie 3: Systeem health check ───────────────────────────────
            tg_message += "─" * 28 + "\n"
            tg_message += "<b>⚙️ SYSTEEM STATUS</b>\n"
            for line in health_lines:
                tg_message += f"  {line}\n"
            tg_message += "\n"

            send_telegram(tg_message, bot_token, chat_id)
            print("[monitor] Telegram bericht verstuurd", file=sys.stderr)


if __name__ == "__main__":
    main()
