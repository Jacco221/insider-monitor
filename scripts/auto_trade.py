#!/usr/bin/env python3
"""
Auto-Trade — Automatisch kopen/verkopen op basis van insider signalen.

Draait na de portfolio_monitor en voert trades uit binnen veiligheidslimieten.

Regels:
  EXIT signaal → verkoop volledige positie
  STRONG_HOLD + niet in portfolio → koop (watchlist items)
  HOLD → geen actie
  Nooit meer dan MAX_ORDER_EUR per trade
  Nooit meer dan MAX_DAILY_EUR per dag totaal
  Altijd Telegram notificatie bij elke trade

Gebruik:
  python3 scripts/auto_trade.py --dry-run          # Preview, geen trades
  python3 scripts/auto_trade.py --execute           # Voer trades uit
  python3 scripts/auto_trade.py --execute --max-order 750

Env vars:
  DEGIRO_USERNAME, DEGIRO_PASSWORD, DEGIRO_INT_ACCOUNT
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  DEGIRO_MAX_ORDER_EUR (default: 500)
  DEGIRO_MAX_DAILY_EUR (default: 2000)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from degiro.auth import get_trading_api
from degiro.portfolio import get_portfolio, get_portfolio_tickers, load_signals
from degiro.ticker_map import search_product
from degiro.orders import preview_order, execute_order

REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "reports"
TRADE_LOG = REPORTS_DIR / "degiro" / "trade_log.jsonl"

DEFAULT_MAX_ORDER = 500
DEFAULT_MAX_DAILY = 2000


def load_monitor_results() -> list[dict]:
    path = REPORTS_DIR / "portfolio_monitor.json"
    if not path.exists():
        print("[fout] portfolio_monitor.json niet gevonden. Draai eerst portfolio_monitor.py", file=sys.stderr)
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def daily_traded_amount() -> float:
    """Bereken hoeveel er vandaag al is gehandeld."""
    if not TRADE_LOG.exists():
        return 0.0
    today = datetime.now().strftime("%Y-%m-%d")
    total = 0.0
    for line in TRADE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            if entry.get("date", "").startswith(today):
                total += abs(float(entry.get("amount_eur", 0)))
        except Exception:
            continue
    return total


def log_trade(entry: dict):
    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def send_telegram(message: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode()
    try:
        urlopen(Request(url, data=data, method="POST"), timeout=15)
    except Exception:
        pass


def determine_actions(monitor_results: list[dict], portfolio_tickers: set[str]) -> list[dict]:
    """Bepaal welke trades nodig zijn op basis van monitor signalen."""
    actions = []

    for r in monitor_results:
        ticker = r["ticker"]
        signal = r["signal"]

        if signal == "EXIT" and ticker in portfolio_tickers:
            actions.append({
                "action": "SELL",
                "ticker": ticker,
                "reason": "; ".join(r.get("reasons", [])),
                "signal": signal,
            })

        elif signal == "STRONG_HOLD" and ticker not in portfolio_tickers:
            # Watchlist item met sterk signaal → koop
            if r.get("net_flow", 0) > 0 and r.get("unique_buyers", 0) >= 2:
                actions.append({
                    "action": "BUY",
                    "ticker": ticker,
                    "reason": "; ".join(r.get("reasons", [])),
                    "signal": signal,
                    "net_flow": r.get("net_flow", 0),
                    "csuite_buyers": r.get("csuite_buyers", []),
                })

    # Sorteer: sells eerst, dan buys gesorteerd op conviction
    sells = [a for a in actions if a["action"] == "SELL"]
    buys = sorted(
        [a for a in actions if a["action"] == "BUY"],
        key=lambda x: x.get("net_flow", 0),
        reverse=True,
    )

    return sells + buys


def main():
    parser = argparse.ArgumentParser(description="Auto-Trade: insider-signaal gebaseerde trades")
    parser.add_argument("--execute", action="store_true", help="Voer trades daadwerkelijk uit")
    parser.add_argument("--dry-run", action="store_true", help="Alleen preview, geen trades (default)")
    parser.add_argument("--max-order", type=float, default=None, help=f"Max per order in EUR (default: {DEFAULT_MAX_ORDER})")
    parser.add_argument("--max-daily", type=float, default=None, help=f"Max per dag in EUR (default: {DEFAULT_MAX_DAILY})")
    args = parser.parse_args()

    max_order = args.max_order or float(os.getenv("DEGIRO_MAX_ORDER_EUR", str(DEFAULT_MAX_ORDER)))
    max_daily = args.max_daily or float(os.getenv("DEGIRO_MAX_DAILY_EUR", str(DEFAULT_MAX_DAILY)))
    is_live = args.execute and not args.dry_run

    mode = "🔴 LIVE" if is_live else "🟡 DRY-RUN"
    print(f"\n{'='*50}")
    print(f"AUTO-TRADE {mode}")
    print(f"Max per order: €{max_order:.0f} | Max per dag: €{max_daily:.0f}")
    print(f"{'='*50}\n")

    # Laad monitor resultaten
    monitor = load_monitor_results()
    if not monitor:
        return

    # Verbind met DEGIRO
    print("[trade] Verbinden met DEGIRO...", file=sys.stderr)
    api = get_trading_api()

    # Haal huidige portfolio op
    positions = get_portfolio(api)
    portfolio_info = get_portfolio_tickers(api, positions)
    portfolio_tickers = set()
    portfolio_by_ticker = {}
    for pos in positions:
        pid = pos.get("product_id")
        if pid and pid in portfolio_info:
            sym = portfolio_info[pid].get("symbol", "").upper()
            if sym:
                portfolio_tickers.add(sym)
                portfolio_by_ticker[sym] = {**pos, **portfolio_info[pid]}

    print(f"Portfolio: {', '.join(sorted(portfolio_tickers)) or 'leeg'}")
    print(f"Monitor tickers: {', '.join(r['ticker'] for r in monitor)}\n")

    # Bepaal trades
    actions = determine_actions(monitor, portfolio_tickers)

    if not actions:
        print("✅ Geen trades nodig. Portfolio is in lijn met signalen.")
        send_telegram("📊 <b>Auto-Trade</b>\n\n✅ Geen trades nodig. Portfolio aligned met insider signalen.")
        return

    # Check dagelijks limiet
    already_traded = daily_traded_amount()
    remaining_daily = max_daily - already_traded

    tg_lines = [f"📊 <b>Auto-Trade {mode}</b>\n"]

    for action in actions:
        ticker = action["ticker"]
        act = action["action"]

        print(f"--- {act} {ticker} ---")
        print(f"  Reden: {action['reason']}")

        # Zoek product op DEGIRO
        product = search_product(api, ticker)
        if not product or not product.get("product_id"):
            print(f"  ⚠️ {ticker} niet gevonden op DEGIRO, overslaan")
            continue

        product_id = product["product_id"]

        if act == "SELL":
            # Verkoop volledige positie
            pos_info = portfolio_by_ticker.get(ticker, {})
            size = pos_info.get("size", 0)
            if size <= 0:
                print(f"  ⚠️ Geen positie in {ticker}, overslaan")
                continue

            preview = preview_order(api, product_id, 999999, "SELL")
            # Override size met werkelijke positie
            if "_order" in preview:
                preview["_order"].size = int(size)
                preview["size"] = int(size)

        elif act == "BUY":
            # Check limieten
            order_amount = min(max_order, remaining_daily)
            if order_amount < 50:
                print(f"  ⚠️ Dagelijks limiet bereikt (€{already_traded:.0f}/€{max_daily:.0f})")
                continue

            preview = preview_order(api, product_id, order_amount, "BUY")

        if "error" in preview:
            print(f"  ❌ {preview['error']}")
            continue

        print(f"  Preview: {act} {preview['size']}x {ticker} @ ~${preview['estimated_price']:.2f} = ~${preview['estimated_total']:.2f}")

        if is_live:
            result = execute_order(api, preview)
            if "error" in result:
                print(f"  ❌ Order gefaald: {result['error']}")
                tg_lines.append(f"❌ {act} {ticker}: {result['error']}")
            else:
                print(f"  ✅ Order uitgevoerd! ID: {result.get('order_id')}")
                remaining_daily -= preview["estimated_total"]

                log_trade({
                    "date": datetime.now().isoformat(),
                    "action": act,
                    "ticker": ticker,
                    "size": preview["size"],
                    "price": preview["estimated_price"],
                    "amount_eur": preview["estimated_total"],
                    "order_id": result.get("order_id"),
                    "reason": action["reason"],
                })

                emoji = "🔴" if act == "SELL" else "🟢"
                tg_lines.append(
                    f"{emoji} <b>{act} {ticker}</b>: {preview['size']}x @ ${preview['estimated_price']:.2f}"
                    f"\n  → ${preview['estimated_total']:.2f} | {action['reason'][:60]}"
                )
        else:
            tg_lines.append(
                f"{'🔴' if act == 'SELL' else '🟢'} [DRY-RUN] <b>{act} {ticker}</b>: "
                f"{preview['size']}x @ ${preview['estimated_price']:.2f} = ${preview['estimated_total']:.2f}"
            )

        print()

    # Telegram samenvatting
    tg_message = "\n".join(tg_lines)
    send_telegram(tg_message)
    print(f"\n[trade] Telegram notificatie verstuurd", file=sys.stderr)


if __name__ == "__main__":
    main()
