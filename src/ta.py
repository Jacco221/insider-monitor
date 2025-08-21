from src.utils import get
import pandas as pd
from datetime import datetime, timezone

def _klines_coingecko(cg_id:str, days=365):
    """Dagelijkse prijs/volume via CoinGecko market_chart (1 call per coin)."""
    data = get(f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
               params={"vs_currency":"usd","days":days,"interval":"daily"})
    prices = pd.DataFrame(data.get("prices", []), columns=["ts","price"])
    vols   = pd.DataFrame(data.get("total_volumes", []), columns=["ts","volume"])
    if prices.empty or vols.empty:
        return pd.DataFrame()
    df = prices.merge(vols, on="ts", how="inner")
    df["date"]   = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["close"]  = df["price"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df[["date","close","volume"]]

def _age_hours(ts): return (datetime.now(timezone.utc) - ts).total_seconds()/3600

def _time_weight(age_hours: float) -> float:
    if age_hours is None:      return 1.0
    if age_hours < 24:         return 3.0
    if age_hours < 24*3:       return 2.0
    if age_hours < 24*7:       return 1.0
    return 0.5

def ta_indicators(symbol:str, cg_id:str):
    """Indicatoren: ma_crossover, volume_trend, funding_rate (neutraal)."""
    scores, ages, w = {}, {}, {}
    df = _klines_coingecko(cg_id, 365)

    if not df.empty:
        last_ts = df["date"].iloc[-1]
        age = _age_hours(last_ts)

        # MA50 vs MA200 (als er <200 candles zijn, gebruik gemiddelde als benadering)
        ma50  = df["close"].tail(50).mean()
        ma200 = df["close"].tail(200).mean() if len(df) >= 200 else df["close"].mean()
        sc = 1 if ma50 > ma200 else (-1 if ma50 < ma200 else 0)
        scores["ma_crossover"] = sc
        ages["ma_crossover"]   = age
        w["ma_crossover"]      = _time_weight(age)

        # Volume trend 7d vs 30d (±5% drempel)
        v7  = df["volume"].tail(7).mean()
        v30 = df["volume"].tail(30).mean() if len(df) >= 30 else df["volume"].mean()
        if v7 > 1.05 * v30:     sc = 1
        elif v7 < 0.95 * v30:   sc = -1
        else:                   sc = 0
        scores["volume_trend"] = sc
        ages["volume_trend"]   = age
        w["volume_trend"]      = _time_weight(age)
    else:
        # Geen data → neutraal
        scores["ma_crossover"] = 0
        scores["volume_trend"] = 0
        ages["ma_crossover"] = ages["volume_trend"] = None
        w["ma_crossover"] = w["volume_trend"] = 1.0

    # Funding voorlopig neutraal (geen Binance‑call → snel en stabiel)
    scores["funding_rate"] = 0
    ages["funding_rate"]   = None
    w["funding_rate"]      = 1.0

    return scores, ages, w

def weighted_group_score(scores: dict, weights: dict) -> float:
    """Gemiddelde (-1..+1) met tijdsgewichten."""
    num = sum(scores[k]*weights.get(k,1.0) for k in scores)
    den = sum(weights.get(k,1.0) for k in scores)
    return 0.0 if den == 0 else num/den
