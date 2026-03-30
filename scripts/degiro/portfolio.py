#!/usr/bin/env python3
"""DEGIRO portfolio uitlezen en vergelijken met insider signals (v3 API)."""

from __future__ import annotations


import json
import sys
from pathlib import Path

from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.trading_pb2 import Update


def get_portfolio(api: TradingAPI) -> list[dict]:
    """Haal huidige DEGIRO posities op via get_update.

    Returns lijst van dicts met: product_id, size, price, value, currency.
    """
    request_list = Update.RequestList()
    request_list.values.extend([
        Update.Request(option=Update.Option.Value("PORTFOLIO")),
    ])

    try:
        update = api.get_update(request_list=request_list, raw=True)
    except Exception as e:
        print(f"[fout] Kan portfolio niet ophalen: {e}", file=sys.stderr)
        return []

    portfolio_data = update.get("portfolio", {})
    positions = []

    for item in portfolio_data.get("value", []):
        pos = {}
        for entry in item.get("value", []):
            name = entry.get("name", "")
            value = entry.get("value", "")
            if name == "id":
                pos["product_id"] = value
            elif name == "size":
                pos["size"] = value
            elif name == "price":
                pos["price"] = value
            elif name == "value":
                pos["value"] = value
            elif name == "currency":
                pos["currency"] = value
            elif name == "product":
                pos["name"] = value

        if pos.get("product_id") and pos.get("size", 0) != 0:
            positions.append(pos)

    return positions


def get_portfolio_tickers(api: TradingAPI, positions: list[dict]) -> dict:
    """Haal product details op voor portfolio posities.

    Returns {product_id: {name, symbol, isin, currency, ...}}.
    """
    product_ids = [p["product_id"] for p in positions if p.get("product_id")]
    if not product_ids:
        return {}

    try:
        info = api.get_products_info(
            product_list=product_ids,
            raw=True,
        )
    except Exception as e:
        print(f"[warn] Kan product info niet ophalen: {e}", file=sys.stderr)
        return {}

    result = {}
    data = info.get("data", {})
    for pid_str, pdata in data.items():
        pid = int(pid_str) if pid_str.isdigit() else pid_str
        result[pid] = {
            "product_id": pid,
            "name": pdata.get("name", ""),
            "symbol": pdata.get("symbol", ""),
            "isin": pdata.get("isin", ""),
            "exchange": pdata.get("exchangeId", ""),
            "currency": pdata.get("currency", ""),
        }
    return result


def compare_with_signals(portfolio: list[dict], portfolio_info: dict, signals: dict) -> dict:
    """Vergelijk DEGIRO portfolio met insider signals uit deep dive JSON.

    Args:
        portfolio: lijst van portfolio posities
        portfolio_info: {product_id: product_details}
        signals: deep dive JSON output (met 'summary' key)

    Returns:
        dict met 'portfolio_tickers', 'in_portfolio', 'to_buy', 'to_sell'
    """
    portfolio_tickers = set()
    for pos in portfolio:
        pid = pos.get("product_id")
        if pid and pid in portfolio_info:
            sym = portfolio_info[pid].get("symbol", "").upper()
            if sym:
                portfolio_tickers.add(sym)

    summary = signals.get("summary", [])
    in_portfolio = []
    to_buy = []
    to_sell = []

    for s in summary:
        ticker = s.get("ticker", "").upper()
        net = s.get("NET", 0)
        p_buy = s.get("P_BUY", 0)
        s_sell = s.get("S_SELL", 0)
        rows = s.get("rows", 0)

        signal_info = {
            "ticker": ticker,
            "net_flow": net,
            "total_buy": p_buy,
            "total_sell": s_sell,
            "transactions": rows,
            "in_portfolio": ticker in portfolio_tickers,
        }

        if ticker in portfolio_tickers:
            in_portfolio.append(signal_info)
            if net < 0 and abs(s_sell) > abs(p_buy) * 2:
                to_sell.append(signal_info)
        else:
            if net > 0 and rows >= 2 and p_buy >= 100_000:
                to_buy.append(signal_info)

    to_buy.sort(key=lambda x: x["net_flow"], reverse=True)
    to_sell.sort(key=lambda x: x["net_flow"])

    return {
        "portfolio_tickers": sorted(portfolio_tickers),
        "in_portfolio": in_portfolio,
        "to_buy": to_buy,
        "to_sell": to_sell,
    }


def load_signals(signals_path: str) -> dict:
    """Laad deep dive JSON output."""
    path = Path(signals_path)
    if not path.exists():
        print(f"[fout] Signals bestand niet gevonden: {path}", file=sys.stderr)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
