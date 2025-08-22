# src/rs.py
from src.utils import get
import pandas as pd
import time

def _cg_market_chart(coin_id: str, days: int = 30):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    data = get(url, params=params)  # gebruikt jouw robuuste GET
    prices = data.get("prices", [])
    if not prices:
        return None
    # lijst van [ms, price] -> DataFrame
    df = pd.DataFrame(prices, columns=["ts", "price"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def rs_vs_btc_indicator(coin_id: str) -> tuple[int, dict, dict]:
    """
    Relatieve sterkte 30d: performance(coin) - performance(BTC).
    Drempels:
      diff > +0.02 => +1
      diff < -0.02 => -1
      anders 0
    """
    try:
        df_c = _cg_market_chart(coin_id, 30)
        df_b = _cg_market_chart("bitcoin", 30)
        if df_c is None or df_b is None or len(df_c) < 2 or len(df_b) < 2:
            return 0, {"rs": None}, {"rs": 1.0}

        pc = (df_c["price"].iloc[-1] / df_c["price"].iloc[0]) - 1.0
        pb = (df_b["price"].iloc[-1] / df_b["price"].iloc[0]) - 1.0
        diff = pc - pb
        score = 0
        if diff > 0.02:
            score = +1
        elif diff < -0.02:
            score = -1

        last_ts = df_c["ts"].iloc[-1].to_pydatetime().timestamp()
        age_hours = max(0.0, (time.time() - last_ts) / 3600.0)
        return score, {"rs": age_hours}, {"rs": 1.0}
    except Exception:
        return 0, {"rs": None}, {"rs": 1.0}
