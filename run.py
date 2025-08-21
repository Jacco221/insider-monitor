from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

from src.universe import get_top_coins
from src.ta import ta_indicators, weighted_group_score

def pct_from_group(avg_m1_to_p1: float) -> float:
    """Zet -1..+1 om naar 0..100%."""
    return max(0.0, min(100.0, (avg_m1_to_p1 + 1.0) * 50.0))

def main():
    coins = get_top_coins(limit=100)   # [{'id','symbol','name','pair'}, ...]
    rows = []

    for c in coins:
        scores, ages, w = ta_indicators(c["pair"])
        ta_avg = weighted_group_score(scores, w)
        ta_pct = pct_from_group(ta_avg)

        # Voor nu: on-chain & macro/sentiment nog neutraal (50%)
        onchain_pct = 50.0
        macro_pct   = 50.0

        total = 0.4*onchain_pct + 0.4*ta_pct + 0.2*macro_pct
        avg_age = pd.Series([a for a in ages.values() if a is not None]).mean()
        rows.append({
            "symbol": c["symbol"],
            "name": c["name"],
            "pair": c["pair"],
            "ta_ma": scores.get("ma_crossover"),
            "ta_volume": scores.get("volume_trend"),
            "ta_funding": scores.get("funding_rate"),
            "TA_%": round(ta_pct,1),
            "OnChain_%": round(onchain_pct,1),
            "Macro_%": round(macro_pct,1),
            "Total_%": round(total,1),
            "AvgDataAge_hours": round(float(avg_age) if pd.notna(avg_age) else 0.0,1)
        })

    df = pd.DataFrame(rows).sort_values("Total_%", ascending=False)

    # Output
    outdir = Path("data/reports"); outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir/"latest.csv", index=False)
    df.to_json(outdir/"latest.json", orient="records")

    # Top 5 rapport (console)
    print("Top 5 kansrijke munten (voorlopig: TA-groep + neutrale on-chain & macro):")
    print(df[["symbol","Total_%","TA_%","OnChain_%","Macro_%","AvgDataAge_hours"]].head(5).to_string(index=False))

if __name__ == "__main__":
    main()
