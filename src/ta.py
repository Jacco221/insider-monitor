from src.utils import get
import pandas as pd
from datetime import datetime, timezone

BINANCE_SPOT = "https://api.binance.com"
BINANCE_FUT  = "https://fapi.binance.com"

def _klines(pair:str, interval="1d", limit=250):
    raw = get(f"{BINANCE_SPOT}/api/v3/klines", params={"symbol":pair, "interval":interval, "limit":limit})
    cols = ["open_time","open","high","low","close","volume","close_time","qav","trades","taker_b","taker_q","ignore"]
    df = pd.DataFrame(raw, columns=cols)
    if df.empty: 
        return df
    df["close"]  = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    df["date"]   = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df[["date","close","volume"]]

def _funding_rate(pair:str):
    # Neem laatste funding uit futures
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

def _age_hours(ts):
    return (datetime.now(timezone.utc) - ts).total_seconds()/3600

def _time_weight(age_hours: float) -> float:
    if age_hours is None:      return 1.0
    if age_hours < 24:         return 3.0
    if age_hours < 24*3:       return 2.0
    if age_hours < 24*7:       return 1.0
    return 0.5

def ta_indicators(pair:str):
    """
    Retourneert:
      scores: dict met -1/0/+1
      ages:   leeftijd in uren per indicator
      weights: toegepaste tijdsgewichten per indicator
    """
    out_scores, out_ages, out_w = {}, {}, {}

    # ---- Klines voor MA/Volume
    k = _klines(pair, "1d", 250)
    if not k.empty:
        last_ts = k["date"].iloc[-1]
        age = _age_hours(last_ts)

        # MA50 vs MA200
        ma50  = k["close"].tail(50).mean()
        ma200 = k["close"].tail(200).mean() if len(k) >= 200 else k["close"].mean()
        sc = 1 if ma50 > ma200 else (-1 if ma50 < ma200 else 0)
        out_scores["ma_crossover"] = sc
        out_ages["ma_crossover"]   = age
        out_w["ma_crossover"]      = _time_weight(age)

        # Volume 7d vs 30d (±5% drempel)
        v7  = k["volume"].tail(7).mean()
        v30 = k["volume"].tail(30).mean() if len(k) >= 30 else k["volume"].mean()
        if v7 > 1.05 * v30:     sc = 1
        elif v7 < 0.95 * v30:   sc = -1
        else:                   sc = 0
        out_scores["volume_trend"] = sc
        out_ages["volume_trend"]   = age
        out_w["volume_trend"]      = _time_weight(age)
    else:
        out_scores["ma_crossover"] = 0
        out_scores["volume_trend"] = 0
        out_ages["ma_crossover"] = out_ages["volume_trend"] = None
        out_w["ma_crossover"] = out_w["volume_trend"] = 1.0

    # ---- Funding
    rate, f_age = _funding_rate(pair)
    if rate is None:
        sc = 0
    else:
        # contrarian: te positieve funding is vaak oververhit
        if rate >  0.0002: sc = -1
        elif rate < -0.0002: sc = 1
        else: sc = 0
    out_scores["funding_rate"] = sc
    out_ages["funding_rate"]   = f_age
    out_w["funding_rate"]      = _time_weight(f_age if f_age is not None else 24.0)

    return out_scores, out_ages, out_w

def weighted_group_score(scores: dict, weights: dict) -> float:
    """Gemiddelde van indicator-scores (-1..+1) gewogen met tijdsgewicht."""
    num = sum(scores[k]*weights.get(k,1.0) for k in scores)
    den = sum(weights.get(k,1.0) for k in scores)
    return 0.0 if den == 0 else num/den
