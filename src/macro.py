# src/macro.py
import time
from io import StringIO
import pandas as pd
import requests

def _fetch_stooq_csv() -> pd.DataFrame | None:
    # Probeer meerdere symbolen (Stooq varieert)
    urls = [
        "https://stooq.com/q/d/l/?s=usdidx&i=d",
        "https://stooq.com/q/d/l/?s=dxy&i=d",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200 and "Date,Open,High,Low,Close,Volume" in r.text:
                df = pd.read_csv(StringIO(r.text))
                # parse & sort
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.sort_values("Date").reset_index(drop=True)
                return df
        except Exception:
            pass
    return None

def dxy_indicator() -> tuple[int, dict, dict]:
    """
    Bepaal DXY-trend met SMA10 vs SMA30.
    - DXY omlaag (SMA10 < SMA30) = bullish voor crypto => +1
    - DXY omhoog (SMA10 > SMA30) = bearish => -1
    - anders 0
    Return: (score, ages, weights)
    """
    df = _fetch_stooq_csv()
    if df is None or len(df) < 40:
        # geen betrouwbare data -> neutraal, geen leeftijd
        return 0, {"dxy": None}, {"dxy": 1.0}

    df["SMA10"] = df["Close"].rolling(10).mean()
    df["SMA30"] = df["Close"].rolling(30).mean()
    last = df.dropna().iloc[-1]
    score = 0
    if last["SMA10"] < last["SMA30"]:
        score = +1
    elif last["SMA10"] > last["SMA30"]:
        score = -1

    # leeftijd in uren (vandaag 00:00 ~ dagdata)
    last_ts = pd.Timestamp(last["Date"]).to_pydatetime().timestamp()
    age_hours = max(0.0, (time.time() - last_ts) / 3600.0)

    return score, {"dxy": age_hours}, {"dxy": 1.0}
