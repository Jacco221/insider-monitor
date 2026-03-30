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
                    elif code == "S" and sell_amt > 0:
                        sells.append(entry)

                found_data = True
                break
        except Exception:
            continue

    if not found_data:
        # Fallback: gebruik submissions API direct
        if ticker not in ticker_map:
            return {"ticker": ticker, "signal": "UNKNOWN", "reasons": ["Niet gevonden in SEC"]}

        cik = ticker_map[ticker]["cik_str"]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()

        subs = fetch_json(SUBMISSIONS_URL.format(cik=cik))
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

    # Weighted buy score (C-suite weegt zwaarder)
    weighted_buy = sum(b["amount"] * b["role_weight"] for b in buys)
    weighted_sell = sum(s["amount"] * s["role_weight"] for s in sells)
    weighted_net = weighted_buy - weighted_sell

    # Laatste buy datum
    last_buy_date = max((b["date"] for b in buys), default=None)
    days_since_buy = (datetime.now(timezone.utc).date() - last_buy_date).days if last_buy_date else 999

    # Laatste sell datum
    last_sell_date = max((s["date"] for s in sells), default=None)

    # Unieke kopers en verkopers
    unique_buyers = set(b["insider"] for b in buys)
    unique_sellers = set(s["insider"] for s in sells)

    # C-suite kopers
    csuite_buyers = set(b["insider"] for b in buys if b["role_weight"] >= 4)
    csuite_sellers = set(s["insider"] for s in sells if s["role_weight"] >= 4)

    # Signaal bepalen
    signal = "HOLD"
    reasons = []

    if days_since_buy <= DAYS_FRESH:
        signal = "STRONG_HOLD"
        reasons.append(f"Vers buy signaal ({days_since_buy}d geleden)")
    elif days_since_buy <= DAYS_STALE:
        signal = "HOLD"
        reasons.append(f"Buy signaal actief ({days_since_buy}d geleden)")
    else:
        signal = "EXIT"
        reasons.append(f"Insider buy uitgewerkt ({days_since_buy}d geleden)")

    if net_flow < NET_SELL_THRESHOLD:
        signal = "EXIT"
        reasons.append(f"Netto sell: ${net_flow:,.0f}")

    if weighted_net < 0:
        signal = "EXIT"
        reasons.append(f"Gewogen netto negatief (C-suite sells)")

    if csuite_sellers and not csuite_buyers:
        signal = "EXIT"
        reasons.append(f"C-suite verkoopt, koopt niet")

    if csuite_buyers and days_since_buy <= DAYS_FRESH:
        signal = "STRONG_HOLD"
        reasons.append(f"C-suite koopt actief: {', '.join(csuite_buyers)}")

    # Nieuw cluster detectie
    recent_buys = [b for b in buys if (datetime.now(timezone.utc).date() - b["date"]).days <= 14]
    if len(set(b["insider"] for b in recent_buys)) >= 3:
        signal = "STRONG_HOLD"
        reasons.append("Vers cluster (3+ insiders in 14d)")

    return {
        "ticker": ticker,
        "signal": signal,
        "reasons": reasons,
        "total_buy": total_buy,
        "total_sell": total_sell,
        "net_flow": net_flow,
        "weighted_net": weighted_net,
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


def format_signal(r: dict) -> str:
    emoji = {"STRONG_HOLD": "🟢", "HOLD": "🟡", "EXIT": "🔴", "UNKNOWN": "⚪"}.get(r["signal"], "⚪")
    lines = [
        f"{emoji} {r['ticker']} — {r['signal']}",
        f"  Redenen: {'; '.join(r['reasons'])}",
        f"  Buy: ${r['total_buy']:,.0f} | Sell: ${r['total_sell']:,.0f} | Netto: ${r['net_flow']:,.0f}",
        f"  Kopers: {r['unique_buyers']} | Verkopers: {r['unique_sellers']}",
        f"  Laatste buy: {r.get('last_buy_date', '?')} ({r['days_since_buy']}d geleden)",
    ]
    if r.get("csuite_buyers"):
        lines.append(f"  C-suite kopers: {', '.join(r['csuite_buyers'])}")
    if r.get("csuite_sellers"):
        lines.append(f"  C-suite verkopers: {', '.join(r['csuite_sellers'])}")
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
    parser.add_argument("--tickers", nargs="+", required=True, help="Tickers in portfolio")
    parser.add_argument("--days", type=int, default=270, help="Lookback periode in dagen")
    parser.add_argument("--telegram", action="store_true", help="Verstuur alerts via Telegram")
    parser.add_argument("--output-dir", default="data/reports", help="Output directory")
    args = parser.parse_args()

    print(f"[monitor] Start portfolio scan: {', '.join(args.tickers)}", file=sys.stderr)
    ticker_map = load_ticker_map()

    results = []
    for ticker in args.tickers:
        print(f"[monitor] Scan {ticker.upper()}...", file=sys.stderr)
        r = analyze_ticker(ticker, args.days, ticker_map)
        results.append(r)
        print(format_signal(r))
        print()

    # Samenvatting
    exits = [r for r in results if r["signal"] == "EXIT"]
    holds = [r for r in results if r["signal"] in ("HOLD", "STRONG_HOLD")]

    summary = f"\n{'='*50}\nPORTFOLIO MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*50}\n"
    for r in results:
        summary += format_signal(r) + "\n\n"

    if exits:
        summary += f"⚠️ EXIT SIGNALEN: {', '.join(r['ticker'] for r in exits)}\n"
    if not exits:
        summary += "✅ Geen exit-signalen. Portfolio intact.\n"

    print(summary)

    # Schrijf JSON output
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / "portfolio_monitor.json"
    json_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"[monitor] JSON geschreven naar {json_path}", file=sys.stderr)

    # Telegram
    if args.telegram:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            print("[warn] TELEGRAM_BOT_TOKEN en TELEGRAM_CHAT_ID env vars vereist", file=sys.stderr)
        else:
            # Stuur alleen als er exit-signalen zijn, of 1x per dag samenvatting
            tg_message = f"<b>📊 Insider Monitor</b>\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            for r in results:
                emoji = {"STRONG_HOLD": "🟢", "HOLD": "🟡", "EXIT": "🔴"}.get(r["signal"], "⚪")
                tg_message += f"{emoji} <b>{r['ticker']}</b> — {r['signal']}\n"
                tg_message += f"  Netto: ${r['net_flow']:,.0f} | Laatste buy: {r['days_since_buy']}d\n"
                if r.get("reasons"):
                    tg_message += f"  → {r['reasons'][0]}\n"
                tg_message += "\n"

            if exits:
                tg_message += f"⚠️ <b>ACTIE VEREIST:</b> {', '.join(r['ticker'] for r in exits)}\n"

            send_telegram(tg_message, bot_token, chat_id)
            print("[monitor] Telegram bericht verstuurd", file=sys.stderr)


if __name__ == "__main__":
    main()
