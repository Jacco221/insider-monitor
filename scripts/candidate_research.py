#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Candidate Research — Diepgaande analyse van STERKE OVERTUIGING kandidaten.

Wanneer de discovery pipeline een sterke kandidaat vindt, doet dit script
automatisch dieper onderzoek en stuurt een Telegram bericht met aanbeveling.

Gebruik:
  python3 scripts/candidate_research.py \
      --monitor-json data/reports/portfolio_monitor.json \
      --portfolio BH BORR GO IPX

  python3 scripts/candidate_research.py \
      --tickers AHCO SBSW \
      --portfolio BH BORR GO IPX \
      --telegram
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# ── Constanten ────────────────────────────────────────────────────────────────

UA_SEC = os.getenv("SEC_USER_AGENT", "InsiderMonitor/1.0 (contact: you@example.com)")
UA_YF  = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

TIMEOUT        = 30
RETRIES        = 3
SLEEP_SEC      = 0.35   # SEC rate-limit (max 10 req/s, wij doen ~3/s)
SLEEP_YF       = 0.5    # Yahoo Finance rate-limit

MIN_SCORE      = 6      # Minimale score voor Telegram bericht
TARGET_SIGNAL  = "STERKE OVERTUIGING"

YF_CHART_URL   = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range={range}"
YF_CHART_WK    = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1wk&range=1y"
YF_NEWS_URL    = "https://query1.finance.yahoo.com/v1/finance/search?q={ticker}&newsCount=3"

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def fetch(url: str, ua: str = UA_SEC, sleep: float = SLEEP_SEC) -> str:
    """Haal URL op met retries en exponential backoff. Geeft lege string bij fout."""
    for attempt in range(RETRIES):
        try:
            time.sleep(sleep)
            req = Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
            with urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            if attempt < RETRIES - 1:
                time.sleep(min(8, 0.8 * (2 ** attempt)))
            else:
                print(f"[warn] fetch({url[:80]}): {e}", file=sys.stderr)
    return ""


def fetch_json(url: str, ua: str = UA_SEC, sleep: float = SLEEP_SEC) -> dict | list:
    text = fetch(url, ua=ua, sleep=sleep)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


# ── Yahoo Finance data ─────────────────────────────────────────────────────────

def get_market_data(ticker: str) -> dict:
    """
    Haal uitgebreide marktdata op via Yahoo Finance.

    Geeft terug:
      market_cap        : float | None
      price_now         : float | None
      week52_high       : float | None   — 52-weeks hoogste koers
      week52_low        : float | None   — 52-weeks laagste koers
      pct_from_52w_high : float | None   — % onder 52-weeks high (negatief = onder de top)
      pct_from_52w_low  : float | None   — % boven 52-weeks low
      pct_change_4w     : float | None   — prijsverandering afgelopen 4 weken
      pct_change_3m     : float | None   — prijsverandering afgelopen 3 maanden
      trend_4w          : str            — "STIJGEND" | "DALEND" | "ZIJWAARTS"
      insider_vs_price  : str | None     — inschatting of insiders kochten na run-up of bij bodem
    """
    result: dict = {
        "market_cap":        None,
        "price_now":         None,
        "week52_high":       None,
        "week52_low":        None,
        "pct_from_52w_high": None,
        "pct_from_52w_low":  None,
        "pct_change_4w":     None,
        "pct_change_3m":     None,
        "trend_4w":          "ONBEKEND",
        # legacy field — behoud voor score_candidate
        "pct_change_30d":    None,
    }

    # ── 1d: huidige prijs, marktcap, 52-weeks range ───────────────────────────
    url_1d = YF_CHART_URL.format(ticker=ticker, range="1d")
    data_1d = fetch_json(url_1d, ua=UA_YF, sleep=SLEEP_YF)
    try:
        meta = data_1d["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        result["price_now"]   = price
        result["week52_high"] = meta.get("fiftyTwoWeekHigh")
        result["week52_low"]  = meta.get("fiftyTwoWeekLow")
        shares = meta.get("sharesOutstanding")
        if shares and price:
            result["market_cap"] = shares * price
        if price and result["week52_high"] and result["week52_high"] > 0:
            result["pct_from_52w_high"] = (price - result["week52_high"]) / result["week52_high"] * 100
        if price and result["week52_low"] and result["week52_low"] > 0:
            result["pct_from_52w_low"]  = (price - result["week52_low"])  / result["week52_low"]  * 100
    except (KeyError, IndexError, TypeError):
        pass

    # ── 1y weekgrafiek: 4-weeks en 3-maands trend ────────────────────────────
    url_1y = YF_CHART_WK.format(ticker=ticker)
    data_1y = fetch_json(url_1y, ua=UA_YF, sleep=SLEEP_YF)
    try:
        closes = data_1y["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        valid  = [(i, c) for i, c in enumerate(closes) if c is not None]
        if valid:
            # Huidige prijs (meest recente week)
            if result["price_now"] is None:
                result["price_now"] = valid[-1][1]

            price_now = result["price_now"]

            # 4-weeks trend: vergelijk prijs nu met 4 weken geleden (~4 wekelijkse bars)
            if len(valid) >= 5:
                price_4w_ago = valid[-5][1]
                result["pct_change_4w"] = (price_now - price_4w_ago) / price_4w_ago * 100
                result["pct_change_30d"] = result["pct_change_4w"]   # legacy alias

                if result["pct_change_4w"] > 3:
                    result["trend_4w"] = "STIJGEND"
                elif result["pct_change_4w"] < -3:
                    result["trend_4w"] = "DALEND"
                else:
                    result["trend_4w"] = "ZIJWAARTS"

            # 3-maands trend (~13 wekelijkse bars)
            if len(valid) >= 14:
                price_3m_ago = valid[-14][1]
                result["pct_change_3m"] = (price_now - price_3m_ago) / price_3m_ago * 100
    except (KeyError, IndexError, TypeError):
        pass

    return result


def get_news_headlines(ticker: str) -> list[str]:
    """Haal maximaal 3 recente nieuwskoppen op via Yahoo Finance search."""
    url = YF_NEWS_URL.format(ticker=urllib.parse.quote(ticker))
    data = fetch_json(url, ua=UA_YF, sleep=SLEEP_YF)

    headlines = []
    try:
        news_items = data.get("news", [])
        for item in news_items[:3]:
            title = item.get("title", "").strip()
            if title:
                headlines.append(title)
    except (AttributeError, TypeError):
        pass
    return headlines


# ── Deepdive JSON reader ───────────────────────────────────────────────────────

def load_deepdive_for_ticker(ticker: str, reports_dir: Path) -> dict:
    """
    Laad insider detail-data voor een ticker uit de meest recente deepdive JSON.

    Geeft terug:
      buys_detail   : list van dicts met insider/role/amount/date
      sells_detail  : list
      total_buy     : float
      total_sell    : float
      unique_buyers : int
      csuite_buyers : list[str]
      last_buy_date : str | None   (ISO date string)
      days_since_buy: int
    """
    ticker = ticker.upper()
    empty = {
        "buys_detail":    [],
        "sells_detail":   [],
        "total_buy":      0.0,
        "total_sell":     0.0,
        "unique_buyers":  0,
        "csuite_buyers":  [],
        "last_buy_date":  None,
        "days_since_buy": 999,
    }

    for json_file in sorted(reports_dir.glob("deepdive_*.json"), reverse=True):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            tickers_in_file = [t.upper() for t in data.get("tickers", [])]
            if ticker not in tickers_in_file:
                continue

            buys, sells = [], []
            for tx in data.get("transactions", []):
                if tx.get("ticker", "").upper() != ticker:
                    continue
                code = tx.get("code", "").upper()
                if code not in ("P", "S"):
                    continue
                try:
                    tx_date = datetime.strptime(str(tx.get("date", ""))[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
                amt = float(tx.get("BUY", 0) if code == "P" else tx.get("SELL", 0))
                entry = {
                    "insider": tx.get("insider", "Unknown"),
                    "role":    tx.get("role", ""),
                    "amount":  amt,
                    "date":    tx_date.isoformat(),
                }
                if code == "P" and amt >= 50_000:
                    buys.append(entry)
                elif code == "S" and amt > 0:
                    sells.append(entry)

            today = datetime.now(timezone.utc).date()

            # Laatste buy datum + days_since
            buy_dates = [datetime.strptime(b["date"], "%Y-%m-%d").date() for b in buys]
            last_buy = max(buy_dates) if buy_dates else None
            days_since = (today - last_buy).days if last_buy else 999

            # C-suite kopers (role_weight >= 4: CEO/CFO/President/COO)
            csuite_roles = {"ceo", "chief executive", "president", "cfo", "chief financial",
                            "coo", "chief operating"}
            csuite = list({
                b["insider"] for b in buys
                if any(kw in b["role"].lower() for kw in csuite_roles)
            })

            return {
                "buys_detail":    sorted(buys,  key=lambda x: x["date"], reverse=True),
                "sells_detail":   sorted(sells, key=lambda x: x["date"], reverse=True),
                "total_buy":      sum(b["amount"] for b in buys),
                "total_sell":     sum(s["amount"] for s in sells),
                "unique_buyers":  len({b["insider"] for b in buys}),
                "csuite_buyers":  csuite,
                "last_buy_date":  last_buy.isoformat() if last_buy else None,
                "days_since_buy": days_since,
            }
        except Exception:
            continue

    return empty


# ── Scoringsmodel ──────────────────────────────────────────────────────────────

CSUITE_ROLES = {"ceo", "chief executive", "president", "cfo", "chief financial",
                "coo", "chief operating"}


def _is_csuite(role: str) -> bool:
    role_l = role.lower()
    return any(kw in role_l for kw in CSUITE_ROLES)


def score_emoji(score: int) -> str:
    """Emoji op basis van score (1-10) — zelfde schaal als portfolio_monitor.py."""
    if score >= 9:  return "🟢🟢🟢"
    if score >= 8:  return "🟢🟢"
    if score >= 6:  return "🟢"
    if score >= 4:  return "🟡"
    if score >= 2:  return "🟠"
    return "🔴"


def score_candidate(
    ticker: str,
    insider_data: dict,
    market_data: dict,
) -> tuple[int, list[str]]:
    """
    Score 1-10 voor een nieuwe kandidaat. Zelfde formule als score_position()
    in portfolio_monitor.py (max ruwe score = 12) zodat scores consistent zijn.

      9-10 : CEO koopt $5M+ binnen 7d, groot cluster, nul sells
      7-8  : Sterke overtuiging, vers, significante buy
      5-6  : Positief signaal, minder vers of kleiner
      3-4  : Positief zonder C-suite of verouderd
      1-2  : Zwak signaal

    Ruwe punten genormaliseerd naar 1-10 (max raw = 12).
    """
    raw    = 0
    redenen: list[str] = []

    buys_detail   = insider_data.get("buys_detail", [])
    sells_detail  = insider_data.get("sells_detail", [])
    total_buy     = insider_data.get("total_buy", 0.0)
    total_sell    = insider_data.get("total_sell", 0.0)
    unique_buyers = insider_data.get("unique_buyers", 0)
    csuite_buyers = insider_data.get("csuite_buyers", [])
    days_since    = insider_data.get("days_since_buy", 999)
    market_cap    = market_data.get("market_cap")

    # ── Signaalsterkte (max 3) — afgeleid van buy/sell verhouding ─────────────
    if total_buy > 0 and total_sell < total_buy * 0.2:
        raw += 3
        redenen.append("Sterke overtuiging: buys domineren")
    elif total_buy > 0 and total_buy >= total_sell:
        raw += 2
        redenen.append("Positief signaal: meer buys dan sells")
    elif total_buy > 0:
        raw += 1
        redenen.append("Gemengd signaal: buys én sells aanwezig")

    # ── C-suite koper (max 2, min -3) ─────────────────────────────────────────
    csuite_in_detail = [b for b in buys_detail if _is_csuite(b.get("role", ""))]
    csuite_sellers_d = [b for b in sells_detail if _is_csuite(b.get("role", ""))]
    all_csuite_buy   = list({b["insider"] for b in csuite_in_detail} | set(csuite_buyers))
    all_csuite_sell  = [b["insider"] for b in csuite_sellers_d]

    if all_csuite_buy and not all_csuite_sell:
        raw += 2
        redenen.append(f"C-suite koper(s): {', '.join(all_csuite_buy[:3])}")
    elif all_csuite_buy and all_csuite_sell:
        redenen.append("C-suite koopt én verkoopt — gemengd")
    elif all_csuite_sell:
        raw -= 3
        redenen.append(f"C-suite verkoopt: {', '.join(all_csuite_sell[:2])}")

    # ── Versheid (max 3) ──────────────────────────────────────────────────────
    if days_since <= 7:
        raw += 3
        redenen.append(f"Extreem vers: {days_since}d geleden")
    elif days_since <= 14:
        raw += 2
        redenen.append(f"Vers signaal: {days_since}d geleden")
    elif days_since <= 30:
        raw += 1
        redenen.append(f"Recent signaal: {days_since}d geleden")

    # ── Cluster ≥ 3 kopers ≤ 30d (max 1) ─────────────────────────────────────
    if unique_buyers >= 3 and days_since <= 30:
        raw += 1
        redenen.append(f"Koop-cluster: {unique_buyers} unieke insiders")

    # ── Koopomvang (max 2) — marktcap% indien beschikbaar, anders absoluut ────
    if market_cap and market_cap > 0 and total_buy > 0:
        pct = total_buy / market_cap * 100
        if pct >= 1.0:
            raw += 2
            redenen.append(f"Grote buy: {pct:.2f}% van marktcap")
        elif pct >= 0.1:
            raw += 1
            redenen.append(f"Significante buy: {pct:.2f}% van marktcap")
    elif total_buy > 0:
        if total_buy >= 5_000_000:
            raw += 2
            redenen.append(f"Grote absolute buy: ${total_buy/1e6:.1f}M")
        elif total_buy >= 1_000_000:
            raw += 1
            redenen.append(f"Significante buy: ${total_buy/1e6:.1f}M")

    # ── Netto positief (max 1) ────────────────────────────────────────────────
    if total_buy > total_sell and not all_csuite_sell:
        raw += 1
        redenen.append("Netto koopdruk")

    # Normaliseer naar 1-10 (max ruwe score = 12)
    score = round(max(0, raw) / 12 * 10)
    return max(0, min(10, score)), redenen


# ── Telegram formatting ───────────────────────────────────────────────────────

def format_telegram_message(
    ticker: str,
    insider_data: dict,
    market_data: dict,
    score: int,
    score_redenen: list[str],
    news_headlines: list[str],
    weakest_position: dict | None = None,
    portfolio_positions: list[dict] | None = None,
) -> str:
    """Formateer het Telegram bericht conform de opgegeven template."""
    today_str       = datetime.now().strftime("%d %b %Y")
    total_buy       = insider_data.get("total_buy", 0.0)
    buys_detail     = insider_data.get("buys_detail", [])
    last_buy_date   = insider_data.get("last_buy_date")
    days_since      = insider_data.get("days_since_buy", 999)
    market_cap      = market_data.get("market_cap")
    pct_change_30d  = market_data.get("pct_change_30d")

    # Insider buy % van marktcap
    pct_mcap_str = "n.v.t."
    if market_cap and market_cap > 0 and total_buy > 0:
        pct_mcap = total_buy / market_cap * 100
        pct_mcap_str = f"{pct_mcap:.2f}%"

    # Kopers samenvatting (max 3)
    buyer_parts = []
    seen: set[str] = set()
    for b in buys_detail[:10]:
        name = b.get("insider", "Unknown")
        if name in seen:
            continue
        seen.add(name)
        role = b.get("role", "")
        label = f"{name}"
        if role:
            label += f" ({role})"
        buyer_parts.append(label)
        if len(buyer_parts) >= 3:
            break
    kopers_str = "\n    ".join(buyer_parts) if buyer_parts else "Onbekend"

    # Datum laatste buy
    if last_buy_date:
        last_buy_str = last_buy_date
    else:
        last_buy_str = "onbekend"

    # Prijsdata opbouwen
    price_now         = market_data.get("price_now")
    week52_high       = market_data.get("week52_high")
    week52_low        = market_data.get("week52_low")
    pct_from_52w_high = market_data.get("pct_from_52w_high")
    pct_from_52w_low  = market_data.get("pct_from_52w_low")
    pct_change_4w     = market_data.get("pct_change_4w")
    pct_change_3m     = market_data.get("pct_change_3m")
    trend_4w          = market_data.get("trend_4w", "ONBEKEND")

    trend_emoji = {"STIJGEND": "📈", "DALEND": "📉", "ZIJWAARTS": "➡️"}.get(trend_4w, "❓")

    def _fmt_pct(v):
        return f"{v:+.1f}%" if v is not None else "n.v.t."

    def _fmt_price(v):
        return f"${v:.2f}" if v is not None else "n.v.t."

    # Beoordeling: koopt insider na run-up of dicht bij bodem?
    price_context = ""
    if pct_from_52w_high is not None and pct_from_52w_low is not None:
        range_pos = pct_from_52w_low / (pct_from_52w_low - pct_from_52w_high) * 100 if (pct_from_52w_low - pct_from_52w_high) != 0 else 50
        if pct_from_52w_high > -15:
            price_context = "⚠️ Nabij 52w top — insiders kopen na run-up"
        elif range_pos < 30:
            price_context = "✅ Dicht bij 52w bodem — insiders kopen bij lage koers"
        else:
            price_context = "➡️ Middenbereik 52w range"

    # Onderbouwing regels
    onderbouwing = "\n".join(f"  • {r}" for r in score_redenen) if score_redenen else "  • (geen data)"

    lines = [
        f"🔬 <b>Kandidaat Analyse — {ticker}</b>",
        f"{today_str}",
        "",
        f"📊 Signaal: STERKE OVERTUIGING",
        f"💰 Insider buys: ${total_buy:,.0f} ({pct_mcap_str} van marktcap)",
        f"👤 Kopers:",
        f"    {kopers_str}",
        f"📅 Meest recent: {last_buy_str} ({days_since}d geleden)",
        "",
        f"<b>📊 Prijsanalyse:</b>",
        f"  Huidig:    {_fmt_price(price_now)}",
        f"  52w hoog:  {_fmt_price(week52_high)} ({_fmt_pct(pct_from_52w_high)} van top)",
        f"  52w laag:  {_fmt_price(week52_low)} ({_fmt_pct(pct_from_52w_low)} boven bodem)",
        f"  Trend 4w:  {trend_emoji} {trend_4w} ({_fmt_pct(pct_change_4w)})",
        f"  Trend 3m:  {_fmt_pct(pct_change_3m)}",
        f"  {price_context}",
        f"🎯 Score: {score}/10",
        "",
        "💡 <b>Onderbouwing:</b>",
        onderbouwing,
    ]

    # Nieuws headlines (optioneel)
    if news_headlines:
        lines.append("")
        lines.append("📰 <b>Recent nieuws:</b>")
        for h in news_headlines[:3]:
            # Kap lange headlines af op 100 tekens
            h_short = h[:100] + ("…" if len(h) > 100 else "")
            lines.append(f"  • {h_short}")

    # ── Vergelijking met portfolio ─────────────────────────────────────────────
    if portfolio_positions:
        lines.append("")
        lines.append("<b>⚖️ Vergelijking met portfolio:</b>")
        # Kandidaat bovenaan
        lines.append(f"  {'★':2} {ticker:<6} score {score}/10  ← kandidaat")
        for p in sorted(portfolio_positions, key=lambda x: x["pos_score"], reverse=True):
            pt  = p.get("ticker", "?")
            ps  = p.get("pos_score", 0)
            emo = score_emoji(ps)
            better = "✅ zwakker" if ps < score else ("➡️ gelijk" if ps == score else "⬆️ sterker")
            lines.append(f"  {emo} {pt:<6} score {ps}/10  {better}")

    # ── Advies en herbalancering ───────────────────────────────────────────────
    lines.append("")
    beaten = [p for p in (portfolio_positions or []) if p.get("pos_score", 0) < score]

    if score >= 8 and beaten:
        sell_target = beaten[0]  # zwakste positie die kandidaat verslaat
        lines.append(f"✅ <b>AANBEVELING: Neem {ticker} op in portfolio</b>")
        lines.append(
            f"↔️ Verkoop <b>{sell_target['ticker']}</b> (score {sell_target['pos_score']}/10)"
            f" — {ticker} is sterker"
        )
    elif score >= 8:
        lines.append(f"✅ <b>AANBEVELING: Neem {ticker} op in portfolio</b>")
        lines.append("↔️ Financier vanuit vrij cash — kandidaat verslaat geen huidige positie")
    elif score >= 6 and beaten:
        lines.append(f"👁️ <b>MONITOREN — potentieel sterker dan {beaten[0]['ticker']}</b>")
        lines.append(f"   Score {score}/10 vs {beaten[0]['ticker']} {beaten[0]['pos_score']}/10 — nog niet genoeg voor directe instap")
    elif score >= 6:
        lines.append(f"👁️ <b>MONITOREN: Interessant maar portfolio is sterker</b>")
        lines.append(f"   Volg {ticker} — bij C-suite of cluster upgrade wél interessant")
    else:
        lines.append("⏸️ Onvoldoende signaal — portfolio is sterker")

    return "\n".join(lines)


# ── Telegram verzenden ────────────────────────────────────────────────────────

def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """Verstuur bericht via Telegram Bot API (parse_mode HTML)."""
    url  = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id":    chat_id,
        "text":       message,
        "parse_mode": "HTML",
    }).encode()
    req = Request(url, data=data, method="POST")
    try:
        with urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"[warn] Telegram send fout: {e}", file=sys.stderr)
        return False


# ── Kandidaten filteren uit monitor JSON ──────────────────────────────────────

def load_candidates_from_monitor(
    monitor_json: Path,
    portfolio_set: set[str],
) -> list[str]:
    """
    Lees portfolio_monitor.json en filter op:
      - signal == STERKE OVERTUIGING
      - ticker NIET in huidig portfolio
    Geeft gesorteerde lijst van tickers terug.
    """
    try:
        records = json.loads(monitor_json.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[error] Kan {monitor_json} niet lezen: {e}", file=sys.stderr)
        return []

    candidates = []
    for r in records:
        ticker = str(r.get("ticker", "")).upper()
        signal = r.get("signal", "")
        if signal == TARGET_SIGNAL and ticker not in portfolio_set:
            candidates.append(ticker)

    return sorted(set(candidates))


# ── Hoofdlogica per ticker ─────────────────────────────────────────────────────

def score_portfolio_position(r: dict) -> tuple[int, list[str]]:
    """
    Bereken een score (1-10) voor een bestaande portfoliopositie.
    Zelfde herziene schaal als score_position() in portfolio_monitor.py:
      9-10 : CEO koopt $5M+ binnen 7d, groot cluster, nul sells
      7-8  : Sterke overtuiging, C-suite, vers en cluster
      5-6  : Positief signaal met C-suite maar minder vers/groot
      3-4  : Positief signaal zonder C-suite of verouderd
      1-2  : Gemengd of zwak signaal
    Ruwe punten (max 12) genormaliseerd naar 1-10.
    """
    raw = 0
    redenen = []

    signal         = r.get("signal", "UNKNOWN")
    days           = r.get("days_since_buy", 999)
    csuite_buyers  = r.get("csuite_buyers", [])
    csuite_sellers = r.get("csuite_sellers", [])
    unique_buyers  = r.get("unique_buyers", 0)
    total_buy      = r.get("total_buy", 0)
    net_flow       = r.get("net_flow_clean", r.get("net_flow", 0))

    # Signaalsterkte (max 3)
    raw += {
        "STERKE OVERTUIGING": 3,
        "POSITIEF SIGNAAL":   2,
        "GEMENGD SIGNAAL":    1,
        "NEGATIEF SIGNAAL":   0,
        "SIGNAAL UITGEWERKT": 0,
    }.get(signal, 0)

    # C-suite kwaliteit (max 2, min -3)
    if csuite_buyers and not csuite_sellers:
        raw += 2
        redenen.append(f"C-suite koopt: {', '.join(csuite_buyers[:2])}")
    elif csuite_buyers and csuite_sellers:
        raw += 0
        redenen.append(f"C-suite koopt én verkoopt — gemengd signaal")
    elif csuite_sellers:
        raw -= 3  # Open market sell door C-suite = sterk negatief
        redenen.append(f"C-suite verkoopt open market: {', '.join(csuite_sellers[:2])}")

    # Versheid (max 3)
    if days <= 7:
        raw += 3
        redenen.append(f"Extreem vers ({days}d)")
    elif days <= 14:
        raw += 2
        redenen.append(f"Vers signaal ({days}d)")
    elif days <= 30:
        raw += 1
        redenen.append(f"Actief signaal ({days}d)")
    else:
        redenen.append(f"Ouder signaal ({days}d)")

    # Cluster (max 1)
    if unique_buyers >= 3 and days <= 30:
        raw += 1
        redenen.append(f"Cluster: {unique_buyers} kopers")

    # Koopomvang (max 2)
    if total_buy >= 5_000_000:
        raw += 2
        redenen.append(f"Grote buy: ${total_buy/1e6:.1f}M")
    elif total_buy >= 1_000_000:
        raw += 1
        redenen.append(f"Significante buy: ${total_buy/1e6:.1f}M")

    # Netto positief (max 1)
    if net_flow > 0 and not csuite_sellers:
        raw += 1
        redenen.append("Geen netto sells")

    # Signaal label als eerste reden
    redenen.insert(0, signal)

    # Normaliseer naar 1-10 (max ruwe score = 12)
    score = round(max(0, raw) / 12 * 10)
    return max(0, min(10, score)), redenen


def portfolio_comparison(monitor_json: Path, portfolio_set: set) -> list[dict]:
    """
    Geeft gescoorde lijst van alle portfolioposities terug, gesorteerd zwakste eerst.
    """
    if not monitor_json or not monitor_json.exists():
        return []
    try:
        results = json.loads(monitor_json.read_text(encoding="utf-8"))
    except Exception:
        return []

    scored = []
    for r in results:
        if r.get("ticker", "").upper() not in portfolio_set:
            continue
        score, redenen = score_portfolio_position(r)
        scored.append({**r, "pos_score": score, "pos_redenen": redenen})

    scored.sort(key=lambda x: (x["pos_score"], -x.get("days_since_buy", 0)))
    return scored


def weakest_portfolio_position(monitor_json: Path, portfolio_set: set) -> dict | None:
    positions = portfolio_comparison(monitor_json, portfolio_set)
    return positions[0] if positions else None


def research_ticker(
    ticker: str,
    reports_dir: Path,
    send_tg: bool,
    bot_token: str,
    chat_id: str,
    monitor_json: Path | None = None,
    portfolio_set: set | None = None,
) -> dict:
    """
    Voer volledig onderzoek uit op één ticker.
    Geeft research-resultaat dict terug.
    """
    ticker = ticker.upper()
    print(f"[research] Analyseer {ticker}...", file=sys.stderr)

    # 1. Laad insider data uit deepdive JSON
    insider_data = load_deepdive_for_ticker(ticker, reports_dir)
    if not insider_data["buys_detail"]:
        print(f"[research] {ticker}: geen deepdive data gevonden — sla over", file=sys.stderr)

    # 2. Yahoo Finance marktdata (met graceful degradatie)
    market_data: dict = {
        "market_cap":     None,
        "price_now":      None,
        "price_30d_ago":  None,
        "pct_change_30d": None,
    }
    try:
        market_data = get_market_data(ticker)
    except Exception as e:
        print(f"[warn] {ticker}: Yahoo Finance fout: {e} — ga door zonder marktcap", file=sys.stderr)

    # 3. Nieuws headlines (optioneel, fout is niet fataal)
    news_headlines: list[str] = []
    try:
        news_headlines = get_news_headlines(ticker)
    except Exception as e:
        print(f"[warn] {ticker}: nieuws ophalen mislukt: {e}", file=sys.stderr)

    # 4. Score berekenen
    score, score_redenen = score_candidate(ticker, insider_data, market_data)
    print(f"[research] {ticker}: score {score}/10 — {', '.join(score_redenen)}", file=sys.stderr)

    # 4b. Portfoliovergelijking (voor herbalanceringsadvies)
    all_positions: list[dict] = []
    weakest = None
    if monitor_json and portfolio_set:
        all_positions = portfolio_comparison(monitor_json, portfolio_set)
        weakest = all_positions[0] if all_positions else None

    # 5. Telegram bericht (alleen bij score >= MIN_SCORE)
    tg_sent = False
    if score >= MIN_SCORE and send_tg:
        msg = format_telegram_message(
            ticker, insider_data, market_data, score, score_redenen,
            news_headlines, weakest_position=weakest,
            portfolio_positions=all_positions,
        )
        if bot_token and chat_id:
            tg_sent = send_telegram(msg, bot_token, chat_id)
            status = "verstuurd" if tg_sent else "MISLUKT"
            print(f"[research] {ticker}: Telegram {status}", file=sys.stderr)
        else:
            print(f"[warn] Telegram tokens niet geconfigureerd — bericht niet verstuurd", file=sys.stderr)
            print(f"\n{'─'*60}\n{msg}\n{'─'*60}\n")
    elif score >= MIN_SCORE:
        msg = format_telegram_message(
            ticker, insider_data, market_data, score, score_redenen,
            news_headlines, weakest_position=weakest,
            portfolio_positions=all_positions,
        )
        print(f"\n{'─'*60}\n{msg}\n{'─'*60}\n")

    # Marktcap voor percentage-berekening in output
    market_cap = market_data.get("market_cap")
    total_buy  = insider_data.get("total_buy", 0.0)
    pct_mcap   = None
    if market_cap and market_cap > 0 and total_buy > 0:
        pct_mcap = round(total_buy / market_cap * 100, 4)

    return {
        "ticker":          ticker,
        "signal":          TARGET_SIGNAL,
        "score":           score,
        "score_redenen":   score_redenen,
        "total_buy":       round(total_buy, 2),
        "total_sell":      round(insider_data.get("total_sell", 0.0), 2),
        "unique_buyers":   insider_data.get("unique_buyers", 0),
        "csuite_buyers":   insider_data.get("csuite_buyers", []),
        "last_buy_date":   insider_data.get("last_buy_date"),
        "days_since_buy":  insider_data.get("days_since_buy", 999),
        "buys_detail":     insider_data.get("buys_detail", [])[:5],
        "market_cap":      round(market_cap, 0) if market_cap else None,
        "price_now":       market_data.get("price_now"),
        "pct_change_30d":  round(market_data.get("pct_change_30d") or 0, 2) if market_data.get("pct_change_30d") is not None else None,
        "pct_mcap":        pct_mcap,
        "news_headlines":  news_headlines,
        "tg_sent":         tg_sent,
        "analyzed_at":     datetime.now().isoformat(timespec="seconds"),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Candidate Research — diepgaande analyse van STERKE OVERTUIGING kandidaten"
    )

    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--monitor-json",
        type=Path,
        metavar="FILE",
        help="portfolio_monitor.json output — filtert automatisch op STERKE OVERTUIGING",
    )
    source.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        help="Expliciete lijst van tickers om te analyseren (overslaat signaalfilter)",
    )

    p.add_argument(
        "--portfolio",
        nargs="+",
        default=[],
        metavar="TICKER",
        help="Tickers die al in het portfolio zitten (worden overgeslagen)",
    )
    p.add_argument(
        "--telegram",
        action="store_true",
        help="Verstuur bevindingen met score >= 6 via Telegram",
    )
    p.add_argument(
        "--output-dir",
        default="data/reports",
        metavar="DIR",
        help="Output directory voor JSON resultaten (default: data/reports)",
    )
    p.add_argument(
        "--min-score",
        type=int,
        default=MIN_SCORE,
        metavar="N",
        help=f"Minimale score voor Telegram melding (default: {MIN_SCORE})",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    portfolio_set = {t.upper() for t in args.portfolio}
    reports_dir   = Path(args.output_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Bepaal te onderzoeken tickers
    if args.monitor_json:
        if not args.monitor_json.exists():
            print(f"[error] {args.monitor_json} niet gevonden", file=sys.stderr)
            sys.exit(1)
        candidates = load_candidates_from_monitor(args.monitor_json, portfolio_set)
        if not candidates:
            print(
                f"[research] Geen {TARGET_SIGNAL} kandidaten gevonden buiten portfolio — klaar.",
                file=sys.stderr,
            )
            sys.exit(0)
        print(
            f"[research] {len(candidates)} kandidaat(en) gevonden: {', '.join(candidates)}",
            file=sys.stderr,
        )
    else:
        # Expliciete tickers — filter wel op portfolio
        candidates = [t.upper() for t in args.tickers if t.upper() not in portfolio_set]
        if not candidates:
            print("[research] Alle opgegeven tickers zitten al in het portfolio — klaar.", file=sys.stderr)
            sys.exit(0)

    # Telegram credentials
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
    if args.telegram and (not bot_token or not chat_id):
        print(
            "[warn] TELEGRAM_BOT_TOKEN en/of TELEGRAM_CHAT_ID niet geconfigureerd — "
            "berichten worden geprint maar niet verstuurd",
            file=sys.stderr,
        )

    # Onderzoek elke kandidaat
    global MIN_SCORE
    MIN_SCORE = args.min_score  # Overschrijf globaal met CLI arg

    # Monitor JSON pad — voor portfoliovergelijking
    # Bij --tickers: fallback naar standaard portfolio_monitor.json zodat vergelijking altijd werkt
    monitor_json_path = (
        args.monitor_json
        if args.monitor_json
        else Path(args.output_dir) / "portfolio_monitor.json"
    )

    all_results: list[dict] = []
    for ticker in candidates:
        try:
            result = research_ticker(
                ticker       = ticker,
                reports_dir  = reports_dir,
                send_tg      = args.telegram,
                bot_token    = bot_token,
                chat_id      = chat_id,
                monitor_json = monitor_json_path,
                portfolio_set= portfolio_set,
            )
            all_results.append(result)
        except Exception as e:
            print(f"[error] {ticker}: onverwachte fout: {e}", file=sys.stderr)
            all_results.append({
                "ticker": ticker,
                "error":  str(e),
                "analyzed_at": datetime.now().isoformat(timespec="seconds"),
            })

    # Sla resultaten op
    today_str = datetime.now().strftime("%Y-%m-%d")
    out_path  = reports_dir / f"candidate_research_{today_str}.json"
    out_path.write_text(
        json.dumps(all_results, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[research] Resultaten opgeslagen: {out_path}", file=sys.stderr)

    # Console samenvatting
    strong = [r for r in all_results if r.get("score", 0) >= MIN_SCORE]
    print(
        f"\n[research] Klaar. {len(all_results)} ticker(s) geanalyseerd, "
        f"{len(strong)} met score >= {MIN_SCORE}.",
        file=sys.stderr,
    )
    for r in sorted(strong, key=lambda x: x.get("score", 0), reverse=True):
        tg_flag = " [TG verstuurd]" if r.get("tg_sent") else ""
        print(
            f"  {r['ticker']}: score {r.get('score')}/10 "
            f"— {', '.join(r.get('score_redenen', []))[:80]}{tg_flag}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
