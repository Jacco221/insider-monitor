# src/market_regime.py
# Bepaalt marktregime via BTC t.o.v. MA50/MA200 (CoinGecko).
# RISK_ON  = BTC close > MA50 en MA50 > MA200
# RISK_OFF = anders

from __future__ import annotations
import datetime as dt
import json
import sys
from typing import Dict, Any, Tuple

import requests
import pandas as pd


COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
USER_AGENT = "crypto-pipeline-market-regime/1.0"


def _fetch_btc_prices(days: int = 300) -> pd.Series:
    """Haalt dagprijzen (USD) van BTC op via CoinGecko voor N dagen."""
    params = {"vs_currency": "usd", "days": days}
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    r = requests.get(COINGECKO_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    # data["prices"] = [[ts_ms, price], ...]
    prices = pd.DataFrame(data["prices"], columns=["ts_ms", "price"])
    prices["date"] = pd.to_datetime(prices["ts_ms"], unit="ms").dt.date
    s = prices.groupby("date")["price"].last()
    s.index = pd.to_datetime(s.index)
    s.name = "BTC_USD"
    return s


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=max(5, window // 5)).mean()


def determine_market_regime(days: int = 300,
                            short_ma: int = 50,
                            long_ma: int = 200) -> Dict[str, Any]:
    """Bepaalt marktregime o.b.v. BTC > MA50 en MA50 > MA200."""
    s = _fetch_btc_prices(days=days)
    if len(s) < long_ma:
        # Te weinig data, val terug op conservatief: RISK_OFF
        return {
            "regime": "RISK_OFF",
            "reason": "insufficient_history",
            "as_of": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }

    ma50 = _sma(s, short_ma)
    ma200 = _sma(s, long_ma)
    last_close = float(s.iloc[-1])
    last_ma50 = float(ma50.iloc[-1])
    last_ma200 = float(ma200.iloc[-1])

    risk_on = (last_close > last_ma50) and (last_ma50 > last_ma200)
    regime = "RISK_ON" if risk_on else "RISK_OFF"

    return {
        "regime": regime,
        "as_of": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "last_close": round(last_close, 2),
        "ma50": round(last_ma50, 2),
        "ma200": round(last_ma200, 2),
        "rule": "close>MA50 and MA50>MA200",
        "source": "CoinGecko",
    }


def main(argv: list[str]) -> int:
    # Kleine CLI: print JSON met regime
    out = determine_market_regime()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

