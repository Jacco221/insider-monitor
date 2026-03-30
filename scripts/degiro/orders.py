#!/usr/bin/env python3
"""DEGIRO order suggesties en uitvoering (v3 API). Orders worden NOOIT automatisch geplaatst."""

from __future__ import annotations


import os
import sys

from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.trading_pb2 import Order


DEFAULT_MAX_ORDER_EUR = 500


def suggest_orders(to_buy: list[dict], product_map: dict, max_order_eur: float | None = None) -> list[dict]:
    """Genereer order suggesties op basis van insider signals.

    Args:
        to_buy: lijst uit portfolio.compare_with_signals()['to_buy']
        product_map: {ticker: product_info} uit ticker_map.resolve_tickers()
        max_order_eur: maximaal bedrag per order

    Returns: lijst van order suggesties (nog NIET geplaatst)
    """
    max_eur = max_order_eur or float(os.getenv("DEGIRO_MAX_ORDER_EUR", str(DEFAULT_MAX_ORDER_EUR)))

    suggestions = []
    for signal in to_buy:
        ticker = signal["ticker"]
        product = product_map.get(ticker)

        if not product or not product.get("product_id"):
            continue

        suggestions.append({
            "ticker": ticker,
            "product_id": product["product_id"],
            "name": product.get("name", ""),
            "isin": product.get("isin", ""),
            "currency": product.get("currency", "USD"),
            "max_amount_eur": max_eur,
            "signal_net_flow": signal.get("net_flow", 0),
            "signal_transactions": signal.get("transactions", 0),
            "signal_total_buy": signal.get("total_buy", 0),
        })

    return suggestions


def preview_order(api: TradingAPI, product_id: int, amount_eur: float, action: str = "BUY") -> dict:
    """Preview een order via DEGIRO's check_order. Plaatst NIETS.

    Returns: order details incl. geschatte kosten, of foutmelding.
    """
    buy_sell = Order.Action.Value("BUY") if action.upper() == "BUY" else Order.Action.Value("SELL")

    # Haal product prijs op voor size berekening
    try:
        info = api.get_products_info(product_list=[product_id], raw=True)
        product_data = info.get("data", {}).get(str(product_id), {})
        close_price = float(product_data.get("closePrice", 0))
        name = product_data.get("name", "?")
    except Exception as e:
        return {"error": f"Kan productprijs niet ophalen: {e}"}

    if close_price <= 0:
        return {"error": f"Geen geldige prijs beschikbaar voor product {product_id}"}

    # Bereken aantal shares
    size = max(1, int(amount_eur / close_price))

    order = Order(
        action=buy_sell,
        order_type=Order.OrderType.Value("MARKET"),
        product_id=product_id,
        size=size,
        time_type=Order.TimeType.Value("GOOD_TILL_DAY"),
    )

    try:
        checking_response = api.check_order(order=order)
    except Exception as e:
        return {"error": f"Order check gefaald: {e}"}

    confirmation_id = getattr(checking_response, "confirmation_id", None)

    return {
        "product_id": product_id,
        "name": name,
        "action": action.upper(),
        "size": size,
        "estimated_price": close_price,
        "estimated_total": round(close_price * size, 2),
        "confirmation_id": confirmation_id,
        "_order": order,
    }


def execute_order(api: TradingAPI, preview: dict) -> dict:
    """Voer een order uit. ALLEEN aanroepen na expliciete gebruikersbevestiging.

    Args:
        preview: resultaat van preview_order() met confirmation_id en _order
    """
    confirmation_id = preview.get("confirmation_id")
    order = preview.get("_order")

    if not confirmation_id or not order:
        return {"error": "Geen geldige preview/confirmation beschikbaar"}

    try:
        result = api.confirm_order(
            confirmation_id=confirmation_id,
            order=order,
        )
        return {
            "status": "uitgevoerd",
            "order_id": getattr(result, "order_id", str(result)),
            "product_id": preview["product_id"],
            "action": preview["action"],
            "size": preview["size"],
            "estimated_total": preview["estimated_total"],
        }
    except Exception as e:
        return {"error": f"Order uitvoering gefaald: {e}"}
