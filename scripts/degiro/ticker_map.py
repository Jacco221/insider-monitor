#!/usr/bin/env python3
"""SEC ticker → DEGIRO product ID mapping met caching."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.product_search import LookupRequest

STATE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "state"
CACHE_FILE = STATE_DIR / "degiro_ticker_cache.json"


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def search_product(api: TradingAPI, ticker: str) -> dict | None:
    """Zoek een product op DEGIRO via ticker symbool."""
    cache = _load_cache()
    ticker_upper = ticker.upper()

    if ticker_upper in cache:
        return cache[ticker_upper]

    try:
        result = api.product_search(
            product_request=LookupRequest(
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

        exchange = str(p.get("exchangeId", ""))
        if any(x in exchange for x in ("XNAS", "XNYS", "NSQ", "NYS")):
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

    return best


def resolve_tickers(api: TradingAPI, tickers: list[str]) -> dict[str, dict | None]:
    """Zoek meerdere tickers op."""
    return {t.upper(): search_product(api, t) for t in tickers}
