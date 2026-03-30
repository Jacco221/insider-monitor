#!/usr/bin/env python3
"""
DEGIRO Trade CLI — Insider Monitor integratie

Subcommands:
  portfolio    Toon huidige DEGIRO posities
  signals      Laad deep dive resultaten en toon buy/sell suggesties
  sync         Vergelijk portfolio met insider signals
  order        Preview of plaats een order

Gebruik:
  python3 scripts/degiro_trade.py portfolio
  python3 scripts/degiro_trade.py signals --file data/reports/deepdive_AAPL_2026-03-30.json
  python3 scripts/degiro_trade.py sync --file data/reports/deepdive_AAPL_2026-03-30.json
  python3 scripts/degiro_trade.py order --ticker AAPL --amount 500
  python3 scripts/degiro_trade.py order --ticker AAPL --amount 500 --execute

Vereiste env vars:
  DEGIRO_USERNAME, DEGIRO_PASSWORD
  Optioneel: DEGIRO_INT_ACCOUNT, DEGIRO_MAX_ORDER_EUR
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Zorg dat scripts/degiro/ importeerbaar is
sys.path.insert(0, str(Path(__file__).resolve().parent))

from degiro.auth import get_trading_api
from degiro.ticker_map import resolve_tickers, search_product
from degiro.portfolio import get_portfolio, get_portfolio_tickers, compare_with_signals, load_signals
from degiro.orders import suggest_orders, preview_order, execute_order


def cmd_portfolio(args):
    """Toon huidige DEGIRO posities."""
    api = get_trading_api()
    positions = get_portfolio(api)

    if not positions:
        print("Geen posities gevonden.")
        return

    info = get_portfolio_tickers(api, positions)

    print(f"\n{'Ticker':<8} {'Naam':<35} {'Aantal':<10} {'Prijs':<10} {'Waarde':<12} {'Valuta':<6}")
    print("-" * 85)

    for pos in positions:
        pid = pos.get("product_id")
        p_info = info.get(pid, {})
        symbol = p_info.get("symbol", "?")
        name = p_info.get("name", pos.get("name", ""))[:33]
        size = pos.get("size", 0)
        price = pos.get("price", 0)
        value = pos.get("value", 0)
        currency = p_info.get("currency", pos.get("currency", ""))

        print(f"{symbol:<8} {name:<35} {size:<10} {price:<10} {value:<12} {currency:<6}")

    print(f"\nTotaal: {len(positions)} posities")


def cmd_signals(args):
    """Toon insider signals uit deep dive JSON."""
    signals = load_signals(args.file)
    if not signals:
        return

    summary = signals.get("summary", [])
    print(f"\n=== Insider Signals ({signals.get('generated', '?')}) ===")
    print(f"Tickers: {', '.join(signals.get('tickers', []))}")
    print(f"Periode: {signals.get('days', '?')} dagen\n")

    print(f"{'Ticker':<8} {'Txns':<6} {'BUY':<15} {'SELL':<15} {'NET':<15} {'Signaal':<10}")
    print("-" * 75)

    for s in summary:
        net = s.get("NET", 0)
        signal = "KOOP" if net > 0 else "VERKOOP" if net < 0 else "NEUTRAAL"
        print(
            f"{s['ticker']:<8} "
            f"{s['rows']:<6} "
            f"${s.get('P_BUY', 0):>12,.0f}  "
            f"${s.get('S_SELL', 0):>12,.0f}  "
            f"${net:>12,.0f}  "
            f"{signal:<10}"
        )


def cmd_sync(args):
    """Vergelijk DEGIRO portfolio met insider signals."""
    api = get_trading_api()
    signals = load_signals(args.file)
    if not signals:
        return

    positions = get_portfolio(api)
    portfolio_info = get_portfolio_tickers(api, positions)

    comparison = compare_with_signals(positions, portfolio_info, signals)

    print(f"\n=== Portfolio Sync ===")
    print(f"Portfolio tickers: {', '.join(comparison['portfolio_tickers']) or 'leeg'}\n")

    if comparison["to_buy"]:
        print("--- KOOPSUGGESTIES (niet in portfolio, sterk insider buy signaal) ---")
        for s in comparison["to_buy"]:
            print(f"  {s['ticker']:<8} NET: ${s['net_flow']:>12,.0f}  ({s['transactions']} transacties)")
    else:
        print("--- Geen koopsuggesties ---")

    if comparison["to_sell"]:
        print("\n--- VERKOOPSUGGESTIES (in portfolio, sterk insider sell signaal) ---")
        for s in comparison["to_sell"]:
            print(f"  {s['ticker']:<8} NET: ${s['net_flow']:>12,.0f}  ({s['transactions']} transacties)")

    if comparison["in_portfolio"]:
        print(f"\n--- In portfolio met insider data ({len(comparison['in_portfolio'])}) ---")
        for s in comparison["in_portfolio"]:
            status = "OK" if s["net_flow"] >= 0 else "LET OP"
            print(f"  {s['ticker']:<8} NET: ${s['net_flow']:>12,.0f}  [{status}]")


def cmd_order(args):
    """Preview of plaats een order."""
    api = get_trading_api()

    # Zoek product op DEGIRO
    product = search_product(api, args.ticker)
    if not product:
        print(f"[fout] {args.ticker} niet gevonden op DEGIRO", file=sys.stderr)
        return

    print(f"\nProduct: {product['name']} ({product['symbol']})")
    print(f"ISIN: {product['isin']}")
    print(f"Exchange: {product['exchange']}")

    # Preview
    action = args.action.upper()
    preview = preview_order(api, product["product_id"], args.amount, action)

    if "error" in preview:
        print(f"\n[fout] {preview['error']}", file=sys.stderr)
        return

    print(f"\n--- Order Preview ---")
    print(f"Actie:    {action}")
    print(f"Aantal:   {preview['size']} stuks")
    print(f"Prijs:    ~${preview['estimated_price']:.2f}")
    print(f"Totaal:   ~${preview['estimated_total']:.2f}")

    if not args.execute:
        print(f"\nDit is een PREVIEW. Voeg --execute toe om daadwerkelijk te handelen.")
        return

    # Uitvoeren
    print(f"\nOrder wordt geplaatst...")
    result = execute_order(api, preview)

    if "error" in result:
        print(f"[fout] {result['error']}", file=sys.stderr)
    else:
        print(f"Order uitgevoerd! ID: {result.get('order_id')}")
        print(f"  {result['action']} {result['size']}x {args.ticker} (~${result['estimated_total']:.2f})")

    # Log naar bestand
    log_dir = Path(__file__).resolve().parent.parent / "data" / "reports" / "degiro"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"order_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    log_file.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="DEGIRO Trade CLI — Insider Monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # portfolio
    subparsers.add_parser("portfolio", help="Toon huidige DEGIRO posities")

    # signals
    p_signals = subparsers.add_parser("signals", help="Toon insider signals uit deep dive")
    p_signals.add_argument("--file", required=True, help="Pad naar deep dive JSON bestand")

    # sync
    p_sync = subparsers.add_parser("sync", help="Vergelijk portfolio met insider signals")
    p_sync.add_argument("--file", required=True, help="Pad naar deep dive JSON bestand")

    # order
    p_order = subparsers.add_parser("order", help="Preview of plaats een order")
    p_order.add_argument("--ticker", required=True, help="Ticker symbool (bijv. AAPL)")
    p_order.add_argument("--amount", type=float, default=500, help="Bedrag in EUR (default: 500)")
    p_order.add_argument("--action", default="BUY", choices=["BUY", "SELL"], help="Koop of verkoop")
    p_order.add_argument("--execute", action="store_true", help="Voer order daadwerkelijk uit (zonder: alleen preview)")

    args = parser.parse_args()

    commands = {
        "portfolio": cmd_portfolio,
        "signals": cmd_signals,
        "sync": cmd_sync,
        "order": cmd_order,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
