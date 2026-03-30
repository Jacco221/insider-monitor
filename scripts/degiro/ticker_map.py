#!/usr/bin/env python3
"""SEC ticker → DEGIRO product ID mapping met caching."""

from __future__ import annotations


import json
import sys
from pathlib import Path

from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.trading_pb2 import ProductSearch

STATE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "state"
CACHE_FILE = STATE_DIR / "degiro_ticker_cache.json"


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


def _save_cache(cache: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def search_product(api: TradingAPI, ticker: str) -> dict | None:
    """Zoek een product op DEGIRO via ticker symbool.

    Returns dict met: product_id, name, isin, symbol, exchange, vwd_id, currency
    of None als niet gevonden.
    """
    cache = _load_cache()
    ticker_upper = ticker.upper()

    if ticker_upper in cache:
        return cache[ticker_upper]

    try:
        result = api.product_search(
            product_request=ProductSearch.RequestLookup(
                search_text=ticker_upper,
                limit=10,
                offset=0,
            ),
            raw=True,
        )
    except Exception as e:
        print(f"[warn] DEGIRO product_search fout voor {ticker_upper}: {e}", file=sys.stderr)
        return None

    products = result.get("products", []) if isinstance(result, dict) else []

    # Zoek beste match: exacte ticker match, bij voorkeur op US exchange
    best = None
    for p in products:
        sym = (p.get("symbol") or "").upper()
        if sym != ticker_upper:
            continue

        product_info = {
            "product_id": p.get("id"),
            "name": p.get("name", ""),
            "isin": p.get("isin", ""),
            "symbol": sym,
            "exchange": p.get("exchangeId", ""),
            "vwd_id": p.get("vwdId", ""),
            "currency": p.get("currency", ""),
        }

        # Prefer US exchanges (NYSE, NASDAQ)
        exchange = (p.get("exchangeId") or "")
        if "XNAS" in str(exchange) or "XNYS" in str(exchange) or "NSQ" in str(exchange) or "NYS" in str(exchange):
            best = product_info
            break

        if best is None:
            best = product_info

    if best:
        cache[ticker_upper] = best
        _save_cache(cache)
        print(f"[degiro] {ticker_upper} → {best['name']} (ISIN: {best['isin']}, ID: {best['product_id']})", file=sys.stderr)
    else:
        print(f"[warn] {ticker_upper} niet gevonden op DEGIRO", file=sys.stderr)
        cache[ticker_upper] = None
        _save_cache(cache)

    return best


def resolve_tickers(api: TradingAPI, tickers: list[str]) -> dict[str, dict | None]:
    """Zoek meerdere tickers op. Returns {ticker: product_info of None}."""
    results = {}
    for t in tickers:
        results[t.upper()] = search_product(api, t)
    return results


def clear_cache():
    """Verwijder de ticker cache."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
        print("[degiro] Ticker cache gewist", file=sys.stderr)
