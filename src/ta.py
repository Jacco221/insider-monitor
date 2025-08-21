from src.utils import get
import pandas as pd
import time
from datetime import datetime, timezone

BINANCE_SPOT = "https://api.binance.com"
BINANCE_FUT  = "https://fapi.binance.com"

def _klines_binance(pair:str, interval="1d", limit=250):
    raw = get(f"{BINANCE_SPOT}/api/v3/klines",
              params={"symbol":pair, "interval":interval, "limit":limit})
    cols = ["open_time","open","high","low","close","volume","close_time","qav","trades","taker_b","taker_q","ignore"]
    df = pd.DataFrame(raw, columns=cols)
    if df.empty:
        return pd.DataFrame()
    df["close"]  = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    df["date"]   = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df[["date","close","volume"]]

def _klines_coingecko(cg_id:str, days=365):
    # market_chart geeft [timestamp, price] en total_volumes
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

def _funding_rate_binance(pair:str):
    try:
        data = get(f"{BINANCE_FUT}/fapi/v1/fundingRate", params={"symbol":pair, "limit": 100})
        df = pd.DataFrame(data)
        if df.empty:
            return None, None
        df["fundingRate"] = df["fundingRate"].astype(float)
        df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
        last = df.iloc[-1]
        rate = float(last["fundingRate"])
        age_hours = (datetime.now(timezone.utc) - last["fundingTime"]).total_seconds()/3600
        return rate, age_hours
    except Exception:
        return None, None

def _age_hours(ts):
    return (datetime.now(timezone.utc) - ts).total_seconds()/3600

def _time_weight(age_hours: float) -> float:
    if age_hours is None:      return 1.0
    if age_hours < 24:         return 3.0
    if age_hours < 24*3:       return 2.0
    if age_hours < 24*7:       return 1.0
    return 0.5

def ta_indicators(symbol:str, cg_id:str):
    """
    Probeert eerst Binance candles (symbol+'USDT'), anders CoinGecko market_chart.
    Indicatoren: ma_crossover, volume_trend, funding_rate
    Retourneert (scores, ages, weights)
    """
    pair = f"{symbol}USDT"
    scores, ages, w = {}, {}, {}

    # ---- Klines: Binance -> CoinGecko fallback
    df = pd.DataFrame()
    try:
        df = _klines_binance(pair, "1d", 250)
    except Exception:
        df = pd.DataFrame()
    if df.empty:
        try:
            df = _klines_coingecko(cg_id, 365)
        except Exception:
            df = pd.DataFrame()

    if not df.empty:
        last_ts = df["date"].iloc[-1]
        age = _age_hours(last_ts)

        # MA50 vs MA200
        ma50  = df["close"].tail(50).mean()
        ma200 = df["close"].tail(200).mean() if len(df) >= 200 else df["close"].mean()
        sc = 1 if ma50 > ma200 else (-1 if ma50 < ma200 else 0)
        scores["ma_crossover"] = sc
        ages["ma_crossover"]   = age
        w["ma_crossover"]      = _time_weight(age)

        # Volume 7d vs 30d (±5% drempel)
        v7  = df["volume"].tail(7).mean()
        v30 = df["volume"].tail(30).mean() if len(df) >= 30 else df["volume"].mean()
        if v7 > 1.05 * v30:     sc = 1
        elif v7 < 0.95 * v30:   sc = -1
        else:                   sc = 0
        scores["volume_trend"] = sc
        ages["volume_trend"]   = age
        w["volume_trend"]      = _time_weight(age)
    else:
        scores["ma_crossover"] = 0
        scores["volume_trend"] = 0
        ages["ma_crossover"] = ages["volume_trend"] = None
        w["ma_crossover"] = w["volume_trend"] = 1.0

    # ---- Funding (alleen als Binance futures beschikbaar)
    rate, f_age = _funding_rate_binance(pair)
    if rate is None:
        sc = 0
    else:
        if rate >  0.0002: sc = -1
        elif rate < -0.0002: sc = 1
        else: sc = 0
    scores["funding_rate"] = sc
    ages["funding_rate"]   = f_age
    w["funding_rate"]      = _time_weight(f_age if f_age is not None else 24.0)

    return scores, ages, w

def weighted_group_score(scores: dict, weights: dict) -> float:
    """Gemiddelde van indicator-scores (-1..+1) gewogen met tijdsgewicht."""
    num = sum(scores[k]*weights.get(k,1.0) for k in scores)
    den = sum(weights.get(k,1.0) for k in scores)
    return 0.0 if den == 0 else num/den
